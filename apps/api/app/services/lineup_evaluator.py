"""Service that ties lineup scoring back to the database.

Reads stat/lineup snapshots for an evaluation run, delegates to the
pure scoring functions, and persists the recommended lineup plus a
summary.  The caller is responsible for committing the transaction.

Opponent starter handedness defaults to RIGHT for MVP.  This is a
known limitation — the actual starter's handedness should flow from a
future ingestion step that captures game-day pitching assignments.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.lineup_model.lineup_score import compute_lineup_score
from app.lineup_model.player_score import compute_player_score
from app.lineup_model.recommendation import generate_recommendation
from app.lineup_model.types import (
    Handedness,
    HitterStats,
    LineupScoreBreakdown,
    LineupSlot,
    Position,
)
from app.models.evaluation import LineupEvaluationRun, LineupEvaluationSummary, RecommendedLineupRow
from app.models.player import Player
from app.models.snapshot import ActualLineupSnapshotRow, PlayerStatSnapshotRow


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

    slots: list[LineupSlot] = []
    stats_by_player: dict[int, HitterStats] = {}
    for row in actual_rows:
        if row.batting_order is None:
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


def evaluate_lineup_for_run(
    session: Session,
    *,
    run: LineupEvaluationRun,
    opp_handedness: Handedness = Handedness.RIGHT,
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
    recommended = generate_recommendation(eligible, opp_handedness)

    # ------------------------------------------------------------------
    # 4. Persist recommended_lineup_rows
    # ------------------------------------------------------------------
    stats_by_player = {s.player_id: s for s in eligible}

    for slot in sorted(recommended.slots, key=lambda s: s.batting_order):
        stats = stats_by_player[slot.player_id]
        # Build a concise rationale string
        breakdown = compute_player_score(stats, slot.position, opp_handedness)
        rationale_parts = []
        if breakdown is not None:
            for r in breakdown.reasons:
                rationale_parts.append(f"{r.component}={r.value:.3f}(w={r.weight}): {r.note}")
        rationale = "; ".join(rationale_parts)

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
        "opp_handedness_note": (
            "Defaulted to RIGHT for MVP; derive from actual starter data in future."
        ),
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

    summary_text = (
        f"Recommended lineup score: {recommended.total_score:.4f}. "
        f"Weighted player average: {recommended.weighted_player_score:.4f}. "
        f"Position completeness: {recommended.position_completeness_adjustment:+.2f}. "
        f"Handedness balance: {recommended.handedness_balance_adjustment:+.2f}. "
        f"Players added vs actual: {additions}. "
        f"Players removed vs actual: {removals}."
    )

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

    session.flush()
    return run
