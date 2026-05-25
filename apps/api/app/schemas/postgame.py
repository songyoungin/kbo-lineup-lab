"""Pydantic response schemas for postgame review API endpoints."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class PostgamePlayerLine(BaseModel):
    """One actual-lineup player's postgame box score line and performance label."""

    model_config = ConfigDict(frozen=True)

    player_id: int
    name: str
    performance_score: float
    label: Literal["Overperformed", "Expected", "Underperformed"]
    box_line: dict[str, object]


class PostgameDifferenceReview(BaseModel):
    """Review for a batting slot where the actual player differed from the recommendation."""

    model_config = ConfigDict(frozen=True)

    batting_order: int
    actual_player_id: int
    actual_player_name: str
    recommended_player_id: int
    recommended_player_name: str
    actual_performance: float
    verdict: str
    rationale: str


class PostgameResponse(BaseModel):
    """Response for GET /api/games/{game_id}/postgame."""

    model_config = ConfigDict(frozen=True)

    game_id: int
    evaluation_run_id: int
    postgame_review_run_id: int
    pregame_actual_score: float
    pregame_recommended_score: float
    pregame_score_gap: float
    pregame_gap_label: str
    overperformers: list[PostgamePlayerLine]
    underperformers: list[PostgamePlayerLine]
    other_actual: list[PostgamePlayerLine]
    difference_reviews: list[PostgameDifferenceReview]
    summary_text: str
    model_limitations: list[str]


# ---------------------------------------------------------------------------
# Job request / response models
# ---------------------------------------------------------------------------


class GeneratePostgameReviewRequest(BaseModel):
    """Request body for POST /api/jobs/generate-postgame-review."""

    evaluation_run_id: int
    box_score_snapshot_id: int


class GeneratePostgameReviewResponse(BaseModel):
    """Response for POST /api/jobs/generate-postgame-review."""

    model_config = ConfigDict(frozen=True)

    postgame_review_run_id: int
    created: bool
    status: str
