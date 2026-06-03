"""Tests for lineup-level scoring: expected runs and handedness adjustment."""

from __future__ import annotations

import pytest

from app.lineup_model.lineup_score import compute_lineup_score
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
# compute_lineup_score — end-to-end
# ---------------------------------------------------------------------------


def test_compute_lineup_score_returns_breakdown() -> None:
    """compute_lineup_score returns a valid LineupScoreBreakdown."""
    # Use alternating handedness so the handedness penalty does not apply.
    mixed_hands = [Handedness.LEFT if i % 2 == 0 else Handedness.RIGHT for i in range(9)]
    slots, stats_by_player = _make_full_lineup(mixed_hands)
    bd = compute_lineup_score(slots, stats_by_player, Handedness.RIGHT)
    assert bd.total_score > 0
    assert bd.slots == slots
    # Reasons: expected_runs + handedness_balance (2 reasons)
    assert len(bd.reasons) == 2


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


def test_compute_lineup_score_all_right_handedness_penalty() -> None:
    """9 same-side hitters produces a run-unit handedness penalty in the breakdown."""
    slots, stats_by_player = _make_full_lineup([Handedness.RIGHT] * 9)
    bd = compute_lineup_score(slots, stats_by_player, Handedness.RIGHT)
    # 9-streak → 6+ → -HAND_PEN_6 = -0.25 in run units
    assert bd.handedness_balance_adjustment == pytest.approx(-0.25)


def test_compute_lineup_score_complete_lineup_position_completeness_zero() -> None:
    """position_completeness_adjustment is always 0.0 (constant, dropped)."""
    slots, stats_by_player = _make_full_lineup()
    bd = compute_lineup_score(slots, stats_by_player, Handedness.RIGHT)
    assert bd.position_completeness_adjustment == pytest.approx(0.0)


def test_compute_lineup_score_reasons_components() -> None:
    """Reasons must contain expected_runs and handedness_balance components."""
    slots, stats_by_player = _make_full_lineup()
    bd = compute_lineup_score(slots, stats_by_player, Handedness.RIGHT)
    components = {r.component for r in bd.reasons}
    assert "expected_runs" in components
    assert "handedness_balance" in components


def test_lineup_score_is_expected_runs_scale() -> None:
    """total_score is now expected runs (~3-6 for a real lineup), not an OPS-scale average."""
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
    slots = tuple(
        LineupSlot(batting_order=i + 1, player_id=i + 1, position=pos)
        for i, pos in enumerate(positions)
    )
    stats_by_player = {
        i + 1: _make_stats(player_id=i + 1, obp=0.350, slg=0.450, primary_position=pos)
        for i, pos in enumerate(positions)
    }
    bd = compute_lineup_score(slots, stats_by_player, Handedness.RIGHT)
    assert bd.total_score > 1.0  # run scale, not ~0.x OPS scale
    assert bd.weighted_player_score == bd.total_score - bd.handedness_balance_adjustment
    assert bd.position_completeness_adjustment == 0.0
