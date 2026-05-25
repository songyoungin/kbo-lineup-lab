"""Service layer that assembles pregame view payloads from DB queries.

All functions read from the database and return Pydantic response models.
Route handlers must stay thin and call these functions directly.
"""

from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.lineup_model.lineup_score import compute_lineup_score
from app.lineup_model.types import Handedness, HitterStats, LineupSlot, Position
from app.models.evaluation import LineupEvaluationRun, LineupEvaluationSummary, RecommendedLineupRow
from app.models.game import Game
from app.models.player import Player
from app.models.snapshot import ActualLineupSnapshotRow, PlayerStatSnapshotRow
from app.models.team import Team
from app.schemas.pregame import (
    DifferenceTypeLiteral,
    LineupComparisonResponse,
    LineupComparisonRow,
    LineupDifference,
    LineupRow,
    PlayerComparisonResponse,
    PlayerComparisonStats,
    PregameResponse,
    ReplayEvaluationRequest,
    ReplayEvaluationResponse,
    TeamHomeGameCard,
    TeamHomeResponse,
    derive_verdict,
)
from app.services.evaluation_runs import get_or_create_evaluation_run
from app.services.lineup_evaluator import build_hitter_stats, evaluate_lineup_for_run
from app.services.snapshot_selector import (
    SnapshotNotFoundError,
    select_lineup_snapshot,
    select_stat_snapshot,
)

# Factors the current model does not capture; returned verbatim to the frontend.
_UNMODELED_FACTORS: list[str] = [
    "Defense and baserunning value",
    "Injury / fatigue / rest days",
    "Manager matchup tendencies",
    "Platoon or late-inning substitution plans",
]

# Note surfaced in pregame model_limitations explaining the actual-lineup score
# method (see _compute_actual_lineup_score docstring).
ACTUAL_SCORE_METHOD_NOTE: str = (
    "Actual lineup score is computed by feeding the announced lineup through "
    "compute_lineup_score with each slot's position synthesised into the "
    "player's secondary_positions if not already present, so every slot is "
    "scoreable. This keeps the actual and recommended scores on the same scale."
)


def _lookup_team_id(session: Session, team_code: str) -> int:
    """Return the primary key for a team identified by code.

    Raises:
        HTTPException: 404 when the team code is not found.
    """
    team = session.execute(select(Team).where(Team.code == team_code)).scalars().first()
    if team is None:
        raise HTTPException(status_code=404, detail=f"Team '{team_code}' not found")
    return team.id


def _latest_completed_run(
    session: Session, game_id: int, team_id: int
) -> LineupEvaluationRun | None:
    """Return the most recently completed evaluation run for a game+team pair."""
    return (
        session.execute(
            select(LineupEvaluationRun)
            .where(
                LineupEvaluationRun.game_id == game_id,
                LineupEvaluationRun.team_id == team_id,
                LineupEvaluationRun.status == "completed",
            )
            .order_by(LineupEvaluationRun.finished_at.desc())
            .limit(1)
        )
        .scalars()
        .first()
    )


def _player_name(session: Session, player_id: int) -> str:
    """Fetch the name of a player by primary key."""
    player = session.get(Player, player_id)
    return player.name if player is not None else f"Player({player_id})"


def _player_names_bulk(session: Session, player_ids: list[int]) -> dict[int, str]:
    """Return a player_id → name mapping for a list of IDs."""
    if not player_ids:
        return {}
    players = session.execute(select(Player).where(Player.id.in_(player_ids))).scalars().all()
    return {p.id: p.name for p in players}


# ---------------------------------------------------------------------------
# Team home view
# ---------------------------------------------------------------------------


