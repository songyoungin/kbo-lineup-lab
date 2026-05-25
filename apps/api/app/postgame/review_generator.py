"""Pure orchestration of postgame review generation.

No DB access — the service layer fetches all inputs and passes plain objects.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import NamedTuple

from app.postgame.performance_score import classify_performance, compute_performance_score
from app.postgame.types import (
    DifferenceReview,
    PerformanceLabel,
    PlayerPerformance,
    PostgameReviewBreakdown,
)

# ---------------------------------------------------------------------------
# Gap label thresholds (pregame score gap = actual_score - recommended_score)
# Negative means actual lineup was weaker than recommendation.
# ---------------------------------------------------------------------------
_GAP_NEARLY_OPTIMAL = -2.0
_GAP_ACCEPTABLE = -5.0
_GAP_QUESTIONABLE = -10.0

# Minimum absolute performance score delta to declare "succeeded" vs "comparable"
_PERF_DELTA_THRESHOLD = 1.0


class ActualLineupRow(NamedTuple):
    """Minimal actual lineup row for generator input."""

    batting_order: int
    player_id: int
    position: str


class RecommendedRow(NamedTuple):
    """Minimal recommended lineup row for generator input."""

    batting_order: int
    player_id: int
    position: str


class BoxLineEntry(NamedTuple):
    """One player's box score data for generator input."""

    player_id: int
    box_line: dict[str, object]


def _pick_gap_label(gap: float) -> str:
    """Map the pregame score gap to a human-readable label.

    Args:
        gap: pregame_actual_score - pregame_recommended_score.

    Returns:
        Label string per design thresholds.
    """
    if gap >= _GAP_NEARLY_OPTIMAL:
        return "nearly optimal"
    if gap >= _GAP_ACCEPTABLE:
        return "acceptable"
    if gap >= _GAP_QUESTIONABLE:
        return "questionable"
    return "low offensive efficiency"


def _build_difference_review(
    batting_order: int,
    actual_player_id: int,
    recommended_player_id: int,
    actual_perf: float,
    recommended_perf: float | None,
    player_names: Mapping[int, str],
) -> DifferenceReview:
    """Construct one DifferenceReview entry for a mismatched batting slot.

    Args:
        batting_order: Slot number (1–9).
        actual_player_id: Player who actually batted at this slot.
        recommended_player_id: Player the model recommended for this slot.
        actual_perf: Box score performance score of the actual player.
        recommended_perf: Box score performance score of the recommended player,
            or None if the recommended player did not appear in the box score.
        player_names: Mapping from player_id to display name.

    Returns:
        DifferenceReview with a verdict and rationale.
    """
    actual_name = player_names.get(actual_player_id, f"Player({actual_player_id})")
    rec_name = player_names.get(recommended_player_id, f"Player({recommended_player_id})")

    if recommended_perf is None:
        # Recommended player did not appear in the box score — cannot compare
        if actual_perf >= 0:
            verdict = "Actual choice succeeded"
            rationale = (
                f"{actual_name} scored {actual_perf:.1f} pts; "
                f"{rec_name} (model's pick) did not appear in the box score."
            )
        else:
            verdict = "Inconclusive — model's pick absent from box score"
            rationale = (
                f"{actual_name} scored {actual_perf:.1f} pts; "
                f"{rec_name} (model's pick) did not appear in the box score."
            )
    else:
        delta = actual_perf - recommended_perf
        if delta >= _PERF_DELTA_THRESHOLD:
            verdict = "Actual choice succeeded"
            rationale = (
                f"{actual_name} outperformed {rec_name}: "
                f"{actual_perf:.1f} vs {recommended_perf:.1f} pts."
            )
        elif delta <= -_PERF_DELTA_THRESHOLD:
            verdict = "Model would have done better"
            rationale = (
                f"{rec_name} (model pick) would have outperformed {actual_name}: "
                f"{recommended_perf:.1f} vs {actual_perf:.1f} pts."
            )
        else:
            verdict = "Both comparable"
            rationale = (
                f"{actual_name} and {rec_name} performed similarly: "
                f"{actual_perf:.1f} vs {recommended_perf:.1f} pts."
            )

    return DifferenceReview(
        batting_order=batting_order,
        actual_player_id=actual_player_id,
        recommended_player_id=recommended_player_id,
        actual_performance=actual_perf,
        verdict=verdict,
        rationale=rationale,
    )


def _generate_summary_text(
    gap_label: str,
    overperformers: tuple[PlayerPerformance, ...],
    underperformers: tuple[PlayerPerformance, ...],
    difference_reviews: tuple[DifferenceReview, ...],
) -> str:
    """Generate a rule-based English narrative summary.

    Selects from four narrative templates based on the pregame gap label and
    the distribution of over/underperformers in the actual lineup.

    Args:
        gap_label: One of the four gap label strings.
        overperformers: Players who exceeded expectations.
        underperformers: Players who fell below expectations.
        difference_reviews: Slot-level verdicts for mismatched selections.

    Returns:
        Human-readable summary paragraph.
    """
    n_over = len(overperformers)
    n_under = len(underperformers)
    actual_succeeded = sum(
        1 for dr in difference_reviews if dr.verdict == "Actual choice succeeded"
    )
    model_better = sum(
        1 for dr in difference_reviews if dr.verdict == "Model would have done better"
    )

    # Template 1: weak pregame, strong actual result
    if gap_label in ("questionable", "low offensive efficiency") and n_over > n_under:
        return (
            "The actual lineup was weaker than the recommendation, "
            "but the selected players exceeded expectations."
        )

    # Template 2: weak pregame, also poor actual result
    if gap_label in ("questionable", "low offensive efficiency") and n_under >= n_over:
        return "The model disliked the choice before the game, and the result also underperformed."

    # Template 3: actual diverged from model and succeeded
    if actual_succeeded > model_better and len(difference_reviews) > 0:
        return "The actual choice differed from the model and succeeded."

    # Template 4: close to optimal, result within expectation
    return "The actual lineup was close to optimal and performed within expectation."


