"""Domain types for postgame review output."""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class PerformanceLabel(StrEnum):
    OVERPERFORMED = "Overperformed"
    EXPECTED = "Expected"
    UNDERPERFORMED = "Underperformed"


class PlayerPerformance(BaseModel):
    """One actual-lineup player's box score performance score."""

    model_config = ConfigDict(frozen=True)

    player_id: int
    performance_score: float
    label: PerformanceLabel
    box_line: dict[str, object]  # raw box score row for transparency


class DifferenceReview(BaseModel):
    """Review of a batting slot where the actual player differed from the recommendation."""

    model_config = ConfigDict(frozen=True)

    batting_order: int
    actual_player_id: int
    recommended_player_id: int
    actual_performance: float
    verdict: str  # e.g. "Actual choice succeeded", "Model would have done better"
    rationale: str


class PostgameReviewBreakdown(BaseModel):
    """Full postgame review result for one evaluation run + box score pair."""

    model_config = ConfigDict(frozen=True)

    evaluation_run_id: int
    box_score_snapshot_id: int
    pregame_actual_score: float
    pregame_recommended_score: float
    pregame_score_gap: float
    pregame_gap_label: (
        str  # "nearly optimal" / "acceptable" / "questionable" / "low offensive efficiency"
    )
    overperformers: tuple[PlayerPerformance, ...]
    underperformers: tuple[PlayerPerformance, ...]
    other_actual: tuple[PlayerPerformance, ...]  # not over- or underperformers
    difference_reviews: tuple[DifferenceReview, ...]
    summary_text: str
    key_insights_json: dict[str, object]
