"""Service that ties lineup scoring back to the database.

Reads stat/lineup snapshots for an evaluation run, delegates to the
pure scoring functions, and persists the recommended lineup plus a
summary.  The caller is responsible for committing the transaction.

Opponent starter handedness is derived from the game's announced starter
(captured by the preview/lineup normalizer) and falls back to RIGHT only when
no starter is known.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.lineup_model.batting_order.orderer import order as order_batting_lineup
from app.lineup_model.batting_order.provider import build_provider
from app.lineup_model.lineup_score import compute_lineup_score
from app.lineup_model.player_score import compute_player_score
from app.lineup_model.recommendation import select_and_assign_positions
from app.lineup_model.types import (
    Handedness,
    HitterStats,
    LineupScoreBreakdown,
    LineupSlot,
    Position,
)
from app.models.evaluation import LineupEvaluationRun, LineupEvaluationSummary, RecommendedLineupRow
from app.models.game import Game
from app.models.player import Player
from app.models.snapshot import (
    ActualLineupSnapshot,
    ActualLineupSnapshotRow,
    PlayerStatSnapshotRow,
)

# Number of most-recent games considered when deriving position eligibility and
# start rhythm from announced-lineup history.  Bounds the lookback so a position
# played once months ago no longer counts as "recent", and caps the per-game
# row queries (avoids scanning the whole season).
_LINEUP_HISTORY_WINDOW = 20  # recent games considered for position eligibility


def build_hitter_stats(
    player_id: int,
    stats_json: dict[str, object],
    player_position: str | None = None,
) -> HitterStats:
    """Construct HitterStats from a PlayerStatSnapshotRow.stats_json blob.

    Fields that may be absent in the fixture are given safe defaults.
    Position eligibility and handedness are derived from stats_json when
    present, otherwise sensible defaults are applied.

    Args:
        player_id: Database player id.
        stats_json: Flexible stats blob from PlayerStatSnapshotRow.

    Returns:
        HitterStats with fallbacks for all optional fields.
    """

    def _float(key: str, default: float = 0.0) -> float:
        v = stats_json.get(key)
        if v is None:
            return default
        # stats_json values come from JSON deserialization; numeric fields
        # must be int or float at runtime.  Use an explicit raise so the
        # check still runs under `python -O` (assert is stripped).
        if not isinstance(v, (int, float)):
            raise TypeError(
                f"stats_json[{key!r}] must be numeric for player_id={player_id}, "
                f"got {type(v).__name__}"
            )
        return float(v)

    def _int(key: str, default: int = 0) -> int:
        v = stats_json.get(key)
        if v is None:
            return default
        if not isinstance(v, (int, float)):
            raise TypeError(
                f"stats_json[{key!r}] must be numeric for player_id={player_id}, "
                f"got {type(v).__name__}"
            )
        return int(v)

    def _opt_float(key: str) -> float | None:
        v = stats_json.get(key)
        if v is None:
            return None
        if not isinstance(v, (int, float)):
            raise TypeError(
                f"stats_json[{key!r}] must be numeric for player_id={player_id}, "
                f"got {type(v).__name__}"
            )
        return float(v)

    # Handedness: default RIGHT when absent
    raw_hand = stats_json.get("handedness", "R")
    try:
        handedness = Handedness(str(raw_hand))
    except ValueError:
        handedness = Handedness.RIGHT

    # Primary position: prefer stats_json, fall back to the Player.position
    # column, then DH if neither is a valid Position value.
    raw_pos = stats_json.get("primary_position") or player_position or "DH"
    try:
        primary_position = Position(str(raw_pos))
    except ValueError:
        primary_position = Position.DH

    # Secondary positions
    sec_raw = stats_json.get("secondary_positions", [])
    secondary_positions: tuple[Position, ...] = ()
    if isinstance(sec_raw, list):
        parsed = []
        for p in sec_raw:
            try:
                parsed.append(Position(str(p)))
            except ValueError:
                pass
        secondary_positions = tuple(parsed)

    # Recent positions
    rec_raw = stats_json.get("recent_positions", [])
    recent_positions: tuple[Position, ...] = ()
    if isinstance(rec_raw, list):
        parsed_r = []
        for p in rec_raw:
            try:
                parsed_r.append(Position(str(p)))
            except ValueError:
                pass
        recent_positions = tuple(parsed_r)

    return HitterStats(
        player_id=player_id,
        handedness=handedness,
        ops=_float("OPS"),
        obp=_float("OBP"),
        slg=_float("SLG"),
        woba=_opt_float("woba"),
        wrc_plus=_opt_float("wrc_plus"),
        recent_14d_ops=_opt_float("recent_14d_ops"),
        recent_30d_ops=_opt_float("recent_30d_ops"),
        vs_rhp_ops=_opt_float("vs_rhp_ops"),
        vs_rhp_pa=_int("vs_rhp_pa"),
        vs_lhp_ops=_opt_float("vs_lhp_ops"),
        vs_lhp_pa=_int("vs_lhp_pa"),
        primary_position=primary_position,
        secondary_positions=secondary_positions,
        recent_positions=recent_positions,
        starts_last_5_games=_int("starts_last_5_games"),
    )


def compute_actual_lineup_score(
    session: Session,
    run: LineupEvaluationRun,
    opp_handedness: Handedness,
) -> float:
    """Compute the model score for the actual lineup that was played.

    Runs the actual_lineup_snapshot rows through compute_lineup_score so the
    result is on the same numeric scale as the recommended lineup score (which
    is also produced by compute_lineup_score and stored in
    LineupEvaluationSummary.key_insights_json['recommended_total_score']).

    To ensure every actual-lineup slot is scoreable we synthesise the slot's
    position into each player's secondary_positions tuple when not already
    present in primary / secondary / recent.  Without the synthetic addition,
    compute_player_score would return None for the slot and contribute 0 to
    the average, deflating the score asymmetrically.

    This helper is shared by the postgame review service so the actual score
    written to key_insights_json at evaluation time matches what the postgame
    pipeline expects.

    Args:
        session: SQLAlchemy session.
        run: LineupEvaluationRun to score.
        opp_handedness: Opposing starter's handedness.

    Returns:
        Total actual lineup score, or 0.0 when no scoreable slots exist.
    """
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

    # Build base stats for every actual-lineup player, then enrich with the same
    # lineup history used for the recommended candidates so both sides of the
    # recommended-vs-actual comparison are scored symmetrically (otherwise the
    # actual players would keep starts_last_5_games=0, pinning start_rhythm to
    # its 0.60 floor and systematically under-scoring the actual lineup). The
    # current game is excluded so it cannot count toward its own history.
    recent_lineups = _load_recent_lineups(
        session, run.team_id, run.evaluation_cutoff_at, exclude_game_id=run.game_id
    )
    base_stats_by_player: dict[int, HitterStats] = {}
    for row in actual_rows:
        if row.batting_order is None:
            continue
        if row.player_id in base_stats_by_player:
            continue
        player = session.get(Player, row.player_id)
        player_pos = player.position if player is not None else None
        base_stats_by_player[row.player_id] = build_hitter_stats(
            row.player_id, stats_json_by_player.get(row.player_id, {}), player_pos
        )
    enriched_stats = _enrich_with_lineup_history(
        list(base_stats_by_player.values()), recent_lineups
    )
    enriched_by_player = {stats.player_id: stats for stats in enriched_stats}

    slots: list[LineupSlot] = []
    stats_by_player: dict[int, HitterStats] = {}
    for row in actual_rows:
        if row.batting_order is None:
            continue
        try:
            pos = Position(row.position)
        except ValueError:
            pos = Position.DH

        base_stats = enriched_by_player[row.player_id]
        # Enrichment runs first; the played-slot position is then synthesised
        # into secondary_positions only when it is not already covered by the
        # primary / secondary / (now enriched) recent positions.
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

    breakdown = compute_lineup_score(tuple(slots), stats_by_player, opp_handedness)
    return breakdown.total_score


def _lineup_output_hash(breakdown: LineupScoreBreakdown) -> str:
    """Produce a stable SHA-256 fingerprint of the recommended lineup.

    Args:
        breakdown: Completed LineupScoreBreakdown.

    Returns:
        64-character hex digest.
    """
    payload = {
        "slots": [
            {
                "batting_order": s.batting_order,
                "player_id": s.player_id,
                "position": str(s.position),
            }
            for s in sorted(breakdown.slots, key=lambda x: x.batting_order)
        ],
        "total_score": breakdown.total_score,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


_THROWS_TO_HANDEDNESS: dict[str, Handedness] = {
    "R": Handedness.RIGHT,
    "L": Handedness.LEFT,
    "S": Handedness.SWITCH,
}


def _resolve_opp_handedness(session: Session, run: LineupEvaluationRun) -> tuple[Handedness, str]:
    """Resolve opponent handedness from the game's announced starter.

    Returns (handedness, source) where source is "announced_starter" when the
    game carries the opposing starter's throwing hand, else "default" (RIGHT).
    """
    game = session.get(Game, run.game_id)
    throws = game.opponent_starter_throws if game is not None else None
    hand = _THROWS_TO_HANDEDNESS.get(throws) if throws else None
    if hand is not None:
        return hand, "announced_starter"
    return Handedness.RIGHT, "default"


def _load_recent_lineups(
    session: Session,
    team_id: int,
    before_at: datetime,
    exclude_game_id: int | None = None,
) -> list[dict[int, str]]:
    """Return the team's recent announced lineups, most-recent game first.

    Returns at most ``_LINEUP_HISTORY_WINDOW`` most-recent games; this window is
    what "recently" means for position eligibility, so a position played only in
    older games no longer counts. Dedupes multiple snapshots of the same game
    (tentative → final) to the latest announced. Each element maps player_id →
    raw position string.

    ``before_at`` bounds the window to lineups announced before the run cutoff.
    The current game's lineup is typically announced *before* that cutoff, so
    pass ``exclude_game_id=run.game_id`` to keep today's game out of its own
    history (otherwise it would inflate starts_last_5_games and leak the current
    slot into recent_positions). The loop stops once the window is filled, which
    also avoids the per-game row query for older games. Ordering is fully
    deterministic via ``(announced_at desc, game_id desc)`` so games sharing an
    identical ``announced_at`` keep a stable order.
    """
    conditions = [
        ActualLineupSnapshot.team_id == team_id,
        ActualLineupSnapshot.announced_at < before_at,
    ]
    if exclude_game_id is not None:
        conditions.append(ActualLineupSnapshot.game_id != exclude_game_id)
    snapshots = (
        session.execute(
            select(ActualLineupSnapshot)
            .where(*conditions)
            .order_by(
                ActualLineupSnapshot.announced_at.desc(),
                ActualLineupSnapshot.game_id.desc(),
            )
        )
        .scalars()
        .all()
    )
    lineups: list[dict[int, str]] = []
    seen_games: set[int] = set()
    for snap in snapshots:
        if snap.game_id in seen_games:
            continue
        seen_games.add(snap.game_id)
        rows = (
            session.execute(
                select(ActualLineupSnapshotRow).where(
                    ActualLineupSnapshotRow.snapshot_id == snap.id
                )
            )
            .scalars()
            .all()
        )
        lineups.append({row.player_id: row.position for row in rows})
        if len(lineups) == _LINEUP_HISTORY_WINDOW:
            break
    return lineups


def _enrich_with_lineup_history(
    eligible: list[HitterStats], lineups: list[dict[int, str]]
) -> list[HitterStats]:
    """Inject recent_positions and starts_last_5_games from lineup history.

    recent_positions = distinct positions the player actually played recently,
    excluding their primary and existing secondary positions. starts_last_5 =
    count of the last 5 games whose lineup included the player.
    """
    positions_by_player: dict[int, set[str]] = {}
    for lineup in lineups:
        for player_id, position in lineup.items():
            positions_by_player.setdefault(player_id, set()).add(position)
    last_five = lineups[:5]

    enriched: list[HitterStats] = []
    for stats in eligible:
        recents: list[Position] = []
        for raw_pos in positions_by_player.get(stats.player_id, set()):
            try:
                pos = Position(raw_pos)
            except ValueError:
                continue
            if pos == stats.primary_position or pos in stats.secondary_positions:
                continue
            recents.append(pos)
        starts = sum(1 for lineup in last_five if stats.player_id in lineup)
        enriched.append(
            stats.model_copy(
                update={
                    "recent_positions": tuple(sorted(recents, key=str)),
                    "starts_last_5_games": starts,
                }
            )
        )
    return enriched


def _persist_start_rhythm(
    rows_by_player: dict[int, PlayerStatSnapshotRow], enriched: list[HitterStats]
) -> None:
    """Write each hitter's derived start rhythm into its stat row's stats_json.

    ``starts_last_5_games`` is computed at evaluation time from lineup history,
    not by the ingestion normalizer, so it is absent from the persisted
    stats_json that read-time views (the pregame player comparison) consume.
    Surfacing it here lets the comparison panel display the real value instead
    of the default 0. The JSON dict is reassigned so SQLAlchemy detects the
    change; existing keys are preserved. Hitters without a matching stat row
    (e.g. not in this snapshot) are skipped.
    """
    for stats in enriched:
        row = rows_by_player.get(stats.player_id)
        if row is None:
            continue
        row.stats_json = {**row.stats_json, "starts_last_5_games": stats.starts_last_5_games}


def evaluate_lineup_for_run(
    session: Session,
    *,
    run: LineupEvaluationRun,
    opp_handedness: Handedness | None = None,
) -> LineupEvaluationRun:
    """Compute scores and persist the recommended lineup + summary for the run.

    Reads:
    - run.stat_snapshot_id → player_stat_snapshot_rows → player stats
    - run.lineup_snapshot_id → actual_lineup_snapshot_rows → actual lineup
    - run.team_id (used to filter stat snapshot rows to team players)

    Writes:
    - recommended_lineup_rows (one per recommended slot, 9 total)
    - lineup_evaluation_summaries (one row with summary_text + key_insights_json)
    - Updates run.status from 'pending' to 'completed', sets output_hash
      and finished_at.

    The caller commits the transaction.

    Args:
        session: SQLAlchemy session.
        run: LineupEvaluationRun row to process (must have status 'pending').
        opp_handedness: Opposing starter's handedness.  Defaults to RIGHT
            (MVP limitation — should be derived from game-day pitching data
            in a future iteration).

    Returns:
        The updated LineupEvaluationRun.
    """
    # ------------------------------------------------------------------
    # 0. Idempotency guard — bail out if this run is already completed.
    #    No DB-level UNIQUE protects against duplicate recommended rows
    #    or summaries, so we rely on the status flag.
    # ------------------------------------------------------------------
    if run.status == "completed":
        return run

    # Resolve opponent handedness: explicit arg wins; otherwise derive it from
    # the game's announced starter, falling back to RIGHT when unknown.
    if opp_handedness is None:
        opp_handedness, opp_handedness_source = _resolve_opp_handedness(session, run)
    else:
        opp_handedness_source = "explicit"

    # ------------------------------------------------------------------
    # 1. Load stat snapshot rows for team players
    # ------------------------------------------------------------------
    stat_rows = session.execute(
        select(PlayerStatSnapshotRow, Player)
        .join(Player, Player.id == PlayerStatSnapshotRow.player_id)
        .where(
            PlayerStatSnapshotRow.snapshot_id == run.stat_snapshot_id,
            Player.team_id == run.team_id,
        )
    ).all()

    eligible: list[HitterStats] = []
    for stat_row, player in stat_rows:
        stats = build_hitter_stats(player.id, stat_row.stats_json, player.position)
        eligible.append(stats)

    # Enrich eligible hitters with position eligibility and start rhythm derived
    # from the team's recently announced lineups (activates position_fit /
    # start_rhythm scoring). The current game is excluded so it cannot count
    # toward its own history. No-op when no prior lineups exist.
    recent_lineups = _load_recent_lineups(
        session, run.team_id, run.evaluation_cutoff_at, exclude_game_id=run.game_id
    )
    eligible = _enrich_with_lineup_history(eligible, recent_lineups)
    # Surface the derived start rhythm in the persisted stat rows so read-time
    # views (the pregame player comparison) display it instead of a default 0.
    _persist_start_rhythm({sr.player_id: sr for sr, _ in stat_rows}, eligible)

    # ------------------------------------------------------------------
    # 2. Load actual lineup rows for comparison
    # ------------------------------------------------------------------
    actual_rows = (
        session.execute(
            select(ActualLineupSnapshotRow).where(
                ActualLineupSnapshotRow.snapshot_id == run.lineup_snapshot_id
            )
        )
        .scalars()
        .all()
    )

    actual_player_ids = {row.player_id for row in actual_rows}

    # ------------------------------------------------------------------
    # 3. Run recommendation (pure)
    # ------------------------------------------------------------------
    assigned = select_and_assign_positions(eligible, opp_handedness)
    provider = build_provider()
    order_result = order_batting_lineup(assigned, opp_handedness, provider)
    stats_by_player = {s.player_id: s for s in eligible}
    recommended = compute_lineup_score(order_result.slots, stats_by_player, opp_handedness)

    # ------------------------------------------------------------------
    # 4. Persist recommended_lineup_rows
    # ------------------------------------------------------------------
    for slot in sorted(recommended.slots, key=lambda s: s.batting_order):
        stats = stats_by_player[slot.player_id]
        breakdown = compute_player_score(stats, slot.position, opp_handedness)
        rationale = order_result.rationale_ko_by_player.get(slot.player_id, "")

        session.add(
            RecommendedLineupRow(
                evaluation_run_id=run.id,
                player_id=slot.player_id,
                batting_order=slot.batting_order,
                position=str(slot.position),
                score=breakdown.total_score if breakdown is not None else None,
                rationale=rationale,
            )
        )

    # ------------------------------------------------------------------
    # 5. Build key_insights_json (score gap between actual and recommended)
    # ------------------------------------------------------------------
    recommended_ids = {slot.player_id for slot in recommended.slots}
    additions = sorted(recommended_ids - actual_player_ids)
    removals = sorted(actual_player_ids - recommended_ids)

    # Compute the actual lineup score once at evaluation time so postgame
    # reviews and other consumers can read it from key_insights_json without
    # having to recompute it on every GET.
    actual_total_score = compute_actual_lineup_score(session, run, opp_handedness)

    key_insights: dict[str, object] = {
        "recommended_total_score": recommended.total_score,
        "actual_total_score": actual_total_score,
        "weighted_player_score": recommended.weighted_player_score,
        "position_completeness_adjustment": recommended.position_completeness_adjustment,
        "handedness_balance_adjustment": recommended.handedness_balance_adjustment,
        "opp_handedness_default": str(opp_handedness),
        "opp_handedness_source": opp_handedness_source,
        "players_added_vs_actual": additions,
        "players_removed_vs_actual": removals,
        "lineup": [
            {
                "batting_order": slot.batting_order,
                "player_id": slot.player_id,
                "position": str(slot.position),
            }
            for slot in sorted(recommended.slots, key=lambda s: s.batting_order)
        ],
    }
    # Surface the handedness limitation only when we actually fell back to the
    # RIGHT default (no announced starter); a derived hand is not a limitation.
    if opp_handedness_source == "default":
        key_insights["opp_handedness_note"] = (
            "Defaulted to RIGHT for MVP; derive from actual starter data in future."
        )

    summary_text = order_result.summary_ko

    session.add(
        LineupEvaluationSummary(
            evaluation_run_id=run.id,
            summary_text=summary_text,
            key_insights_json=key_insights,
        )
    )

    # ------------------------------------------------------------------
    # 6. Update run status
    # ------------------------------------------------------------------
    run.status = "completed"
    run.output_hash = _lineup_output_hash(recommended)
    run.finished_at = datetime.now(UTC)
    model_config: dict[str, object] = {"batting_order_source": order_result.source}
    if order_result.source == "llm":
        model_config["llm_model"] = os.environ.get("LINEUP_LLM_MODEL", "gpt-4.1")
    run.model_config_json = model_config

    session.flush()
    return run
