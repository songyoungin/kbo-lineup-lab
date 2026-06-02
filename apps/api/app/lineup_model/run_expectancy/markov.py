"""Analytic Markov run-expectancy for an ordered lineup.

Pure and deterministic (expected value, no sampling). State within an inning is
(bases, outs, next_batter); the batter pointer carries across innings. Base
advancement is a fixed deterministic model: runners advance by the hit value;
walks force only forced runners; no double plays or sacrifices.
"""

from __future__ import annotations

from app.lineup_model.run_expectancy.rates import EventRates

# bases is a 3-bit int: bit0 = runner on 1B, bit1 = 2B, bit2 = 3B.
_MAX_PA_PER_INNING = 40  # generous cap; residual mass is negligible


def _advance_walk(bases: int) -> tuple[int, int]:
    """Return (new_bases, runs) after a walk (force-advance only)."""
    b1 = bases & 1
    b2 = (bases >> 1) & 1
    b3 = (bases >> 2) & 1
    if b1 and b2 and b3:
        return 0b111, 1  # bases loaded: runner on 3B forced home
    if b1 and b2:
        return 0b111, 0  # 1B,2B -> 2B,3B; batter 1B
    if b1:
        return (0b011 | (b3 << 2)), 0  # 1B -> 2B; batter 1B; 3B unchanged
    return (0b001 | (b2 << 1) | (b3 << 2)), 0  # batter 1B; others unchanged


def _advance_hit(bases: int, k: int) -> tuple[int, int]:
    """Return (new_bases, runs) after a k-base hit (k=1..3, or 4 for HR)."""
    runs = 0
    new_bases = 0
    for base in (1, 2, 3):
        if bases & (1 << (base - 1)):
            dest = base + k
            if dest >= 4:
                runs += 1
            else:
                new_bases |= 1 << (dest - 1)
    if k >= 4:
        runs += 1  # batter homers
    else:
        new_bases |= 1 << (k - 1)  # batter to base k
    return new_bases, runs


def expected_runs(order: tuple[EventRates, ...], innings: int = 9) -> float:
    """Expected runs scored by ``order`` over ``innings`` innings.

    Args:
        order: 9 EventRates in batting-order sequence.
        innings: Number of innings to simulate (default 9).

    Returns:
        Expected runs (a non-negative float), deterministic for fixed inputs.
    """
    n = len(order)
    total_runs = 0.0
    leadoff = [0.0] * n
    leadoff[0] = 1.0

    for _inning in range(innings):
        dist: dict[tuple[int, int, int], float] = {}
        for bi in range(n):
            if leadoff[bi] > 0.0:
                dist[(0, 0, bi)] = dist.get((0, 0, bi), 0.0) + leadoff[bi]
        next_leadoff = [0.0] * n

        for _pa in range(_MAX_PA_PER_INNING):
            if not dist:
                break
            new_dist: dict[tuple[int, int, int], float] = {}
            for (bases, outs, batter), p in dist.items():
                r = order[batter]
                nb = (batter + 1) % n
                if r.out > 0.0:
                    no = outs + 1
                    if no >= 3:
                        next_leadoff[nb] += p * r.out
                    else:
                        key = (bases, no, nb)
                        new_dist[key] = new_dist.get(key, 0.0) + p * r.out
                if r.bb > 0.0:
                    nbb, runs = _advance_walk(bases)
                    total_runs += p * r.bb * runs
                    key = (nbb, outs, nb)
                    new_dist[key] = new_dist.get(key, 0.0) + p * r.bb
                for k, prob in ((1, r.single), (2, r.double), (3, r.triple), (4, r.hr)):
                    if prob > 0.0:
                        nbk, runs = _advance_hit(bases, k)
                        total_runs += p * prob * runs
                        key = (nbk, outs, nb)
                        new_dist[key] = new_dist.get(key, 0.0) + p * prob
            dist = new_dist

        for (_bases, _outs, batter), p in dist.items():
            next_leadoff[batter] += p
        leadoff = next_leadoff

    return total_runs
