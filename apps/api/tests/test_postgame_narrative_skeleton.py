"""Tests for the deterministic Korean narrative skeleton."""

from __future__ import annotations

from app.postgame.narrative.skeleton import build_skeleton
from app.postgame.narrative.types import (
    NarrativeDifference,
    NarrativeFacts,
    NarrativePlayerLine,
)


def _facts(**kw: object) -> NarrativeFacts:
    base: dict[str, object] = {
        "gap_label": "acceptable",
        "pregame_actual_score": 3.90,
        "pregame_recommended_score": 4.10,
        "overperformers": (),
        "underperformers": (),
        "difference_reviews": (),
    }
    base.update(kw)
    return NarrativeFacts(**base)  # type: ignore[arg-type]


def test_skeleton_is_korean_and_mentions_scores() -> None:
    text = build_skeleton(_facts())
    assert "3.90" in text and "4.10" in text
    assert "무난한" in text  # acceptable -> Korean gap label


def test_skeleton_names_top_overperformer() -> None:
    facts = _facts(
        overperformers=(
            NarrativePlayerLine(
                name="김선수", performance_score=5.0, label="Overperformed", box_line={}
            ),
            NarrativePlayerLine(
                name="최선수", performance_score=8.0, label="Overperformed", box_line={}
            ),
        )
    )
    text = build_skeleton(facts)
    # Highest score is picked deterministically regardless of input order.
    assert "최선수" in text
    assert "8.0" in text


def test_skeleton_summarizes_difference_outcome() -> None:
    facts = _facts(
        difference_reviews=(
            NarrativeDifference(
                batting_order=2,
                actual_name="A",
                recommended_name="B",
                verdict="Actual choice succeeded",
                actual_performance=5.0,
                recommended_performance=1.0,
            ),
            NarrativeDifference(
                batting_order=5,
                actual_name="C",
                recommended_name="D",
                verdict="Actual choice succeeded",
                actual_performance=4.0,
                recommended_performance=0.0,
            ),
        )
    )
    text = build_skeleton(facts)
    assert "2곳" in text


def test_skeleton_is_deterministic() -> None:
    facts = _facts(
        overperformers=(
            NarrativePlayerLine(
                name="김선수", performance_score=5.0, label="Overperformed", box_line={}
            ),
        )
    )
    assert build_skeleton(facts) == build_skeleton(facts)
