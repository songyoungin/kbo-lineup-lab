"""Tests for _resolve_narrative: stored value passthrough vs skeleton fallback."""

from __future__ import annotations

from app.postgame.types import (
    PerformanceLabel,
    PlayerPerformance,
    PostgameReviewBreakdown,
)
from app.services.postgame_reviews import _resolve_narrative


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
                box_line={},
            ),
        ),
        underperformers=(),
        other_actual=(),
        difference_reviews=(),
        summary_text="old",
        key_insights_json={},
    )


def test_resolve_returns_stored_when_present() -> None:
    assert (
        _resolve_narrative("이미 저장된 이야기", _breakdown(), {10: "김선수"})
        == "이미 저장된 이야기"
    )


def test_resolve_builds_skeleton_when_null() -> None:
    out = _resolve_narrative(None, _breakdown(), {10: "김선수"})
    assert out.startswith("모델은 이날 실제 라인업을")


def test_resolve_builds_skeleton_when_empty_string() -> None:
    out = _resolve_narrative("", _breakdown(), {10: "김선수"})
    assert out.startswith("모델은 이날 실제 라인업을")
