"""Tests for lineup-level scoring: batting-order weights, slot emphasis,
position completeness, handedness-balance penalty, and compute_lineup_score.
"""

from __future__ import annotations

import pytest

from app.lineup_model.lineup_score import (
    BATTING_ORDER_WEIGHTS,
    compute_lineup_score,
    handedness_balance_penalty,
    position_completeness,
    slot_emphasis_adjustment,
)
from app.lineup_model.types import Handedness, HitterStats, LineupSlot, Position

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_stats(player_id: int, **kwargs: object) -> HitterStats:
    defaults: dict[str, object] = {
        "player_id": player_id,
        "handedness": Handedness.RIGHT,
        "ops": 0.800,
        "obp": 0.350,
        "slg": 0.450,
        "primary_position": Position.FIRST,
        "starts_last_5_games": 3,
    }
    defaults.update(kwargs)
    return HitterStats(**defaults)  # type: ignore[arg-type]


def _make_full_lineup(
    handedness_list: list[Handedness] | None = None,
) -> tuple[
    tuple[LineupSlot, ...],
    dict[int, HitterStats],
]:
    """Build a complete 9-slot lineup with distinct positions and player IDs."""
    positions = [
        Position.C,
        Position.FIRST,
        Position.SECOND,
        Position.THIRD,
        Position.SHORT,
        Position.LEFT,
        Position.CENTER,
        Position.RIGHT,
        Position.DH,
    ]
    if handedness_list is None:
        handedness_list = [Handedness.RIGHT] * 9

    slots = tuple(
        LineupSlot(batting_order=i + 1, player_id=i + 1, position=positions[i]) for i in range(9)
    )
    stats_by_player = {
        i + 1: _make_stats(
            i + 1,
            primary_position=positions[i],
            handedness=handedness_list[i],
        )
        for i in range(9)
    }
    return slots, stats_by_player


# ---------------------------------------------------------------------------
# BATTING_ORDER_WEIGHTS
# ---------------------------------------------------------------------------


def test_batting_order_weights_defined_for_all_slots() -> None:
    """All 9 batting slots must have a weight defined."""
    for slot in range(1, 10):
        assert slot in BATTING_ORDER_WEIGHTS


def test_batting_order_weights_correct_values() -> None:
    """Spot-check specific slot weights per design spec."""
    assert BATTING_ORDER_WEIGHTS[1] == pytest.approx(1.10)
    assert BATTING_ORDER_WEIGHTS[3] == pytest.approx(1.15)
    assert BATTING_ORDER_WEIGHTS[4] == pytest.approx(1.20)
    assert BATTING_ORDER_WEIGHTS[9] == pytest.approx(0.75)


# ---------------------------------------------------------------------------
# slot_emphasis_adjustment
# ---------------------------------------------------------------------------


def test_slot_1_emphasises_obp() -> None:
    """Slot 1 should return a positive emphasis driven by OBP."""
    stats = _make_stats(1, obp=0.400, slg=0.450)
    adj = slot_emphasis_adjustment(stats, batting_order=1)
    assert adj > 0.0


def test_slot_4_emphasises_slg() -> None:
    """Slot 4 should return a positive emphasis driven by SLG."""
    stats = _make_stats(1, obp=0.300, slg=0.600)
    adj = slot_emphasis_adjustment(stats, batting_order=4)
    assert adj > 0.0


def test_slot_6_no_emphasis() -> None:
    """Slot 6 has no defined OBP or SLG boost — emphasis should be 0."""
    stats = _make_stats(1, obp=0.400, slg=0.600)
    adj = slot_emphasis_adjustment(stats, batting_order=6)
    assert adj == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# position_completeness
# ---------------------------------------------------------------------------


def test_position_completeness_full_lineup() -> None:
    """All 9 distinct positions → +0.05 bonus."""
    slots, _ = _make_full_lineup()
    adj = position_completeness(slots)
    assert adj == pytest.approx(0.05)


def test_position_completeness_missing_position() -> None:
    """Fewer than 9 positions → 0.0 (no bonus)."""
    slots = (
        LineupSlot(batting_order=1, player_id=1, position=Position.C),
        LineupSlot(batting_order=2, player_id=2, position=Position.FIRST),
    )
    adj = position_completeness(slots)
    assert adj == pytest.approx(0.0)


def test_position_completeness_duplicate_position() -> None:
    """Duplicate positions → 0.0 even if count is 9."""
    # Two catchers, no DH
    positions = [
        Position.C,
        Position.C,
        Position.SECOND,
        Position.THIRD,
        Position.SHORT,
        Position.LEFT,
        Position.CENTER,
        Position.RIGHT,
        Position.FIRST,
    ]
    slots = tuple(
        LineupSlot(batting_order=i + 1, player_id=i + 1, position=positions[i]) for i in range(9)
    )
    adj = position_completeness(slots)
    assert adj == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# handedness_balance_penalty
# ---------------------------------------------------------------------------