def build_team_home(session: Session, team_code: str) -> TeamHomeResponse:
    """Assemble the team home page payload for the given team.

    For MVP with fixture data:
    - "today" is the single game present in the fixture.
    - "recent" is an empty list (no historical game records yet).
    - Pipeline status is derived from the presence of evaluation run data.

    Args:
        session: SQLAlchemy session.
        team_code: KBO team code (e.g. "LG").

    Returns:
        TeamHomeResponse payload.

    Raises:
        HTTPException: 404 when the team is not found.
    """
    team_id = _lookup_team_id(session, team_code)

    # Find the most recent game for this team (home or away)
    game = (
        session.execute(
            select(Game)
            .where((Game.home_team_id == team_id) | (Game.away_team_id == team_id))
            .order_by(Game.game_date.desc())
            .limit(1)
        )
        .scalars()
        .first()
    )

    today_card: TeamHomeGameCard | None = None
    if game is not None:
        is_home = game.home_team_id == team_id
        opp_team_id = game.away_team_id if is_home else game.home_team_id
        opp_team = session.get(Team, opp_team_id)
        opp_code = opp_team.code if opp_team is not None else "???"

        # Derive pipeline status from existence of evaluation run
        completed_run = _latest_completed_run(session, game.id, team_id)
        pipeline_status: dict[str, str] = {
            "schedule": "ok",
            "lineup": "ok",
            "eval": "ok" if completed_run is not None else "missing",
            "box": "missing",
            "postgame": "missing",
        }

        today_card = TeamHomeGameCard(
            game_id=game.id,
            game_date=game.game_date,
            opponent_team_code=opp_code,
            venue=game.venue,
            # Opponent starter is not seeded in the fixture; always None for MVP
            opponent_starter=None,
            pipeline_status=pipeline_status,
        )

    return TeamHomeResponse(
        team_code=team_code,
        today=today_card,
        recent=[],  # No historical game records for MVP
    )


# ---------------------------------------------------------------------------
# Pregame evaluation view
# ---------------------------------------------------------------------------


def _compute_actual_lineup_score(
    session: Session,
    run: LineupEvaluationRun,
) -> float:
    """Compute the model score for the actual lineup that was played.

    Runs the actual_lineup_snapshot rows through compute_lineup_score so the
    result is on the same numeric scale as the recommended lineup score
    (which is also produced by compute_lineup_score and stored in
    LineupEvaluationSummary.key_insights_json['recommended_total_score']).

    Score parity matters for the verdict bands: without it, the actual score
    is the raw per-player mean while the recommended score includes batting-
    order weights, the position-completeness bonus, and the handedness-
    balance penalty (±2), making the gap meaningless across lineups with
    different handedness composition.

    To ensure every actual-lineup slot is scoreable we synthesise the slot's
    position into each player's secondary_positions tuple. Real player-game
    position data may include slots a player has never played as primary /
    secondary / recent (e.g. fixture-derived primary_position=DH but the
    player actually started in CF on game day). Without the synthetic
    addition, compute_player_score would return None for the slot and
    contribute 0 to the average, deflating the score asymmetrically. This
    workaround is documented in the run's key_insights via
    actual_score_method_note.
    """
    # Load actual lineup rows ordered by batting_order
    actual_rows = (
        session.execute(
            select(ActualLineupSnapshotRow)
            .where(ActualLineupSnapshotRow.snapshot_id == run.lineup_snapshot_id)
            .order_by(ActualLineupSnapshotRow.batting_order)
        )
        .scalars()
        .all()
    )
    if not actual_rows:
        return 0.0

    # Load stat rows keyed by player_id
    stat_rows = (
        session.execute(
            select(PlayerStatSnapshotRow).where(
                PlayerStatSnapshotRow.snapshot_id == run.stat_snapshot_id
            )
        )
        .scalars()
        .all()
    )
    stats_json_by_player: dict[int, dict[str, object]] = {
        r.player_id: r.stats_json for r in stat_rows
    }

    # Build a slot tuple and a stats map, synthesising the slot position into
    # each player's secondary_positions so the slot is always scoreable.
    slots: list[LineupSlot] = []
    stats_by_player: dict[int, HitterStats] = {}
    for row in actual_rows:
        if row.batting_order is None:
            # Skip rows without a batting order — they cannot map to a slot
            continue
        try:
            pos = Position(row.position)
        except ValueError:
            pos = Position.DH

        player = session.get(Player, row.player_id)
        player_pos = player.position if player is not None else None
        base_stats = build_hitter_stats(
            row.player_id, stats_json_by_player.get(row.player_id, {}), player_pos
        )
        # Guarantee the actual slot is in the player's eligibility tuple so
        # compute_player_score returns a non-None breakdown. We add it as a
        # secondary position when it's not already present in primary /
        # secondary / recent — this keeps "primary" tier scoring intact while
        # ensuring scorability.
        if (
            pos != base_stats.primary_position
            and pos not in base_stats.secondary_positions
            and pos not in base_stats.recent_positions
        ):
            adjusted_stats = base_stats.model_copy(
                update={"secondary_positions": (*base_stats.secondary_positions, pos)}
            )
        else:
            adjusted_stats = base_stats
        stats_by_player[row.player_id] = adjusted_stats
        slots.append(
            LineupSlot(
                batting_order=row.batting_order,
                player_id=row.player_id,
                position=pos,
            )
        )

    if not slots:
        return 0.0

    breakdown = compute_lineup_score(tuple(slots), stats_by_player, Handedness.RIGHT)
    return breakdown.total_score


