"""Facts projection and provider interface for the postgame narrative layer."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from pydantic import BaseModel, ConfigDict

from app.postgame.types import PostgameReviewBreakdown


def _name(names: Mapping[int, str], player_id: int) -> str:
    return names.get(player_id, f"Player({player_id})")


class NarrativePlayerLine(BaseModel):
    """A named performance line the narrative can reference."""

    model_config = ConfigDict(frozen=True)

    name: str
    performance_score: float
    label: str  # PerformanceLabel value (English enum value)
    box_line: dict[str, object]


class NarrativeDifference(BaseModel):
    """A slot where the actual player differed from the model's pick."""

    model_config = ConfigDict(frozen=True)

    batting_order: int
    actual_name: str
    recommended_name: str
    verdict: str
    actual_performance: float
    recommended_performance: float | None


class NarrativeFacts(BaseModel):
    """The minimal, name-resolved fact set the narrative is built from."""

    model_config = ConfigDict(frozen=True)

    gap_label: str
    pregame_actual_score: float
    pregame_recommended_score: float
    overperformers: tuple[NarrativePlayerLine, ...]
    underperformers: tuple[NarrativePlayerLine, ...]
    difference_reviews: tuple[NarrativeDifference, ...]

    @classmethod
    def from_breakdown(
        cls, breakdown: PostgameReviewBreakdown, names: Mapping[int, str]
    ) -> NarrativeFacts:
        """Project a PostgameReviewBreakdown into name-resolved narrative facts."""

        def line(perf: object) -> NarrativePlayerLine:
            return NarrativePlayerLine(
                name=_name(names, perf.player_id),  # type: ignore[attr-defined]
                performance_score=perf.performance_score,  # type: ignore[attr-defined]
                label=str(perf.label),  # type: ignore[attr-defined]
                box_line=dict(perf.box_line),  # type: ignore[attr-defined]
            )

        diffs = tuple(
            NarrativeDifference(
                batting_order=d.batting_order,
                actual_name=_name(names, d.actual_player_id),
                recommended_name=_name(names, d.recommended_player_id),
                verdict=d.verdict,
                actual_performance=d.actual_performance,
                recommended_performance=d.recommended_performance,
            )
            for d in breakdown.difference_reviews
        )
        return cls(
            gap_label=breakdown.pregame_gap_label,
            pregame_actual_score=breakdown.pregame_actual_score,
            pregame_recommended_score=breakdown.pregame_recommended_score,
            overperformers=tuple(line(p) for p in breakdown.overperformers),
            underperformers=tuple(line(p) for p in breakdown.underperformers),
            difference_reviews=diffs,
        )


class NarrativeProvider(Protocol):
    """Abstraction over a narrative LLM call (same shape as the batting-order one)."""

    def complete(self, *, system: str, user: str, schema: dict[str, object]) -> dict[str, object]:
        """Return a parsed JSON object conforming to the given schema."""
        ...
