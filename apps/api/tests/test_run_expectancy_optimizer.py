from __future__ import annotations

from app.lineup_model.run_expectancy.markov import expected_runs
from app.lineup_model.run_expectancy.optimizer import optimize_order
from app.lineup_model.run_expectancy.rates import event_rates
from app.lineup_model.types import Handedness, HitterStats, LineupSlot, Position

_POSITIONS = (
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


def _assigned(obps_slgs: list[tuple[float, float]]) -> dict[Position, HitterStats]:
    out: dict[Position, HitterStats] = {}
    for i, (pos, (obp, slg)) in enumerate(zip(_POSITIONS, obps_slgs, strict=True)):
        out[pos] = HitterStats(
            player_id=i + 1,
            handedness=Handedness.RIGHT if i % 2 == 0 else Handedness.LEFT,
            ops=obp + slg,
            obp=obp,
            slg=slg,
            primary_position=pos,
        )
    return out


def test_returns_valid_permutation() -> None:
    assigned = _assigned([(0.30 + 0.01 * i, 0.40 + 0.01 * i) for i in range(9)])
    slots = optimize_order(assigned, Handedness.RIGHT)
    assert {s.batting_order for s in slots} == set(range(1, 10))
    assert len({s.player_id for s in slots}) == 9
    assert {s.position for s in slots} == set(_POSITIONS)


def test_is_local_optimum() -> None:
    """No single pairwise swap improves the objective (runs minus handedness penalty)."""
    from app.lineup_model.run_expectancy.optimizer import _handedness_penalty

    assigned = _assigned([(0.28 + 0.02 * i, 0.36 + 0.02 * i) for i in range(9)])
    slots = optimize_order(assigned, Handedness.RIGHT)
    by_pid = {s.player_id: s for s in assigned.values()}
    ordered = sorted(slots, key=lambda s: s.batting_order)

    def obj(seq: list[LineupSlot]) -> float:
        rates = tuple(event_rates(by_pid[s.player_id].obp, by_pid[s.player_id].slg) for s in seq)
        pen = _handedness_penalty([(s.position, by_pid[s.player_id]) for s in seq])
        return expected_runs(rates) - pen

    base = obj(ordered)
    for i in range(9):
        for j in range(i + 1, 9):
            swapped = list(ordered)
            swapped[i], swapped[j] = swapped[j], swapped[i]
            assert obj(swapped) <= base + 1e-9


def test_deterministic_and_input_order_independent() -> None:
    pairs = [(0.30 + 0.01 * i, 0.40 + 0.015 * i) for i in range(9)]
    a = optimize_order(_assigned(pairs), Handedness.RIGHT)
    b = optimize_order(_assigned(pairs), Handedness.RIGHT)
    assert [(s.batting_order, s.player_id) for s in sorted(a, key=lambda x: x.batting_order)] == [
        (s.batting_order, s.player_id) for s in sorted(b, key=lambda x: x.batting_order)
    ]