def build_pregame_view(
    session: Session,
    game_id: int,
    *,
    team_id: int | None = None,
) -> PregameResponse:
    """Assemble the pregame evaluation view.

    Args:
        session: SQLAlchemy session.
        game_id: Game to look up.
        team_id: Team to evaluate; defaults to LG when None.

    Returns:
        PregameResponse payload.

    Raises:
        HTTPException: 404 when no evaluation run or game is found.
    """
    if team_id is None:
        team_id = _lookup_team_id(session, "LG")

    # Ensure game exists
    game = session.get(Game, game_id)
    if game is None:
        raise HTTPException(status_code=404, detail=f"Game {game_id} not found")

    run = _latest_completed_run(session, game_id, team_id)
    if run is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"No completed evaluation run for game_id={game_id} team_id={team_id}. "
                "Trigger one via POST /api/jobs/replay-evaluation first."
            ),
        )

    # Fetch summary for insights
    summary = (
        session.execute(
            select(LineupEvaluationSummary).where(
                LineupEvaluationSummary.evaluation_run_id == run.id
            )
        )
        .scalars()
        .first()
    )

    insights: dict[str, object] = (
        summary.key_insights_json
        if summary is not None and summary.key_insights_json is not None
        else {}
    )
    _rec_raw = insights.get("recommended_total_score", 0.0)
    recommended_score: float = float(_rec_raw) if isinstance(_rec_raw, (int, float)) else 0.0

    # Compute actual lineup score (score of the players/positions actually played)
    actual_score = _compute_actual_lineup_score(session, run)

    score_gap = actual_score - recommended_score
    verdict = derive_verdict(score_gap)

    # Build actual lineup rows
    actual_snapshot_rows = (
        session.execute(
            select(ActualLineupSnapshotRow).where(
                ActualLineupSnapshotRow.snapshot_id == run.lineup_snapshot_id
            )
        )
        .scalars()
        .all()
    )
    actual_player_ids = [r.player_id for r in actual_snapshot_rows]

    # Build recommended lineup rows
    rec_rows = (
        session.execute(
            select(RecommendedLineupRow)
            .where(RecommendedLineupRow.evaluation_run_id == run.id)
            .order_by(RecommendedLineupRow.batting_order)
        )
        .scalars()
        .all()
    )
    rec_player_ids = [r.player_id for r in rec_rows]

    all_ids = list(set(actual_player_ids + rec_player_ids))
    name_map = _player_names_bulk(session, all_ids)

    actual_lineup = [
        LineupRow(
            batting_order=r.batting_order if r.batting_order is not None else 0,
            position=r.position,
            player_id=r.player_id,
            player_name=name_map.get(r.player_id, f"Player({r.player_id})"),
        )
        for r in sorted(actual_snapshot_rows, key=lambda x: x.batting_order or 0)
    ]

    recommended_lineup = [
        LineupRow(
            batting_order=r.batting_order if r.batting_order is not None else 0,
            position=r.position,
            player_id=r.player_id,
            player_name=name_map.get(r.player_id, f"Player({r.player_id})"),
        )
        for r in rec_rows
    ]

    # Build differences between actual and recommended at each batting order slot
    actual_by_order: dict[int, ActualLineupSnapshotRow] = {
        r.batting_order: r for r in actual_snapshot_rows if r.batting_order is not None
    }
    rec_by_order: dict[int, RecommendedLineupRow] = {
        r.batting_order: r for r in rec_rows if r.batting_order is not None
    }
    rec_order_by_player: dict[int, int] = {
        r.player_id: r.batting_order for r in rec_rows if r.batting_order is not None
    }

    differences: list[LineupDifference] = []
    for order in sorted(set(list(actual_by_order.keys()) + list(rec_by_order.keys()))):
        a = actual_by_order.get(order)
        r = rec_by_order.get(order)
        if a is None or r is None:
            continue
        same_player = a.player_id == r.player_id
        same_position = a.position == r.position
        actual_player_appears_in_rec_at_other_slot = (
            not same_player
            and a.player_id in rec_order_by_player
            and rec_order_by_player[a.player_id] != order
        )
        if same_player and same_position:
            diff_type: DifferenceTypeLiteral = "Same"
            reason = "Actual matches recommendation"
        elif same_player:
            diff_type = "Position changed"
            reason = f"Same player, position differs: actual={a.position} recommended={r.position}"
        elif actual_player_appears_in_rec_at_other_slot:
            other_slot = rec_order_by_player[a.player_id]
            actual_name = name_map.get(a.player_id, str(a.player_id))
            diff_type = "Batting order changed"
            reason = (
                f"{actual_name} batted at slot {order} but the model recommends slot {other_slot}"
            )
        elif same_position:
            diff_type = "Player changed"
            reason = (
                f"Different player: actual={name_map.get(a.player_id, str(a.player_id))} "
                f"recommended={name_map.get(r.player_id, str(r.player_id))}"
            )
        else:
            diff_type = "Player and order changed"
            reason = (
                f"Different player and position: "
                f"actual={name_map.get(a.player_id, str(a.player_id))} {a.position} "
                f"vs recommended={name_map.get(r.player_id, str(r.player_id))} {r.position}"
            )
        differences.append(
            LineupDifference(batting_order=order, difference_type=diff_type, main_reason=reason)
        )

    # Extract model limitations from key_insights
    model_limitations: list[str] = []
    opp_note = insights.get("opp_handedness_note")
    if isinstance(opp_note, str):
        model_limitations.append(opp_note)
    opp_default = insights.get("opp_handedness_default")
    if isinstance(opp_default, str):
        model_limitations.append(f"Opponent handedness defaulted to {opp_default}")
    # Surface the actual-score method note so frontend / consumers know the
    # actual lineup is scored with synthesised position eligibility.
    model_limitations.append(ACTUAL_SCORE_METHOD_NOTE)

    return PregameResponse(
        game_id=game_id,
        actual_score=actual_score,
        recommended_score=recommended_score,
        score_gap=score_gap,
        verdict=verdict,
        actual_lineup=actual_lineup,
        recommended_lineup=recommended_lineup,
        differences=differences,
        model_limitations=model_limitations,
    )


