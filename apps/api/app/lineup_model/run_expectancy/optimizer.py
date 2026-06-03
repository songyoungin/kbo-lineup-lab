"""Deterministic batting-order optimisation by run-expectancy local search.

Best-improvement pairwise-swap hill climbing from a fixed OBP-descending start,
maximising expected runs minus a small handedness-streak penalty. Equal-objective
ties are broken canonically (lexicographically smallest resulting order by
player_id) so the result is order-independent and reproducible.
"""

from __future__ import annotations

from app.lineup_model.run_expectancy.markov import expected_runs
from app.lineup_model.run_expectancy.rates import EventRates, event_rates
from app.lineup_model.types import Handedness, HitterStats, LineupSlot, Position

# Handedness-streak penalty in RUN units (comparable to expected_runs).
_HAND_PEN_5 = 0.10
_HAND_PEN_6 = 0.25


def _max_same_handed_run(handed: list[str]) -> int:
    longest = current = 1
    for i in range(1, len(handed)):
        if handed[i] == handed[i - 1]:
            current += 1
            longest = max(longest, current)
        else:
            current = 1
    return longest if handed else 0


def handedness_penalty(order: list[tuple[Position, HitterStats]]) -> float:
    """Return a run-unit penalty for long same-handedness streaks in the order."""
    handed = ["R" if s.handedness == Handedness.SWITCH else str(s.handedness) for _p, s in order]
    run = _max_same_handed_run(handed)
    if run >= 6:
        return _HAND_PEN_6
    if run == 5:
        return _HAND_PEN_5
    return 0.0


def _order_key(order: list[tuple[Position, HitterStats]]) -> tuple[int, ...]:
    return tuple(s.player_id for _p, s in order)


def optimize_order(
    assigned: dict[Position, HitterStats],
    opp_handedness: Handedness,
) -> list[LineupSlot]:
    """Return batting-order slots maximising expected runs (deterministic).

    Args:
        assigned: Mapping of defensive position to the assigned HitterStats (9).
        opp_handedness: Opposing starter's handedness (reserved; v1 rates are
            season-based and do not yet branch on it).

    Returns:
        Nine LineupSlot with batting_order 1..9.
    """
    rates_by_pid: dict[int, EventRates] = {
        stats.player_id: event_rates(stats.obp, stats.slg) for stats in assigned.values()
    }

    def objective(order: list[tuple[Position, HitterStats]]) -> float:
        rates = tuple(rates_by_pid[s.player_id] for _p, s in order)
        return expected_runs(rates) - handedness_penalty(order)

    current = sorted(assigned.items(), key=lambda kv: (-kv[1].obp, kv[1].player_id))
    current = [(pos, stats) for pos, stats in current]
    current_j = objective(current)

    improving = True
    while improving:
        improving = False
        best_order = current
        best_j = current_j
        for i in range(len(current)):
            for j in range(i + 1, len(current)):
                cand = list(current)
                cand[i], cand[j] = cand[j], cand[i]
                cj = objective(cand)
                if cj > best_j + 1e-12 or (
                    abs(cj - best_j) <= 1e-12 and _order_key(cand) < _order_key(best_order)
                ):
                    best_order, best_j = cand, cj
        if best_order is not current and (
            best_j > current_j + 1e-12
            or (abs(best_j - current_j) <= 1e-12 and _order_key(best_order) < _order_key(current))
        ):
            current, current_j = best_order, best_j
            improving = True

    return [
        LineupSlot(batting_order=idx + 1, player_id=stats.player_id, position=pos)
        for idx, (pos, stats) in enumerate(current)
    ]
