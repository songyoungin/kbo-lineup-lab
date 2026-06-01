"""Pydantic response schemas for pregame evaluation API endpoints."""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict


def _require_tz(dt: datetime) -> datetime:
    """Reject naive datetimes at Pydantic validation time."""
    if dt.tzinfo is None:
        raise ValueError(f"datetime must be tz-aware, got naive: {dt!r}")
    return dt


TzAwareDatetime = Annotated[datetime, AfterValidator(_require_tz)]

# ---------------------------------------------------------------------------
# Verdict thresholds
# Scores live in rate-stat space (~0.6–1.0). The gap is actual - recommended
# (negative = manager chose a worse lineup than the model recommends).
#
#   gap >= -0.02  → "Nearly optimal"   (within 2 % of recommended)
#   -0.05 .. -0.02 → "Acceptable"      (small but non-trivial gap)
#   -0.10 .. -0.05 → "Questionable"    (meaningful optimisation left on table)
#   < -0.10        → "Low offensive efficiency"  (significant divergence)
# ---------------------------------------------------------------------------

VERDICT_NEARLY_OPTIMAL = "Nearly optimal"
VERDICT_ACCEPTABLE = "Acceptable"
VERDICT_QUESTIONABLE = "Questionable"
VERDICT_LOW = "Low offensive efficiency"

VerdictLiteral = Literal[
    "Nearly optimal",
    "Acceptable",
    "Questionable",
    "Low offensive efficiency",
]


def derive_verdict(score_gap: float) -> VerdictLiteral:
    """Map actual-minus-recommended gap to a human-readable verdict string.

    Args:
        score_gap: actual_score - recommended_score. Negative means actual < recommended.

    Returns:
        One of the four verdict strings.
    """
    if score_gap >= -0.02:
        return "Nearly optimal"
    if score_gap >= -0.05:
        return "Acceptable"
    if score_gap >= -0.10:
        return "Questionable"
    return "Low offensive efficiency"


# ---------------------------------------------------------------------------
# Team home schemas
# ---------------------------------------------------------------------------


class RecentGameSummary(BaseModel):
    """Summary of one recent completed game."""

    model_config = ConfigDict(frozen=True)

    game_id: int
    game_date: date
    opponent_team_code: str
    verdict: str | None


class TeamHomeGameCard(BaseModel):
    """Card for today's upcoming game."""

    model_config = ConfigDict(frozen=True)

    game_id: int
    game_date: date
    opponent_team_code: str
    venue: str | None
    # Opponent starter name — None when not yet known (fixture may not seed it)
    opponent_starter: str | None
    # Pipeline step → status string, e.g. {"schedule": "ok", "lineup": "missing"}
    pipeline_status: dict[str, str]
    # Final score from LG's perspective; None until the game is played (RESULT).
    team_score: int | None = None
    opponent_score: int | None = None
    # Game status mirrors the schedule statusCode (e.g. "RESULT", "BEFORE").
    status: str | None = None
    # Pitcher decisions (names; may be opponent pitchers). None until played.
    winning_pitcher_name: str | None = None
    losing_pitcher_name: str | None = None
    save_pitcher_name: str | None = None


class TeamHomeResponse(BaseModel):
    """Response for GET /api/team/lg/home."""

    model_config = ConfigDict(frozen=True)

    team_code: str
    today: TeamHomeGameCard | None
    recent: list[RecentGameSummary]


# ---------------------------------------------------------------------------
# Pregame evaluation schemas
# ---------------------------------------------------------------------------


class LineupRow(BaseModel):
    """One player slot in an actual or recommended lineup."""

    model_config = ConfigDict(frozen=True)

    batting_order: int
    position: str
    player_id: int
    player_name: str


class LineupDifference(BaseModel):
    """Describes a difference between actual and recommended at a batting order slot."""

    model_config = ConfigDict(frozen=True)

    batting_order: int
    difference_type: str
    # Brief human-readable reason (derived from key_insights rationale)
    main_reason: str