# ---------------------------------------------------------------------------
# Lineup comparison view
# ---------------------------------------------------------------------------


def build_lineup_comparison(
    session: Session,
    game_id: int,
    *,
    team_id: int | None = None,
) -> LineupComparisonResponse:
    """Assemble the per-slot lineup comparison view.

    Args:
        session: SQLAlchemy session.
        game_id: Game to compare.
        team_id: Team to compare; defaults to LG.

    Returns:
        LineupComparisonResponse with 9 rows.

    Raises:
        HTTPException: 404 when no evaluation run or game is found.
    """
    if team_id is None:
        team_id = _lookup_team_id(session, "LG")

    game = session.get(Game, game_id)
    if game is None:
        raise HTTPException(status_code=404, detail=f"Game {game_id} not found")

    run = _latest_completed_run(session, game_id, team_id)
    if run is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"No completed evaluation run for game_id={game_id} team_id={team_id}. "
                "Trigger one via POST /api/jobs/replay-evaluation first."
            ),
        )

    actual_rows = (
        session.execute(
            select(ActualLineupSnapshotRow).where(
                ActualLineupSnapshotRow.snapshot_id == run.lineup_snapshot_id
            )
        )
        .scalars()
        .all()
    )
    rec_rows = (
        session.execute(
            select(RecommendedLineupRow)
            .where(RecommendedLineupRow.evaluation_run_id == run.id)
            .order_by(RecommendedLineupRow.batting_order)
        )
        .scalars()
        .all()
    )

    actual_by_order: dict[int, ActualLineupSnapshotRow] = {
        r.batting_order: r for r in actual_rows if r.batting_order is not None
    }
    rec_by_order: dict[int, RecommendedLineupRow] = {
        r.batting_order: r for r in rec_rows if r.batting_order is not None
    }
    # Reverse index: player_id → recommended batting_order. Used to detect
    # "Batting order changed" when the actual player at slot N appears in the
    # recommended lineup at a different slot M.
    rec_order_by_player: dict[int, int] = {
        r.player_id: r.batting_order for r in rec_rows if r.batting_order is not None
    }

    all_ids = [r.player_id for r in actual_rows] + [r.player_id for r in rec_rows]
    name_map = _player_names_bulk(session, list(set(all_ids)))

    comparison_rows: list[LineupComparisonRow] = []
    for order in sorted(set(list(actual_by_order.keys()) + list(rec_by_order.keys()))):
        a = actual_by_order.get(order)
        r = rec_by_order.get(order)
        if a is None or r is None:
            continue

        same_player = a.player_id == r.player_id
        same_position = a.position == r.position
        actual_player_appears_in_rec_at_other_slot = (
            not same_player
            and a.player_id in rec_order_by_player
            and rec_order_by_player[a.player_id] != order
        )

        diff_type: DifferenceTypeLiteral
        reason: str
        if same_player and same_position:
            diff_type = "Same"
            reason = "Actual matches recommendation"
        elif same_player:
            diff_type = "Position changed"
            reason = f"Same player, position differs: actual={a.position} recommended={r.position}"
        elif actual_player_appears_in_rec_at_other_slot:
            # Same player exists in the recommended lineup but at a different
            # batting order — classify as "Batting order changed" so the
            # frontend can highlight the manager's batting-order choice.
            other_slot = rec_order_by_player[a.player_id]
            actual_name = name_map.get(a.player_id, str(a.player_id))
            diff_type = "Batting order changed"
            reason = (
                f"{actual_name} batted at slot {order} but the model recommends slot {other_slot}"
            )
        elif same_position:
            diff_type = "Player changed"
            reason = (
                f"Different player at slot {order}: "
                f"actual={name_map.get(a.player_id, str(a.player_id))} "
                f"vs recommended={name_map.get(r.player_id, str(r.player_id))}"
            )
        else:
            diff_type = "Player and order changed"
            reason = (
                f"Different player+position: "
                f"actual={name_map.get(a.player_id, str(a.player_id))} {a.position} "
                f"vs recommended={name_map.get(r.player_id, str(r.player_id))} {r.position}"
            )

        comparison_rows.append(
            LineupComparisonRow(
                batting_order=order,
                actual_player_id=a.player_id,
                actual_player_name=name_map.get(a.player_id, f"Player({a.player_id})"),
                actual_position=a.position,
                recommended_player_id=r.player_id,
                recommended_player_name=name_map.get(r.player_id, f"Player({r.player_id})"),
                recommended_position=r.position,
                difference_type=diff_type,
                main_reason=reason,
            )
        )

    return LineupComparisonResponse(game_id=game_id, rows=comparison_rows)


