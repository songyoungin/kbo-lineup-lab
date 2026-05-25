"""Unit tests for postgame review computation.

Tests cover:
- Performance score formula (each weight key)
- Classification thresholds (boundary values)
- generate_postgame_review determinism and eval_run reference invariant
- Summary text selection covers all four narrative templates
"""

from __future__ import annotations

from app.postgame.performance_score import (
    OVERPERFORM_THRESHOLD,
    UNDERPERFORM_THRESHOLD,
    classify_performance,
    compute_performance_score,
)
from app.postgame.review_generator import (
    ActualLineupRow,
    BoxLineEntry,
    RecommendedRow,
    generate_postgame_review,
)
from app.postgame.types import PerformanceLabel, PostgameReviewBreakdown

# ---------------------------------------------------------------------------
# Helper to build a box_line dict from keyword args
# ---------------------------------------------------------------------------


def _line(
    hits: int = 0,
    runs: int = 0,
    rbis: int = 0,
    at_bats: int = 4,
    bb: int = 0,
    hbp: int = 0,
    so: int = 0,
    gidp: int = 0,
    doubles: int = 0,
    triples: int = 0,
    hr: int = 0,
) -> dict[str, object]:
    extra: dict[str, object] = {}
    if bb:
        extra["BB"] = bb
    if hbp:
        extra["HBP"] = hbp
    if so:
        extra["SO"] = so
    if gidp:
        extra["GIDP"] = gidp
    if doubles:
        extra["2B"] = doubles
    if triples:
        extra["3B"] = triples
    if hr:
        extra["HR"] = hr
    return {
        "at_bats": at_bats,
        "hits": hits,
        "runs": runs,
        "rbis": rbis,
        "extra_stats_json": extra,
    }


# ---------------------------------------------------------------------------
# Performance score formula — each weight
# ---------------------------------------------------------------------------


def test_single_weight() -> None:
    """A single should contribute +1.0 to the score."""
    box = _line(hits=1, runs=0, rbis=0)
    assert compute_performance_score(box) == 1.0


def test_double_weight() -> None:
    """A double should contribute +2.0 to the score."""
    box = _line(hits=1, doubles=1)  # hits=1, 2B=1 → singles=0
    assert compute_performance_score(box) == 2.0


def test_triple_weight() -> None:
    """A triple should contribute +3.0."""
    box = _line(hits=1, triples=1)
    assert compute_performance_score(box) == 3.0


def test_home_run_weight() -> None:
    """A home run should contribute +4.0."""
    box = _line(hits=1, hr=1)
    assert compute_performance_score(box) == 4.0


def test_walk_weight() -> None:
    """A walk should contribute +1.0."""
    box = _line(hits=0, bb=1)
    assert compute_performance_score(box) == 1.0


def test_hbp_weight() -> None:
    """An HBP should contribute +1.0 (combined with BB as walk_hbp)."""
    box = _line(hits=0, hbp=1)
    assert compute_performance_score(box) == 1.0


def test_run_weight() -> None:
    """A run scored should contribute +1.0."""
    box = _line(hits=0, runs=1)
    assert compute_performance_score(box) == 1.0


def test_rbi_weight() -> None:
    """An RBI should contribute +1.0."""
    box = _line(hits=0, rbis=1)
    assert compute_performance_score(box) == 1.0


def test_strikeout_weight() -> None:
    """A strikeout should contribute -0.5."""
    box = _line(hits=0, so=1)
    assert compute_performance_score(box) == -0.5


def test_gidp_weight() -> None:
    """A GIDP should contribute -1.5."""
    box = _line(hits=0, gidp=1)
    assert compute_performance_score(box) == -1.5


def test_singles_inferred_correctly() -> None:
    """Singles = hits - 2B - 3B - HR, clamped at 0."""
    # 2 hits: 1 double + 1 home run → 0 singles
    box = _line(hits=2, doubles=1, hr=1)
    # score = 0 singles×1 + 1 double×2 + 1 HR×4 = 6.0
    assert compute_performance_score(box) == 6.0


