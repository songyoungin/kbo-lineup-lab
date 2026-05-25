"""Lineup-level scoring: batting-order weights, slot emphasis, and adjustments.

Inputs are pure Python objects; no database access.
"""

from __future__ import annotations

from app.lineup_model.player_score import compute_player_score
from app.lineup_model.types import (
    Handedness,
    HitterStats,
    LineupScoreBreakdown,
    LineupSlot,
    Position,
    ScoringReason,
)

# ---------------------------------------------------------------------------
# Batting-order weights
# ---------------------------------------------------------------------------

BATTING_ORDER_WEIGHTS: dict[int, float] = {
    1: 1.10,
    2: 1.05,
    3: 1.15,
    4: 1.20,
    5: 1.10,
    6: 0.95,
    7: 0.90,
    8: 0.80,
    9: 0.75,
}

# Per-slot emphasis: how much to additionally scale the OBP component
# (positive) or SLG component (positive) within the season_offense sub-score.
# Values are applied as a multiplier on the respective stat's contribution.
# Slot 1/2 favour OBP; slot 4/5 favour SLG; others are balanced.
_SLOT_OBP_BOOST: dict[int, float] = {
    1: 0.15,
    2: 0.10,
    9: 0.05,
}
_SLOT_SLG_BOOST: dict[int, float] = {
    4: 0.15,
    5: 0.10,
    3: 0.05,
}


# ---------------------------------------------------------------------------
# Slot emphasis
# ---------------------------------------------------------------------------


def slot_emphasis_adjustment(stats: HitterStats, batting_order: int) -> float:
    """Compute a small per-slot bonus based on the player's OBP or SLG.

    Slots 1 and 2 reward high OBP; slots 4 and 5 reward high SLG.
    The adjustment is additive to the player's base score before the
    batting-order weight is applied.

    Args:
        stats: Hitter statistics providing OBP and SLG.
        batting_order: Slot number (1–9).

    Returns:
        Adjustment value (can be 0.0 if no bonus applies for this slot).
    """
    obp_boost = _SLOT_OBP_BOOST.get(batting_order, 0.0)
    slg_boost = _SLOT_SLG_BOOST.get(batting_order, 0.0)
    return obp_boost * stats.obp + slg_boost * stats.slg


# ---------------------------------------------------------------------------
# Position completeness
# ---------------------------------------------------------------------------

# All nine expected defensive positions for a standard KBO DH-rule game.
_REQUIRED_POSITIONS: frozenset[Position] = frozenset(
    {
        Position.C,
        Position.FIRST,
        Position.SECOND,
        Position.THIRD,
        Position.SHORT,
        Position.LEFT,
        Position.CENTER,
        Position.RIGHT,
        Position.DH,
    }
)


def position_completeness(slots: tuple[LineupSlot, ...]) -> float:
    """Score bonus / penalty for defensive position completeness.

    Returns:
      +0.05  if all 9 required positions are covered exactly once.
       0.00  for any other configuration (partial or duplicate).
    """
    filled = [slot.position for slot in slots]
    if set(filled) == _REQUIRED_POSITIONS and len(filled) == 9:
        return 0.05
    return 0.0


# ---------------------------------------------------------------------------
# Handedness balance penalty
# ---------------------------------------------------------------------------


def handedness_balance_penalty(
    slots: tuple[LineupSlot, ...],
    stats_by_player: dict[int, HitterStats],
) -> float:
    """Compute a consecutive same-handedness penalty.

    Counts the longest run of consecutive L or R hitters (switch hitters
    count as their non-dominant side for the purpose of this calculation;
    we conservatively treat switch hitters as the most common side to
    avoid masking streaks).

    Penalty:
      5 consecutive same-handedness → -1
      6+ consecutive same-handedness → -2
      otherwise → 0

    Args:
        slots: Batting-order slots sorted by batting_order.
        stats_by_player: Mapping from player_id to HitterStats.

    Returns:
        Penalty value (0, -1, or -2).
    """
    ordered = sorted(slots, key=lambda s: s.batting_order)
    handedness_list: list[str] = []
    for slot in ordered:
        h = stats_by_player[slot.player_id].handedness
        # Switch hitters are treated as RIGHT for streak calculation
        handedness_list.append("R" if h == Handedness.SWITCH else str(h))

    max_run = _max_consecutive_run(handedness_list)

    if max_run >= 6:
        return -2.0
    if max_run == 5:
        return -1.0
    return 0.0


def _max_consecutive_run(items: list[str]) -> int:
    """Return the length of the longest consecutive same-value run."""
    if not items:
        return 0
    max_run = current_run = 1
    for i in range(1, len(items)):
        if items[i] == items[i - 1]:
            current_run += 1
            max_run = max(max_run, current_run)
        else:
            current_run = 1
    return max_run


# ---------------------------------------------------------------------------
# Lineup score
# ---------------------------------------------------------------------------


def compute_lineup_score(
    slots: tuple[LineupSlot, ...],
    stats_by_player: dict[int, HitterStats],
    opp_handedness: Handedness,
) -> LineupScoreBreakdown:
    """Compute the aggregate score for a complete lineup.

    Algorithm:
    1. For each slot, compute a per-player score adjusted by slot emphasis
       and multiplied by the batting-order weight.
    2. Average the weighted scores.
    3. Add position completeness adjustment.
    4. Add handedness balance adjustment (typically a penalty).

    Args:
        slots: 9 lineup slots with batting_order, player_id, position.
        stats_by_player: Full HitterStats for every player in the lineup.
        opp_handedness: Opposing starter's handedness.

    Returns:
        LineupScoreBreakdown with all components.
    """
    reasons: list[ScoringReason] = []
    weighted_sum = 0.0
    total_weight = 0.0

    for slot in sorted(slots, key=lambda s: s.batting_order):
        stats = stats_by_player[slot.player_id]
        breakdown = compute_player_score(stats, slot.position, opp_handedness)
        # If position is impossible we still produce a score of 0 with a note.
        base_score = breakdown.total_score if breakdown is not None else 0.0
        emphasis = slot_emphasis_adjustment(stats, slot.batting_order)
        adjusted = base_score + emphasis
        order_weight = BATTING_ORDER_WEIGHTS.get(slot.batting_order, 1.0)
        weighted_sum += adjusted * order_weight
        total_weight += order_weight
        reasons.append(
            ScoringReason(
                component=f"slot_{slot.batting_order}",
                value=adjusted,
                weight=order_weight,
                note=f"player={slot.player_id} pos={slot.position} emphasis={emphasis:.4f}",
            )
        )

    weighted_avg = weighted_sum / total_weight if total_weight > 0 else 0.0

    pos_adj = position_completeness(slots)
    hand_adj = handedness_balance_penalty(slots, stats_by_player)

    total = weighted_avg + pos_adj + hand_adj

    reasons.append(
        ScoringReason(
            component="position_completeness",
            value=pos_adj,
            weight=1.0,
            note="all 9 positions filled" if pos_adj > 0 else "incomplete or duplicate positions",
        )
    )
    reasons.append(
        ScoringReason(
            component="handedness_balance",
            value=hand_adj,
            weight=1.0,
            note=f"penalty={hand_adj}",
        )
    )

    return LineupScoreBreakdown(
        slots=tuple(slots),
        weighted_player_score=weighted_avg,
        position_completeness_adjustment=pos_adj,
        handedness_balance_adjustment=hand_adj,
        total_score=total,
        reasons=tuple(reasons),
    )