# ---------------------------------------------------------------------------
# Player comparison view
# ---------------------------------------------------------------------------


def build_player_comparison(
    session: Session,
    game_id: int,
    batting_order: int,
    *,
    team_id: int | None = None,
) -> PlayerComparisonResponse:
    """Assemble the head-to-head player comparison for a specific batting order slot.

    Args:
        session: SQLAlchemy session.
        game_id: Game to compare.
        batting_order: Slot number (1–9) to compare.
        team_id: Team to compare; defaults to LG.

    Returns:
        PlayerComparisonResponse with stats for both players.

    Raises:
        HTTPException: 404 when game, evaluation run, or batting-order slot is missing.
    """
    if team_id is None:
        team_id = _lookup_team_id(session, "LG")

    game = session.get(Game, game_id)
    if game is None:
        raise HTTPException(status_code=404, detail=f"Game {game_id} not found")

    run = _latest_completed_run(session, game_id, team_id)
    if run is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"No completed evaluation run for game_id={game_id} team_id={team_id}. "
                "Trigger one via POST /api/jobs/replay-evaluation first."
            ),
        )

    # Actual player at this batting order
    actual_slot = (
        session.execute(
            select(ActualLineupSnapshotRow).where(
                ActualLineupSnapshotRow.snapshot_id == run.lineup_snapshot_id,
                ActualLineupSnapshotRow.batting_order == batting_order,
            )
        )
        .scalars()
        .first()
    )
    if actual_slot is None:
        raise HTTPException(
            status_code=404,
            detail=f"No actual lineup slot at batting_order={batting_order} for game {game_id}",
        )

    # Recommended player at this batting order
    rec_slot = (
        session.execute(
            select(RecommendedLineupRow).where(
                RecommendedLineupRow.evaluation_run_id == run.id,
                RecommendedLineupRow.batting_order == batting_order,
            )
        )
        .scalars()
        .first()
    )
    if rec_slot is None:
        raise HTTPException(
            status_code=404,
            detail=f"No recommended slot at batting_order={batting_order} for run {run.id}",
        )

    # Load stat rows for both players
    stat_rows = (
        session.execute(
            select(PlayerStatSnapshotRow).where(
                PlayerStatSnapshotRow.snapshot_id == run.stat_snapshot_id,
                PlayerStatSnapshotRow.player_id.in_([actual_slot.player_id, rec_slot.player_id]),
            )
        )
        .scalars()
        .all()
    )
    stats_map: dict[int, dict[str, object]] = {r.player_id: r.stats_json for r in stat_rows}

    name_map = _player_names_bulk(session, [actual_slot.player_id, rec_slot.player_id])

    def _build_comparison_stats(
        player_id: int,
        position: str,
        slot_score: float | None,
        stats_json: dict[str, object],
    ) -> PlayerComparisonStats:
        def _f(key: str) -> float:
            v = stats_json.get(key, 0.0)
            return float(v) if isinstance(v, (int, float)) else 0.0

        def _opt_f(key: str) -> float | None:
            v = stats_json.get(key)
            if v is None:
                return None
            return float(v) if isinstance(v, (int, float)) else None

        def _i(key: str) -> int:
            v = stats_json.get(key, 0)
            return int(v) if isinstance(v, (int, float)) else 0

        return PlayerComparisonStats(
            player_id=player_id,
            player_name=name_map.get(player_id, f"Player({player_id})"),
            position=position,
            ops=_f("OPS"),
            obp=_f("OBP"),
            slg=_f("SLG"),
            recent_14d_ops=_opt_f("recent_14d_ops"),
            recent_30d_ops=_opt_f("recent_30d_ops"),
            vs_rhp_ops=_opt_f("vs_rhp_ops"),
            vs_lhp_ops=_opt_f("vs_lhp_ops"),
            pa_vs_rhp=_i("vs_rhp_pa"),
            pa_vs_lhp=_i("vs_lhp_pa"),
            starts_last_5=_i("starts_last_5_games"),
            model_score=slot_score,
        )

    actual_stats = _build_comparison_stats(
        actual_slot.player_id,
        actual_slot.position,
        None,  # actual lineup doesn't carry a model score
        stats_map.get(actual_slot.player_id, {}),
    )
    rec_stats = _build_comparison_stats(
        rec_slot.player_id,
        rec_slot.position,
        rec_slot.score,
        stats_map.get(rec_slot.player_id, {}),
    )

    # Simple judgment: compare OPS if both have it; otherwise check model score
    if rec_stats.model_score is not None and actual_stats.ops < rec_stats.ops:
        judgment = (
            f"Model favours {rec_stats.player_name} "
            f"(OPS {rec_stats.ops:.3f} vs {actual_stats.ops:.3f})"
        )
    elif actual_stats.ops >= rec_stats.ops:
        judgment = (
            f"Actual player {actual_stats.player_name} has equal or better OPS "
            f"({actual_stats.ops:.3f} vs {rec_stats.ops:.3f})"
        )
    else:
        judgment = "Model scores favour recommended player"

    return PlayerComparisonResponse(
        batting_order=batting_order,
        actual=actual_stats,
        recommended=rec_stats,
        judgment=judgment,
        unmodeled_factors=_UNMODELED_FACTORS,
    )