def test_singles_clamped_at_zero() -> None:
    """If extra hits exceed total hits (data error), singles should not go negative."""
    # More extra bases than total hits — singles should be clamped at 0
    box = _line(hits=1, doubles=1, hr=1)
    # hits=1, 2B=1, HR=1 → raw singles = 1-1-1 = -1 → clamped to 0
    # score = 0 singles + 1 double×2 + 1 HR×4 = 6.0
    assert compute_performance_score(box) == 6.0


def test_combined_line_score() -> None:
    """Verify a complex box line matches the expected weighted sum."""
    # 2 hits (1 single + 1 HR), 1 BB, 2 runs, 3 RBIs, 1 SO
    box = _line(hits=2, hr=1, bb=1, runs=2, rbis=3, so=1)
    # singles=1, HR=1, BB=1, runs=2, rbis=3, SO=1
    # = 1×1 + 1×4 + 1×1 + 2×1 + 3×1 - 1×0.5 = 1+4+1+2+3-0.5 = 10.5
    assert compute_performance_score(box) == 10.5


def test_empty_box_line_score_is_zero() -> None:
    """An empty box line produces a score of 0.0."""
    box: dict[str, object] = {}
    assert compute_performance_score(box) == 0.0


def test_missing_extra_stats_defaults_to_zero() -> None:
    """Missing extra_stats_json key is treated as empty dict (all 0)."""
    box: dict[str, object] = {"hits": 1, "runs": 0, "rbis": 0}
    assert compute_performance_score(box) == 1.0  # 1 single


# ---------------------------------------------------------------------------
# Classification thresholds
# ---------------------------------------------------------------------------


def test_overperform_threshold_boundary_above() -> None:
    """Score at exactly OVERPERFORM_THRESHOLD is classified as Overperformed."""
    assert classify_performance(OVERPERFORM_THRESHOLD) == PerformanceLabel.OVERPERFORMED


def test_overperform_threshold_boundary_below() -> None:
    """Score just below OVERPERFORM_THRESHOLD is classified as Expected."""
    assert classify_performance(OVERPERFORM_THRESHOLD - 0.001) == PerformanceLabel.EXPECTED


def test_underperform_threshold_boundary_at() -> None:
    """Score at exactly UNDERPERFORM_THRESHOLD is classified as Underperformed."""
    assert classify_performance(UNDERPERFORM_THRESHOLD) == PerformanceLabel.UNDERPERFORMED


def test_underperform_threshold_boundary_above() -> None:
    """Score just above UNDERPERFORM_THRESHOLD is classified as Expected."""
    assert classify_performance(UNDERPERFORM_THRESHOLD + 0.001) == PerformanceLabel.EXPECTED


def test_expected_mid_range() -> None:
    """Score of 2.0 (between thresholds) is Expected."""
    assert classify_performance(2.0) == PerformanceLabel.EXPECTED


def test_positive_high_is_overperformed() -> None:
    """Score of 10.0 is Overperformed."""
    assert classify_performance(10.0) == PerformanceLabel.OVERPERFORMED


def test_negative_deep_is_underperformed() -> None:
    """Score of -5.0 is Underperformed."""
    assert classify_performance(-5.0) == PerformanceLabel.UNDERPERFORMED


# ---------------------------------------------------------------------------
# generate_postgame_review — determinism
# ---------------------------------------------------------------------------


def _make_review(eval_run_id: int = 1) -> PostgameReviewBreakdown:
    """Produce a PostgameReviewBreakdown from minimal fixture-like inputs."""
    actual_lineup = [
        ActualLineupRow(batting_order=1, player_id=101, position="CF"),
        ActualLineupRow(batting_order=2, player_id=102, position="SS"),
    ]
    recommended_lineup = [
        RecommendedRow(batting_order=1, player_id=103, position="CF"),  # differs
        RecommendedRow(batting_order=2, player_id=102, position="SS"),  # same
    ]
    box_score_rows = [
        BoxLineEntry(player_id=101, box_line=_line(hits=2, runs=1, rbis=1, so=0)),
        BoxLineEntry(player_id=102, box_line=_line(hits=0, runs=0, rbis=0, so=3)),
        BoxLineEntry(player_id=103, box_line=_line(hits=1, runs=0, rbis=1)),
    ]
    names = {101: "Player A", 102: "Player B", 103: "Player C"}

    return generate_postgame_review(
        evaluation_run_id=eval_run_id,
        box_score_snapshot_id=10,
        pregame_actual_score=0.75,
        pregame_recommended_score=0.80,
        actual_lineup=actual_lineup,
        recommended_lineup=recommended_lineup,
        box_score_rows=box_score_rows,
        player_names_by_id=names,
    )


