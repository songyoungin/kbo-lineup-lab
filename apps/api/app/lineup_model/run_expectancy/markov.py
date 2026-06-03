"""Analytic Markov run-expectancy for an ordered lineup.

Pure and deterministic (expected value, no sampling). Base advancement is a
fixed deterministic model: runners advance by the hit value; walks force only
forced runners; no double plays or sacrifices.

Within an inning we step PA-by-PA in lockstep from a single leadoff batter, so
every live path has taken the same number of PAs at step ``t`` and the batter
is always ``(leadoff + t) % n``. The intra-inning state therefore collapses
from (bases, outs, batter) to just (bases, outs). We precompute one inning per
possible leadoff batter (expected runs + the next-inning leadoff distribution),
then chain across innings by linear expectation -- exact, and ~18x faster than
tracking the batter inside the state.
"""

from __future__ import annotations

import functools

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


_MASS_EPS = 1e-12  # stop an inning once live probability mass is negligible


def _inning_from_leadoff(order: tuple[EventRates, ...], leadoff: int) -> tuple[float, list[float]]:
    """Expected runs in one inning starting with ``leadoff``, plus the
    probability distribution over which batter leads off the NEXT inning.

    Lockstep PA stepping keeps the batter a function of the step
    (``(leadoff + step) % n``), so state is just (bases, outs).
    """
    n = len(order)
    dist: dict[tuple[int, int], float] = {(0, 0): 1.0}
    exp_runs = 0.0
    next_leadoff = [0.0] * n
    step = 0
    while dist and step < _MAX_PA_PER_INNING:
        batter = (leadoff + step) % n
        nb = (batter + 1) % n
        r = order[batter]
        new_dist: dict[tuple[int, int], float] = {}
        for (bases, outs), p in dist.items():
            if r.out > 0.0:
                no = outs + 1
                if no >= 3:
                    next_leadoff[nb] += p * r.out
                else:
                    key = (bases, no)
                    new_dist[key] = new_dist.get(key, 0.0) + p * r.out
            if r.bb > 0.0:
                nbb, runs = _advance_walk(bases)
                exp_runs += p * r.bb * runs
                key = (nbb, outs)
                new_dist[key] = new_dist.get(key, 0.0) + p * r.bb
            for k, prob in ((1, r.single), (2, r.double), (3, r.triple), (4, r.hr)):
                if prob > 0.0:
                    nbk, runs = _advance_hit(bases, k)
                    exp_runs += p * prob * runs
                    key = (nbk, outs)
                    new_dist[key] = new_dist.get(key, 0.0) + p * prob
        dist = new_dist
        step += 1
        if dist and sum(dist.values()) < _MASS_EPS:
            break
    # Residual (non-terminated) mass leads off next inning with the due batter.
    residual = sum(dist.values())
    if residual > 0.0:
        next_leadoff[(leadoff + step) % n] += residual
    return exp_runs, next_leadoff


@functools.lru_cache(maxsize=100_000)
def expected_runs(order: tuple[EventRates, ...], innings: int = 9) -> float:
    """Expected runs scored by ``order`` over ``innings`` innings.

    Deterministic analytic expectation (no sampling). Computes one inning per
    possible leadoff batter, then chains across innings.
    """
    n = len(order)
    runs_by_leadoff: list[float] = [0.0] * n
    next_by_leadoff: list[list[float]] = [[0.0] * n for _ in range(n)]
    for s in range(n):
        runs_by_leadoff[s], next_by_leadoff[s] = _inning_from_leadoff(order, s)

    total = 0.0
    leadoff = [0.0] * n
    leadoff[0] = 1.0
    for _inning in range(innings):
        total += sum(leadoff[s] * runs_by_leadoff[s] for s in range(n))
        new_lead = [0.0] * n
        for s in range(n):
            ps = leadoff[s]
            if ps > 0.0:
                row = next_by_leadoff[s]
                for t in range(n):
                    new_lead[t] += ps * row[t]
        leadoff = new_lead
    return total