# ---------------------------------------------------------------------------
# Replay evaluation job
# ---------------------------------------------------------------------------


def replay_evaluation(
    session: Session,
    *,
    request: ReplayEvaluationRequest,
) -> ReplayEvaluationResponse:
    """Trigger or retrieve a lineup evaluation run.

    1. Selects cutoff-safe stat and lineup snapshots.
    2. Calls get_or_create_evaluation_run (idempotent).
    3. If the run is not yet completed, calls evaluate_lineup_for_run.
    4. Commits and returns run metadata.

    Args:
        session: SQLAlchemy session.
        request: ReplayEvaluationRequest with game/team/cutoff/model_version.

    Returns:
        ReplayEvaluationResponse indicating run id, whether it was created, and status.

    Raises:
        HTTPException: 404 when snapshots cannot be found.
        HTTPException: 422 when evaluation_cutoff_at is naive (propagated from to_utc).
    """
    try:
        stat_snapshot = select_stat_snapshot(
            session,
            team_id=request.team_id,
            cutoff_at=request.evaluation_cutoff_at,
        )
        lineup_snapshot = select_lineup_snapshot(
            session,
            game_id=request.game_id,
            team_id=request.team_id,
            cutoff_at=request.evaluation_cutoff_at,
        )
    except SnapshotNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    run, created = get_or_create_evaluation_run(
        session,
        game_id=request.game_id,
        team_id=request.team_id,
        evaluation_cutoff_at=request.evaluation_cutoff_at,
        stat_snapshot_id=stat_snapshot.id,
        lineup_snapshot_id=lineup_snapshot.id,
        model_version_id=request.model_version_id,
    )

    # evaluate_lineup_for_run is idempotent when status='completed'
    evaluate_lineup_for_run(session, run=run)
    session.commit()

    return ReplayEvaluationResponse(
        evaluation_run_id=run.id,
        created=created,
        status=run.status,
    )