def test_handedness_penalty_four_in_a_row_no_penalty() -> None:
    """4 consecutive same-side hitters → 0 penalty (max run == 4)."""
    # Pattern: 4 R, 1 L, 4 R → max run = 4, no penalty
    hands = [Handedness.RIGHT] * 4 + [Handedness.LEFT] + [Handedness.RIGHT] * 4
    slots, stats_by_player = _make_full_lineup(hands)
    penalty = handedness_balance_penalty(slots, stats_by_player)
    assert penalty == pytest.approx(0.0)


def test_handedness_penalty_five_in_a_row() -> None:
    """5 consecutive same-side hitters → -1 penalty."""
    hands = [Handedness.RIGHT] * 5 + [Handedness.LEFT] * 4
    slots, stats_by_player = _make_full_lineup(hands)
    penalty = handedness_balance_penalty(slots, stats_by_player)
    assert penalty == pytest.approx(-1.0)


def test_handedness_penalty_six_in_a_row() -> None:
    """6 consecutive same-side hitters → -2 penalty."""
    hands = [Handedness.RIGHT] * 6 + [Handedness.LEFT] * 3
    slots, stats_by_player = _make_full_lineup(hands)
    penalty = handedness_balance_penalty(slots, stats_by_player)
    assert penalty == pytest.approx(-2.0)


def test_handedness_penalty_seven_in_a_row() -> None:
    """7+ consecutive → -2 penalty (same as 6)."""
    hands = [Handedness.RIGHT] * 7 + [Handedness.LEFT] * 2
    slots, stats_by_player = _make_full_lineup(hands)
    penalty = handedness_balance_penalty(slots, stats_by_player)
    assert penalty == pytest.approx(-2.0)


def test_handedness_penalty_switch_treated_as_right() -> None:
    """Switch hitters count as RIGHT in streak calculation."""
    # 4 R + 2 S + 3 L → streak of 6 R/S → -2
    hands = [Handedness.RIGHT] * 4 + [Handedness.SWITCH] * 2 + [Handedness.LEFT] * 3
    slots, stats_by_player = _make_full_lineup(hands)
    penalty = handedness_balance_penalty(slots, stats_by_player)
    assert penalty == pytest.approx(-2.0)


def test_handedness_penalty_alternating_no_penalty() -> None:
    """Alternating L/R → longest run = 1 → 0 penalty."""
    hands = [Handedness.LEFT if i % 2 == 0 else Handedness.RIGHT for i in range(9)]
    slots, stats_by_player = _make_full_lineup(hands)
    penalty = handedness_balance_penalty(slots, stats_by_player)
    assert penalty == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# compute_lineup_score — end-to-end
# ---------------------------------------------------------------------------


def test_compute_lineup_score_returns_breakdown() -> None:
    """compute_lineup_score returns a valid LineupScoreBreakdown."""
    # Use alternating handedness so the -2 balance penalty does not apply.
    mixed_hands = [Handedness.LEFT if i % 2 == 0 else Handedness.RIGHT for i in range(9)]
    slots, stats_by_player = _make_full_lineup(mixed_hands)
    bd = compute_lineup_score(slots, stats_by_player, Handedness.RIGHT)
    assert bd.total_score > 0
    assert bd.slots == slots
    # Reasons: 9 slot reasons + position_completeness + handedness_balance
    assert len(bd.reasons) == 11


def test_compute_lineup_score_total_equals_components() -> None:
    """total_score == weighted_player_score + completeness + balance."""
    slots, stats_by_player = _make_full_lineup()
    bd = compute_lineup_score(slots, stats_by_player, Handedness.RIGHT)
    reconstructed = (
        bd.weighted_player_score
        + bd.position_completeness_adjustment
        + bd.handedness_balance_adjustment
    )
    assert bd.total_score == pytest.approx(reconstructed)


def test_compute_lineup_score_all_right_no_balance_penalty() -> None:
    """9 same-side hitters produces a -2 balance penalty in the breakdown."""
    slots, stats_by_player = _make_full_lineup([Handedness.RIGHT] * 9)
    bd = compute_lineup_score(slots, stats_by_player, Handedness.RIGHT)
    assert bd.handedness_balance_adjustment == pytest.approx(-2.0)


def test_compute_lineup_score_complete_lineup_gets_bonus() -> None:
    """A complete distinct-position lineup gets +0.05 completeness bonus."""
    slots, stats_by_player = _make_full_lineup()
    bd = compute_lineup_score(slots, stats_by_player, Handedness.RIGHT)
    assert bd.position_completeness_adjustment == pytest.approx(0.05)


def test_compute_lineup_score_reasons_include_all_slots() -> None:
    """There must be a reason entry for each slot number 1–9."""
    slots, stats_by_player = _make_full_lineup()
    bd = compute_lineup_score(slots, stats_by_player, Handedness.RIGHT)
    slot_reasons = [r for r in bd.reasons if r.component.startswith("slot_")]
    slot_numbers = {int(r.component.split("_")[1]) for r in slot_reasons}
    assert slot_numbers == set(range(1, 10))