def test_generate_review_is_deterministic() -> None:
    """Two calls with identical inputs must produce identical breakdowns."""
    r1 = _make_review()
    r2 = _make_review()
    assert r1 == r2


def test_generate_review_references_provided_eval_run_id() -> None:
    """The breakdown must carry exactly the evaluation_run_id passed in.

    This is the critical architectural invariant: postgame review must not
    recompute pregame scores from newer data.  The evaluation_run_id in the
    breakdown ties the result to the original pregame evaluation.
    """
    breakdown = _make_review(eval_run_id=42)
    assert breakdown.evaluation_run_id == 42


def test_generate_review_references_provided_scores_not_recomputed() -> None:
    """pregame_actual_score and pregame_recommended_score must match the inputs verbatim."""
    breakdown = _make_review()
    # The generator MUST NOT recompute these — it must pass them through as-is
    assert breakdown.pregame_actual_score == 0.75
    assert breakdown.pregame_recommended_score == 0.80


def test_generate_review_difference_reviews_only_for_mismatches() -> None:
    """DifferenceReview entries exist only for slots where actual != recommended."""
    breakdown = _make_review()
    # Slot 1: player 101 vs recommended 103 → difference review
    # Slot 2: player 102 == recommended 102 → no difference review
    assert len(breakdown.difference_reviews) == 1
    assert breakdown.difference_reviews[0].batting_order == 1


def test_generate_review_score_gap_computed_correctly() -> None:
    """pregame_score_gap must equal pregame_actual_score - pregame_recommended_score."""
    breakdown = _make_review()
    assert abs(breakdown.pregame_score_gap - (0.75 - 0.80)) < 1e-9


# ---------------------------------------------------------------------------
# Summary text template selection
# ---------------------------------------------------------------------------


def _review_with_gap_and_outcomes(
    gap: float,
    over_count: int,
    under_count: int,
    actual_choice_succeeded: bool,
) -> PostgameReviewBreakdown:
    """Produce a review designed to trigger a specific summary template."""
    # Build minimal lineup where batting order 1 differs so DifferenceReview is generated
    actual_lineup = [ActualLineupRow(batting_order=1, player_id=101, position="CF")]
    recommended_lineup = [RecommendedRow(batting_order=1, player_id=999, position="CF")]

    # Build box score entries to produce desired over/under counts
    box_lines: list[BoxLineEntry] = []

    # Player 101 (actual at slot 1)
    if over_count > 0:
        # Overperform: score >= 4.0 (e.g. 1 HR = 4 pts)
        box_lines.append(BoxLineEntry(player_id=101, box_line=_line(hits=1, hr=1)))
    elif under_count > 0:
        # Underperform: score <= -1.0
        box_lines.append(BoxLineEntry(player_id=101, box_line=_line(hits=0, so=3)))
    else:
        box_lines.append(BoxLineEntry(player_id=101, box_line=_line(hits=1)))

    # Recommended player 999 box line — if actual_choice_succeeded, actual > rec
    if actual_choice_succeeded:
        box_lines.append(BoxLineEntry(player_id=999, box_line=_line(hits=0)))
    else:
        box_lines.append(BoxLineEntry(player_id=999, box_line=_line(hits=2, runs=1, rbis=1)))

    actual_score = 0.75 + gap  # derive actual score from gap
    names = {101: "A", 999: "X"}

    return generate_postgame_review(
        evaluation_run_id=1,
        box_score_snapshot_id=10,
        pregame_actual_score=actual_score,
        pregame_recommended_score=0.75,
        actual_lineup=actual_lineup,
        recommended_lineup=recommended_lineup,
        box_score_rows=box_lines,
        player_names_by_id=names,
    )


