"""Tests for NarrativeFacts.from_breakdown projection."""

from __future__ import annotations

from app.postgame.narrative.types import NarrativeFacts
from app.postgame.types import (
    DifferenceReview,
    PerformanceLabel,
    PlayerPerformance,
    PostgameReviewBreakdown,
)


def _breakdown() -> PostgameReviewBreakdown:
    return PostgameReviewBreakdown(
        evaluation_run_id=1,
        box_score_snapshot_id=1,
        pregame_actual_score=3.9,
        pregame_recommended_score=4.1,
        pregame_score_gap=-0.2,
        pregame_gap_label="acceptable",
        overperformers=(
            PlayerPerformance(
                player_id=10,
                performance_score=5.0,
                label=PerformanceLabel.OVERPERFORMED,
                box_line={"H": 3},
            ),
        ),
        underperformers=(
            PlayerPerformance(
                player_id=20,
                performance_score=-1.0,
                label=PerformanceLabel.UNDERPERFORMED,
                box_line={"H": 0},
            ),
        ),
        other_actual=(),
        difference_reviews=(
            DifferenceReview(
                batting_order=3,
                actual_player_id=10,
                recommended_player_id=99,
                actual_performance=5.0,
                recommended_performance=2.0,
                verdict="Actual choice succeeded",
                rationale="x",
            ),
        ),
        summary_text="old english summary",
        key_insights_json={},
    )


def test_from_breakdown_maps_names_and_facts() -> None:
    facts = NarrativeFacts.from_breakdown(_breakdown(), {10: "김선수", 20: "이선수", 99: "박선수"})
    assert facts.gap_label == "acceptable"
    assert facts.pregame_actual_score == 3.9
    assert facts.overperformers[0].name == "김선수"
    assert facts.overperformers[0].performance_score == 5.0
    assert facts.underperformers[0].name == "이선수"
    assert facts.difference_reviews[0].actual_name == "김선수"
    assert facts.difference_reviews[0].recommended_name == "박선수"
    assert facts.difference_reviews[0].verdict == "Actual choice succeeded"


def test_from_breakdown_unknown_name_falls_back_to_placeholder() -> None:
    facts = NarrativeFacts.from_breakdown(_breakdown(), {})
    assert facts.overperformers[0].name == "Player(10)"