def generate_postgame_review(
    *,
    evaluation_run_id: int,
    box_score_snapshot_id: int,
    pregame_actual_score: float,
    pregame_recommended_score: float,
    actual_lineup: Sequence[ActualLineupRow],
    recommended_lineup: Sequence[RecommendedRow],
    box_score_rows: Sequence[BoxLineEntry],
    player_names_by_id: Mapping[int, str],
) -> PostgameReviewBreakdown:
    """Produce a deterministic postgame review from already-fetched inputs.

    No DB access — the service layer fetches everything and passes plain objects.
    The function references the original pregame_actual_score and
    pregame_recommended_score directly; it does NOT recompute them.

    Args:
        evaluation_run_id: PK of the LineupEvaluationRun this review is for.
        box_score_snapshot_id: PK of the BoxScoreSnapshot used.
        pregame_actual_score: Score of the actual lineup from the evaluation run.
        pregame_recommended_score: Score of the recommended lineup from the evaluation run.
        actual_lineup: Ordered actual lineup rows (batting_order, player_id, position).
        recommended_lineup: Ordered recommended lineup rows.
        box_score_rows: Per-player box score lines.
        player_names_by_id: player_id → display name.

    Returns:
        PostgameReviewBreakdown with all computed fields.
    """
    # Index box score by player_id for O(1) lookup
    box_by_player: dict[int, dict[str, object]] = {
        entry.player_id: entry.box_line for entry in box_score_rows
    }

    # Compute performance for each actual-lineup player
    perf_by_player: dict[int, float] = {}
    player_performances: list[PlayerPerformance] = []

    for slot in actual_lineup:
        box_line = box_by_player.get(slot.player_id, {})
        score = compute_performance_score(box_line)
        label = classify_performance(score)
        perf_by_player[slot.player_id] = score
        player_performances.append(
            PlayerPerformance(
                player_id=slot.player_id,
                performance_score=score,
                label=label,
                box_line=dict(box_line),
            )
        )

    overperformers = tuple(
        p for p in player_performances if p.label == PerformanceLabel.OVERPERFORMED
    )
    underperformers = tuple(
        p for p in player_performances if p.label == PerformanceLabel.UNDERPERFORMED
    )
    other_actual = tuple(p for p in player_performances if p.label == PerformanceLabel.EXPECTED)

    # Build recommended lineup index by batting_order
    rec_by_order: dict[int, RecommendedRow] = {row.batting_order: row for row in recommended_lineup}
    actual_by_order: dict[int, ActualLineupRow] = {row.batting_order: row for row in actual_lineup}

    # Build DifferenceReview for slots where actual player != recommended player
    difference_reviews: list[DifferenceReview] = []
    for order in sorted(set(list(actual_by_order.keys()) + list(rec_by_order.keys()))):
        actual_slot = actual_by_order.get(order)
        rec_slot = rec_by_order.get(order)
        if actual_slot is None or rec_slot is None:
            continue
        if actual_slot.player_id == rec_slot.player_id:
            continue  # same player — no difference review needed

        actual_perf = perf_by_player.get(actual_slot.player_id, 0.0)
        # Recommended player may or may not have appeared in the box score
        rec_perf: float | None = None
        if rec_slot.player_id in box_by_player:
            rec_perf = compute_performance_score(box_by_player[rec_slot.player_id])

        difference_reviews.append(
            _build_difference_review(
                batting_order=order,
                actual_player_id=actual_slot.player_id,
                recommended_player_id=rec_slot.player_id,
                actual_perf=actual_perf,
                recommended_perf=rec_perf,
                player_names=player_names_by_id,
            )
        )

    # Score gap and label
    pregame_score_gap = pregame_actual_score - pregame_recommended_score
    gap_label = _pick_gap_label(pregame_score_gap)

    summary_text = _generate_summary_text(
        gap_label,
        overperformers,
        underperformers,
        tuple(difference_reviews),
    )

    key_insights: dict[str, object] = {
        "evaluation_run_id": evaluation_run_id,
        "box_score_snapshot_id": box_score_snapshot_id,
        "pregame_actual_score": pregame_actual_score,
        "pregame_recommended_score": pregame_recommended_score,
        "pregame_score_gap": pregame_score_gap,
        "pregame_gap_label": gap_label,
        "overperformer_ids": [p.player_id for p in overperformers],
        "underperformer_ids": [p.player_id for p in underperformers],
        "difference_verdicts": [
            {
                "batting_order": dr.batting_order,
                "actual_player_id": dr.actual_player_id,
                "recommended_player_id": dr.recommended_player_id,
                "verdict": dr.verdict,
            }
            for dr in difference_reviews
        ],
    }

    return PostgameReviewBreakdown(
        evaluation_run_id=evaluation_run_id,
        box_score_snapshot_id=box_score_snapshot_id,
        pregame_actual_score=pregame_actual_score,
        pregame_recommended_score=pregame_recommended_score,
        pregame_score_gap=pregame_score_gap,
        pregame_gap_label=gap_label,
        overperformers=overperformers,
        underperformers=underperformers,
        other_actual=other_actual,
        difference_reviews=tuple(difference_reviews),
        summary_text=summary_text,
        key_insights_json=key_insights,
    )