def test_summary_template_weak_pregame_over() -> None:
    """Template 1: weak pregame → overperformers → 'weaker than rec, but exceeded'."""
    # Gap = -8 (questionable) and actual player overperformed (HR = 4 pts)
    breakdown = _review_with_gap_and_outcomes(
        gap=-8.0, over_count=1, under_count=0, actual_choice_succeeded=True
    )
    assert "weaker than the recommendation" in breakdown.summary_text
    assert "exceeded expectations" in breakdown.summary_text


def test_summary_template_weak_pregame_under() -> None:
    """Template 2: weak pregame → underperformers → 'model disliked... also underperformed'."""
    # Gap = -8 (questionable) and actual player underperformed (3 SO = -1.5 pts)
    breakdown = _review_with_gap_and_outcomes(
        gap=-8.0, over_count=0, under_count=1, actual_choice_succeeded=False
    )
    assert "model disliked" in breakdown.summary_text
    assert "underperformed" in breakdown.summary_text


def test_summary_template_actual_succeeded() -> None:
    """Template 3: actual diverged from model and succeeded."""
    # Gap near-optimal (-0.5) but the actual choice beat the rec player
    actual_lineup = [ActualLineupRow(batting_order=1, player_id=101, position="CF")]
    recommended_lineup = [RecommendedRow(batting_order=1, player_id=999, position="CF")]
    box_lines = [
        BoxLineEntry(player_id=101, box_line=_line(hits=1, hr=1)),  # 4.0 pts
        BoxLineEntry(player_id=999, box_line=_line(hits=0)),  # 0 pts
    ]
    breakdown = generate_postgame_review(
        evaluation_run_id=1,
        box_score_snapshot_id=10,
        pregame_actual_score=0.74,
        pregame_recommended_score=0.75,
        actual_lineup=actual_lineup,
        recommended_lineup=recommended_lineup,
        box_score_rows=box_lines,
        player_names_by_id={101: "A", 999: "X"},
    )
    assert "differed from the model and succeeded" in breakdown.summary_text


def test_summary_template_close_to_optimal() -> None:
    """Template 4: near-optimal gap + no strong difference verdict → 'close to optimal'."""
    # All players same, no difference reviews, near-optimal gap
    actual_lineup = [ActualLineupRow(batting_order=1, player_id=101, position="CF")]
    recommended_lineup = [RecommendedRow(batting_order=1, player_id=101, position="CF")]
    box_lines = [BoxLineEntry(player_id=101, box_line=_line(hits=1))]
    breakdown = generate_postgame_review(
        evaluation_run_id=1,
        box_score_snapshot_id=10,
        pregame_actual_score=0.80,
        pregame_recommended_score=0.80,
        actual_lineup=actual_lineup,
        recommended_lineup=recommended_lineup,
        box_score_rows=box_lines,
        player_names_by_id={101: "A"},
    )
    assert "close to optimal" in breakdown.summary_text


# ---------------------------------------------------------------------------
# Gap label selection
# ---------------------------------------------------------------------------


def test_gap_label_nearly_optimal() -> None:
    """Gap >= -2.0 → 'nearly optimal'."""
    from app.postgame.review_generator import _pick_gap_label

    assert _pick_gap_label(-1.0) == "nearly optimal"
    assert _pick_gap_label(-2.0) == "nearly optimal"
    assert _pick_gap_label(0.0) == "nearly optimal"


def test_gap_label_acceptable() -> None:
    """Gap in [-5.0, -2.0) → 'acceptable'."""
    from app.postgame.review_generator import _pick_gap_label

    assert _pick_gap_label(-3.0) == "acceptable"
    assert _pick_gap_label(-5.0) == "acceptable"


def test_gap_label_questionable() -> None:
    """Gap in [-10.0, -5.0) → 'questionable'."""
    from app.postgame.review_generator import _pick_gap_label

    assert _pick_gap_label(-7.0) == "questionable"
    assert _pick_gap_label(-10.0) == "questionable"


def test_gap_label_low_offensive_efficiency() -> None:
    """Gap < -10.0 → 'low offensive efficiency'."""
    from app.postgame.review_generator import _pick_gap_label

    assert _pick_gap_label(-11.0) == "low offensive efficiency"
    assert _pick_gap_label(-100.0) == "low offensive efficiency"
