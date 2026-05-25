"""Immutable typed inputs and outputs for the lineup scoring model.

All models are frozen (immutable) so that scoring functions are pure
and cannot accidentally mutate their inputs.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class Handedness(StrEnum):
    """Batting or pitching handedness."""

    LEFT = "L"
    RIGHT = "R"
    SWITCH = "S"


class Position(StrEnum):
    """Defensive positions recognised by the model."""

    P = "P"
    C = "C"
    FIRST = "1B"
    SECOND = "2B"
    THIRD = "3B"
    SHORT = "SS"
    LEFT = "LF"
    CENTER = "CF"
    RIGHT = "RF"
    DH = "DH"


# Canonical defensive lineup positions (excluding pitcher in a DH game).
LINEUP_POSITIONS: tuple[Position, ...] = (
    Position.C,
    Position.FIRST,
    Position.SECOND,
    Position.THIRD,
    Position.SHORT,
    Position.LEFT,
    Position.CENTER,
    Position.RIGHT,
    Position.DH,
)


class HitterStats(BaseModel):
    """Season + recent + split stats for one hitter at a cutoff date.

    Optional fields fall back to season OPS when missing — documented on
    each scoring function that applies the fallback.
    """

    model_config = ConfigDict(frozen=True)

    player_id: int
    handedness: Handedness
    # Season averages
    ops: float
    obp: float
    slg: float
    # Recent form (optional — model falls back to season OPS when absent)
    recent_14d_ops: float | None = None
    recent_30d_ops: float | None = None
    # Handedness splits vs RHP / LHP (optional — falls back to season OPS)
    vs_rhp_ops: float | None = None
    vs_rhp_pa: int = 0
    vs_lhp_ops: float | None = None
    vs_lhp_pa: int = 0
    # Defensive eligibility
    primary_position: Position
    secondary_positions: tuple[Position, ...] = ()
    recent_positions: tuple[Position, ...] = ()
    # Consecutive starts tracking
    starts_last_5_games: int = 0


class ScoringReason(BaseModel):
    """Single line of rationale explaining one component's contribution."""

    model_config = ConfigDict(frozen=True)

    component: str  # e.g. "season_offense", "recent_form", "matchup"
    value: float
    weight: float
    note: str = ""


class PlayerScoreBreakdown(BaseModel):
    """Full breakdown of a player's score for a specific slot and matchup."""

    model_config = ConfigDict(frozen=True)

    player_id: int
    total_score: float
    reasons: tuple[ScoringReason, ...]


class LineupSlot(BaseModel):
    """One player placed in a batting-order slot at a defensive position."""

    model_config = ConfigDict(frozen=True)

    batting_order: int  # 1–9
    player_id: int
    position: Position


class LineupScoreBreakdown(BaseModel):
    """Aggregate score for a complete 9-slot lineup."""

    model_config = ConfigDict(frozen=True)

    slots: tuple[LineupSlot, ...]
    weighted_player_score: float
    position_completeness_adjustment: float
    handedness_balance_adjustment: float
    total_score: float
    reasons: tuple[ScoringReason, ...]