class OpponentPitcher(BaseModel):
    """Opponent starting pitcher quality block from key_insights_json.

    k_pct is a FRACTION (so/tbf, e.g. 0.269), not a percentage; multiplier is the
    matchup-difficulty scaling applied to both headline totals.
    """

    model_config = ConfigDict(frozen=True)

    era: float | None
    whip: float | None
    k_pct: float | None
    multiplier: float


class PregameResponse(BaseModel):
    """Response for GET /api/games/{game_id}/pregame."""

    model_config = ConfigDict(frozen=True)

    game_id: int
    actual_score: float
    recommended_score: float
    # actual_score - recommended_score (negative = actual is worse than recommended)
    score_gap: float
    verdict: VerdictLiteral
    actual_lineup: list[LineupRow]
    recommended_lineup: list[LineupRow]
    differences: list[LineupDifference]
    # Limitations extracted from key_insights_json (e.g. opp_handedness_default note)
    model_limitations: list[str]
    # Opponent starter quality; None when no pitcher data was resolved for the run.
    opponent_pitcher: OpponentPitcher | None = None


# ---------------------------------------------------------------------------
# Lineup comparison schemas
# ---------------------------------------------------------------------------

DifferenceTypeLiteral = Literal[
    "Same",
    "Player changed",
    "Position changed",
    "Batting order changed",
    "Player and order changed",
]


class LineupComparisonRow(BaseModel):
    """Per-slot comparison row between actual and recommended lineups."""

    model_config = ConfigDict(frozen=True)

    batting_order: int
    actual_player_id: int
    actual_player_name: str
    actual_position: str
    recommended_player_id: int
    recommended_player_name: str
    recommended_position: str
    difference_type: DifferenceTypeLiteral
    main_reason: str


class LineupComparisonResponse(BaseModel):
    """Response for GET /api/games/{game_id}/lineup-comparison."""

    model_config = ConfigDict(frozen=True)

    game_id: int
    rows: list[LineupComparisonRow]


# ---------------------------------------------------------------------------
# Player comparison schemas
# ---------------------------------------------------------------------------


class PlayerComparisonStats(BaseModel):
    """Stats for one player in a head-to-head comparison."""

    model_config = ConfigDict(frozen=True)

    player_id: int
    player_name: str
    position: str
    # Season stats
    ops: float
    obp: float
    slg: float
    # Recent form (None when not present in fixture)
    recent_14d_ops: float | None
    recent_30d_ops: float | None
    # Handedness splits
    vs_rhp_ops: float | None
    vs_lhp_ops: float | None
    # Recent runners-in-scoring-position average (None when not present in fixture)
    risp_avg: float | None
    # Playing time indicators
    pa_vs_rhp: int
    pa_vs_lhp: int
    starts_last_5: int
    # Model-assigned score for this slot
    model_score: float | None


class PlayerComparisonResponse(BaseModel):
    """Response for GET /api/games/{game_id}/players/compare?batting_order=N."""

    model_config = ConfigDict(frozen=True)

    batting_order: int
    actual: PlayerComparisonStats
    recommended: PlayerComparisonStats
    # Which player the model favours and the key reason
    judgment: str
    # Factors not captured by the current model
    unmodeled_factors: list[str]


# ---------------------------------------------------------------------------
# Job schemas
# ---------------------------------------------------------------------------


class ReplayEvaluationRequest(BaseModel):
    """Request body for POST /api/jobs/replay-evaluation."""

    game_id: int
    team_id: int
    # Must be tz-aware; Pydantic rejects naive datetimes via TzAwareDatetime
    evaluation_cutoff_at: TzAwareDatetime
    # FK to an existing ModelVersion row
    model_version_id: int


class ReplayEvaluationResponse(BaseModel):
    """Response for POST /api/jobs/replay-evaluation."""

    model_config = ConfigDict(frozen=True)

    evaluation_run_id: int
    # True if a new run was created; False if an existing run was returned
    created: bool
    # "completed" once evaluate_lineup_for_run finishes; "pending" otherwise
    status: str
