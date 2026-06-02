# Phase 3 — Run-Expectancy Lineup Model Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the static weighted-average lineup aggregation with a deterministic **run-expectancy** model that scores a lineup by expected runs and chooses the batting order that maximizes them.

**Architecture:** Four pure, deterministic components in a new `app/lineup_model/run_expectancy/` package — `rates` (OBP/SLG → per-PA event probabilities), `markov` (ordered lineup → expected runs via an analytic Markov chain, no RNG), `optimizer` (deterministic local-search batting order). Then integrate: rewrite `_assign_batting_order` to call the optimizer (signature unchanged, so both the LLM fallback and `generate_recommendation` pick it up) and `compute_lineup_score` to return expected runs (keeping the `LineupScoreBreakdown` shape so `lineup_evaluator` is untouched). See spec `docs/superpowers/specs/2026-06-02-phase3-run-expectancy-design.md`.

**Tech Stack:** Python 3.13 (stdlib only — no numpy/scipy), pytest, uv. Run from `apps/api`.

**Determinism invariant:** `app/lineup_model/` must stay deterministic; `output_hash` reproducible. Every function below is pure, with no randomness/clock/iteration-order dependence. `total_score` rescales to expected runs (~3–6); `output_hash` values shift but stay reproducible (as in Phase 1/2).

---

## File Structure

- **Create** `app/lineup_model/run_expectancy/__init__.py` — package exports.
- **Create** `app/lineup_model/run_expectancy/rates.py` — `EventRates` dataclass + `event_rates(obp, slg)`.
- **Create** `app/lineup_model/run_expectancy/markov.py` — `expected_runs(order, innings=9)` + base-advancement helpers.
- **Create** `app/lineup_model/run_expectancy/optimizer.py` — `optimize_order(assigned, opp_handedness)` + handedness penalty.
- **Modify** `app/lineup_model/recommendation.py` — `_assign_batting_order` delegates to `optimize_order`; remove dead heuristic helpers (`pop_by_key` logic stays only if used elsewhere — it isn't).
- **Modify** `app/lineup_model/lineup_score.py` — `compute_lineup_score` returns expected runs; remove dead slot-weight/position-completeness machinery; rescale handedness to run units.
- **Tests** — one new test module per component; update existing scale-asserting tests in Task 6.

---

## Task 1: `EventRates` + `event_rates` (OBP/SLG → per-PA probabilities)

**Files:**
- Create: `app/lineup_model/run_expectancy/__init__.py`, `app/lineup_model/run_expectancy/rates.py`
- Test: `tests/test_run_expectancy_rates.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_run_expectancy_rates.py`:

```python
from __future__ import annotations

import pytest

from app.lineup_model.run_expectancy.rates import EventRates, event_rates


def _total(r: EventRates) -> float:
    return r.bb + r.single + r.double + r.triple + r.hr + r.out


def test_probabilities_sum_to_one() -> None:
    r = event_rates(obp=0.350, slg=0.450)
    assert _total(r) == pytest.approx(1.0, abs=1e-9)


def test_out_rate_complements_obp() -> None:
    r = event_rates(obp=0.350, slg=0.450)
    assert r.out == pytest.approx(1.0 - 0.350, abs=1e-9)


def test_higher_slg_gives_more_extra_base_power() -> None:
    low = event_rates(obp=0.340, slg=0.380)
    high = event_rates(obp=0.340, slg=0.560)
    # Same OBP, more SLG -> more HR weight, fewer singles.
    assert high.hr > low.hr
    assert high.single < low.single


def test_higher_obp_gives_more_baserunners() -> None:
    low = event_rates(obp=0.300, slg=0.420)
    high = event_rates(obp=0.400, slg=0.420)
    assert (high.bb + high.single + high.double + high.triple + high.hr) > (
        low.bb + low.single + low.double + low.triple + low.hr
    )


def test_degenerate_inputs_are_clamped_and_normalised() -> None:
    # OBP > SLG (extreme slap hitter) and a zero-power line still normalise.
    r = event_rates(obp=0.500, slg=0.500)
    assert _total(r) == pytest.approx(1.0, abs=1e-9)
    assert all(v >= 0.0 for v in (r.bb, r.single, r.double, r.triple, r.hr, r.out))
    zero = event_rates(obp=0.000, slg=0.000)
    assert zero.out == pytest.approx(1.0, abs=1e-9)
```

- [ ] **Step 2: Run to verify failure** — `uv run pytest tests/test_run_expectancy_rates.py -v` → FAIL (module missing).

- [ ] **Step 3: Implement**

Create `app/lineup_model/run_expectancy/__init__.py`:

```python
"""Deterministic run-expectancy lineup model (rates, Markov, order optimizer)."""
```

Create `app/lineup_model/run_expectancy/rates.py`:

```python
"""Approximate per-PA event probabilities derived from season OBP/SLG.

Pure, deterministic. HBP is folded into ``bb``; SF/SH/ROE are ignored. The
league constants are recent-KBO-average approximations — documented as such; a
future upgrade can replace this provider with real per-hitter counting lines
without changing the ``EventRates`` interface.
"""

from __future__ import annotations

from dataclasses import dataclass

# League-average approximations (per-PA walk+HBP rate, and the split of
# extra-base HITS into 2B/3B/HR). Tunable; documented as estimates.
_LEAGUE_BB_RATE = 0.085
_XBH_SHARES = (0.78, 0.04, 0.18)  # (double, triple, hr) fractions of XB hits


@dataclass(frozen=True)
class EventRates:
    """Per-plate-appearance outcome probabilities (sum to 1.0)."""

    bb: float
    single: float
    double: float
    triple: float
    hr: float
    out: float


def event_rates(obp: float, slg: float) -> EventRates:
    """Derive a per-PA outcome distribution from season OBP and SLG.

    Args:
        obp: On-base percentage (reaches base per PA).
        slg: Slugging percentage (total bases per at-bat).

    Returns:
        EventRates whose six probabilities sum to exactly 1.0.
    """
    obp = min(max(obp, 0.0), 0.999)
    slg = max(slg, 0.0)

    bb = min(_LEAGUE_BB_RATE, obp * 0.99)
    hit = max(0.0, obp - bb)  # hits per PA
    out = 1.0 - obp

    ab_per_pa = 1.0 - bb
    tb_per_pa = slg * ab_per_pa
    extra = max(0.0, tb_per_pa - hit)  # extra bases beyond one-per-hit

    f2, f3, f4 = _XBH_SHARES
    weight = f2 * 1.0 + f3 * 2.0 + f4 * 3.0  # extra bases per XB hit
    xb_hits = extra / weight if weight > 0 else 0.0
    xb_hits = min(xb_hits, hit)  # cannot exceed total hits

    double = xb_hits * f2
    triple = xb_hits * f3
    hr = xb_hits * f4
    single = max(0.0, hit - (double + triple + hr))

    raw = [bb, single, double, triple, hr, out]
    total = sum(raw)
    if total <= 0.0:
        return EventRates(bb=0.0, single=0.0, double=0.0, triple=0.0, hr=0.0, out=1.0)
    bb, single, double, triple, hr, out = (v / total for v in raw)
    return EventRates(bb=bb, single=single, double=double, triple=triple, hr=hr, out=out)
```

- [ ] **Step 4: Run to verify pass** — `uv run pytest tests/test_run_expectancy_rates.py -v` → PASS.

- [ ] **Step 5: Commit**

```bash
git add app/lineup_model/run_expectancy/__init__.py app/lineup_model/run_expectancy/rates.py tests/test_run_expectancy_rates.py
git commit -m "feat(run-expectancy): per-PA event rates from OBP/SLG"
```
Fix any pre-commit issues; re-commit (no `--no-verify`).

---

## Task 2: `expected_runs` — analytic Markov chain

**Files:**
- Create: `app/lineup_model/run_expectancy/markov.py`
- Test: `tests/test_run_expectancy_markov.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_run_expectancy_markov.py`:

```python
from __future__ import annotations

import pytest

from app.lineup_model.run_expectancy.markov import expected_runs
from app.lineup_model.run_expectancy.rates import EventRates

_ALL_OUT = EventRates(bb=0.0, single=0.0, double=0.0, triple=0.0, hr=0.0, out=1.0)
_ALL_HR = EventRates(bb=0.0, single=0.0, double=0.0, triple=0.0, hr=1.0, out=0.0)


def _order(rates: EventRates) -> tuple[EventRates, ...]:
    return tuple(rates for _ in range(9))


def test_all_out_lineup_scores_zero() -> None:
    assert expected_runs(_order(_ALL_OUT)) == pytest.approx(0.0, abs=1e-12)


def test_all_hr_lineup_scores_unbounded_capped_by_pa_budget() -> None:
    # Every PA is a HR and never makes an out, so the inning never ends until the
    # PA cap. Expected runs is large and positive; just assert it is huge.
    runs = expected_runs(_order(_ALL_HR), innings=1)
    assert runs > 20.0


def test_sequencing_matters() -> None:
    """A high-OBP table-setter ahead of sluggers outscores the reverse order."""
    setter = EventRates(bb=0.20, single=0.20, double=0.0, triple=0.0, hr=0.0, out=0.60)
    slugger = EventRates(bb=0.0, single=0.0, double=0.0, triple=0.0, hr=0.25, out=0.75)
    filler = EventRates(bb=0.0, single=0.0, double=0.0, triple=0.0, hr=0.0, out=1.0)
    # setters first, then sluggers, then 5 fillers
    good = (setter, setter, slugger, slugger, filler, filler, filler, filler, filler)
    bad = (slugger, slugger, setter, setter, filler, filler, filler, filler, filler)
    assert expected_runs(good) > expected_runs(bad)


def test_deterministic_and_input_independent() -> None:
    a = EventRates(bb=0.1, single=0.2, double=0.05, triple=0.01, hr=0.04, out=0.60)
    b = EventRates(bb=0.08, single=0.18, double=0.04, triple=0.0, hr=0.05, out=0.65)
    order = (a, b, a, b, a, b, a, b, a)
    assert expected_runs(order) == expected_runs(order)


def test_more_innings_scores_more() -> None:
    r = EventRates(bb=0.1, single=0.2, double=0.05, triple=0.01, hr=0.04, out=0.60)
    assert expected_runs(_order(r), innings=9) > expected_runs(_order(r), innings=3)
```

- [ ] **Step 2: Run to verify failure** — `uv run pytest tests/test_run_expectancy_markov.py -v` → FAIL (module missing).

- [ ] **Step 3: Implement**

Create `app/lineup_model/run_expectancy/markov.py`:

```python
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
    # Probability distribution over which batter leads off the inning.
    leadoff = [0.0] * n
    leadoff[0] = 1.0

    for _inning in range(innings):
        # dist: (bases, outs, next_batter) -> probability (mass still in inning)
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

        # Fold any residual (non-terminated) mass into the next inning's leadoff
        # to conserve probability (negligible with _MAX_PA_PER_INNING = 40).
        for (_bases, _outs, batter), p in dist.items():
            next_leadoff[batter] += p
        leadoff = next_leadoff

    return total_runs
```

- [ ] **Step 4: Run to verify pass** — `uv run pytest tests/test_run_expectancy_markov.py -v` → PASS.

- [ ] **Step 5: Commit**

```bash
git add app/lineup_model/run_expectancy/markov.py tests/test_run_expectancy_markov.py
git commit -m "feat(run-expectancy): analytic Markov expected-runs estimator"
```
Fix any pre-commit issues; re-commit.

---

## Task 3: `optimize_order` — deterministic local-search batting order

**Files:**
- Create: `app/lineup_model/run_expectancy/optimizer.py`
- Test: `tests/test_run_expectancy_optimizer.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_run_expectancy_optimizer.py`:

```python
from __future__ import annotations

from app.lineup_model.run_expectancy.markov import expected_runs
from app.lineup_model.run_expectancy.optimizer import optimize_order
from app.lineup_model.run_expectancy.rates import event_rates
from app.lineup_model.types import Handedness, HitterStats, Position

_POSITIONS = (
    Position.C, Position.FIRST, Position.SECOND, Position.THIRD, Position.SHORT,
    Position.LEFT, Position.CENTER, Position.RIGHT, Position.DH,
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


def _rates_in_order(slots, assigned_by_pid):
    ordered = sorted(slots, key=lambda s: s.batting_order)
    return tuple(
        event_rates(assigned_by_pid[s.player_id].obp, assigned_by_pid[s.player_id].slg)
        for s in ordered
    )


def test_returns_valid_permutation() -> None:
    assigned = _assigned([(0.30 + 0.01 * i, 0.40 + 0.01 * i) for i in range(9)])
    slots = optimize_order(assigned, Handedness.RIGHT)
    assert {s.batting_order for s in slots} == set(range(1, 10))
    assert len({s.player_id for s in slots}) == 9
    assert {s.position for s in slots} == set(_POSITIONS)


def test_is_local_optimum() -> None:
    """No single pairwise swap improves expected runs (penalty aside)."""
    assigned = _assigned([(0.28 + 0.02 * i, 0.36 + 0.02 * i) for i in range(9)])
    slots = optimize_order(assigned, Handedness.RIGHT)
    by_pid = {s.player_id: s for s in assigned.values()}
    base = expected_runs(_rates_in_order(slots, by_pid))
    ordered = sorted(slots, key=lambda s: s.batting_order)
    for i in range(9):
        for j in range(i + 1, 9):
            swapped = list(ordered)
            swapped[i], swapped[j] = swapped[j], swapped[i]
            rates = tuple(event_rates(by_pid[s.player_id].obp, by_pid[s.player_id].slg) for s in swapped)
            assert expected_runs(rates) <= base + 1e-9


def test_deterministic_and_input_order_independent() -> None:
    pairs = [(0.30 + 0.01 * i, 0.40 + 0.015 * i) for i in range(9)]
    a = optimize_order(_assigned(pairs), Handedness.RIGHT)
    b = optimize_order(_assigned(pairs), Handedness.RIGHT)
    assert [(s.batting_order, s.player_id) for s in sorted(a, key=lambda x: x.batting_order)] == [
        (s.batting_order, s.player_id) for s in sorted(b, key=lambda x: x.batting_order)
    ]
```

- [ ] **Step 2: Run to verify failure** — `uv run pytest tests/test_run_expectancy_optimizer.py -v` → FAIL (module missing).

- [ ] **Step 3: Implement**

Create `app/lineup_model/run_expectancy/optimizer.py`:

```python
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


def _handedness_penalty(order: list[tuple[Position, HitterStats]]) -> float:
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
        return expected_runs(rates) - _handedness_penalty(order)

    # Deterministic start: OBP descending, ties by ascending player_id.
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
```

- [ ] **Step 4: Run to verify pass** — `uv run pytest tests/test_run_expectancy_optimizer.py -v` → PASS. If `test_is_local_optimum` fails because a swap improves raw runs while the handedness penalty blocked it, that is correct behavior — adjust the test to compare `objective` (runs − penalty) rather than raw `expected_runs`. Prefer fixing the test to assert local-optimality of the *objective*.

- [ ] **Step 5: Commit**

```bash
git add app/lineup_model/run_expectancy/optimizer.py tests/test_run_expectancy_optimizer.py
git commit -m "feat(run-expectancy): deterministic local-search order optimizer"
```
Fix any pre-commit issues; re-commit.

---

## Task 4: Integrate the optimizer into `_assign_batting_order`

**Files:**
- Modify: `app/lineup_model/recommendation.py`
- Test: `tests/test_recommendation.py`

`_assign_batting_order(assigned, opp_handedness) -> list[LineupSlot]` is called by both `orderer._fallback` (production) and `generate_recommendation` (tests). Rewriting its body to delegate to `optimize_order` makes the whole system use run-expectancy ordering with no call-site changes.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_recommendation.py`:

```python
def test_batting_order_uses_run_expectancy_optimizer() -> None:
    """The deterministic order is the run-expectancy optimum, not the old heuristic."""
    from app.lineup_model.recommendation import _assign_batting_order
    from app.lineup_model.run_expectancy.optimizer import optimize_order

    pool = _make_pool()
    assigned = select_and_assign_positions(pool, Handedness.RIGHT)
    via_recommendation = sorted(
        _assign_batting_order(assigned, Handedness.RIGHT), key=lambda s: s.batting_order
    )
    via_optimizer = sorted(optimize_order(assigned, Handedness.RIGHT), key=lambda s: s.batting_order)
    assert [(s.batting_order, s.player_id, s.position) for s in via_recommendation] == [
        (s.batting_order, s.player_id, s.position) for s in via_optimizer
    ]
```

- [ ] **Step 2: Run to verify failure** — `uv run pytest tests/test_recommendation.py::test_batting_order_uses_run_expectancy_optimizer -v` → FAIL (old heuristic differs from optimizer).

- [ ] **Step 3: Implement**

In `app/lineup_model/recommendation.py`:

(a) Add import near the top:
```python
from app.lineup_model.run_expectancy.optimizer import optimize_order
```

(b) Replace the entire body of `_assign_batting_order` with a thin delegation (keep the signature and docstring summary):
```python
def _assign_batting_order(
    assignments: dict[Position, HitterStats],
    opp_handedness: Handedness,
) -> list[LineupSlot]:
    """Order the assigned 9 by maximising run expectancy (deterministic)."""
    return optimize_order(assignments, opp_handedness)
```

(c) Remove now-dead helpers that only `_assign_batting_order`'s old body used: the `composite`/`pop_by_key` inner logic is gone with the body. If `Callable` import becomes unused, remove it (vulture/ruff will flag). Do NOT touch `select_and_assign_positions`, `generate_recommendation`, or `compute_lineup_score` here.

- [ ] **Step 4: Run to verify pass** — `uv run pytest tests/test_recommendation.py -v`. The new test passes. Some pre-existing `test_recommendation.py` assertions about batting-order *content* (e.g. "slot 1 has highest OBP") may now differ — that is the intended behavior change; reconcile them in Task 6, not here. (Structural tests — 9 distinct players, orders 1–9, determinism, position eligibility, infeasible — still pass.)

- [ ] **Step 5: Commit**

```bash
git add app/lineup_model/recommendation.py tests/test_recommendation.py
git commit -m "feat(lineup): batting order via run-expectancy optimizer"
```
Fix any pre-commit issues; re-commit.

---

## Task 5: `compute_lineup_score` returns expected runs

**Files:**
- Modify: `app/lineup_model/lineup_score.py`
- Test: `tests/test_lineup_score.py`

Keep the `LineupScoreBreakdown` shape (so `lineup_evaluator` is untouched): `weighted_player_score` now carries raw expected runs, `position_completeness_adjustment` is `0.0` (dropped — always-satisfied constant), `handedness_balance_adjustment` is the run-unit handedness penalty (negative), and `total_score = weighted_player_score + handedness_balance_adjustment`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_lineup_score.py` (read its top for the existing `HitterStats` helper — there is a `**defaults` factory around line 34; reuse it):

```python
def test_lineup_score_is_expected_runs_scale() -> None:
    """total_score is now expected runs (~3-6 for a real lineup), not an OPS-scale average."""
    from app.lineup_model.lineup_score import compute_lineup_score
    from app.lineup_model.types import Handedness, LineupSlot, Position

    positions = [
        Position.C, Position.FIRST, Position.SECOND, Position.THIRD, Position.SHORT,
        Position.LEFT, Position.CENTER, Position.RIGHT, Position.DH,
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
```

> Adapt `_make_stats(...)` to the file's actual helper name/signature (read `tests/test_lineup_score.py:~20-40`). It must produce a `HitterStats` with the given `obp`, `slg`, `primary_position`.

- [ ] **Step 2: Run to verify failure** — `uv run pytest tests/test_lineup_score.py::test_lineup_score_is_expected_runs_scale -v` → FAIL (old weighted-average ~0.x).

- [ ] **Step 3: Implement**

Rewrite `compute_lineup_score` in `app/lineup_model/lineup_score.py`:

```python
def compute_lineup_score(
    slots: tuple[LineupSlot, ...],
    stats_by_player: dict[int, HitterStats],
    opp_handedness: Handedness,
) -> LineupScoreBreakdown:
    """Score a lineup by its expected runs (deterministic run-expectancy model).

    The batting order in ``slots`` is evaluated by the analytic Markov model; a
    small run-unit penalty discourages long same-handedness streaks. Returns a
    LineupScoreBreakdown whose ``weighted_player_score`` carries the raw expected
    runs and ``total_score`` = expected runs + handedness adjustment.
    """
    ordered = sorted(slots, key=lambda s: s.batting_order)
    rates = tuple(
        event_rates(stats_by_player[s.player_id].obp, stats_by_player[s.player_id].slg)
        for s in ordered
    )
    runs = expected_runs(rates)
    hand_adj = -_handedness_penalty(
        [(s.position, stats_by_player[s.player_id]) for s in ordered]
    )
    total = runs + hand_adj

    reasons: list[ScoringReason] = [
        ScoringReason(component="expected_runs", value=runs, weight=1.0, note=f"{len(ordered)} batters"),
        ScoringReason(
            component="handedness_balance",
            value=hand_adj,
            weight=1.0,
            note=f"penalty={hand_adj}",
        ),
    ]
    return LineupScoreBreakdown(
        slots=tuple(slots),
        weighted_player_score=runs,
        position_completeness_adjustment=0.0,
        handedness_balance_adjustment=hand_adj,
        total_score=total,
        reasons=tuple(reasons),
    )
```

Add imports at the top of `lineup_score.py`:
```python
from app.lineup_model.run_expectancy.markov import expected_runs
from app.lineup_model.run_expectancy.optimizer import _handedness_penalty
from app.lineup_model.run_expectancy.rates import event_rates
```

Remove the now-dead machinery in `lineup_score.py`: `BATTING_ORDER_WEIGHTS`, `_SLOT_OBP_BOOST`, `_SLOT_SLG_BOOST`, `slot_emphasis_adjustment`, `_REQUIRED_POSITIONS`, `position_completeness`, `handedness_balance_penalty`, `_max_consecutive_run` (the handedness penalty now lives in `optimizer._handedness_penalty`). Keep `compute_player_score` import only if still used (it is not after this rewrite — remove it; vulture will flag otherwise).

> If importing `_handedness_penalty` (a private helper) across modules feels wrong, promote it: rename `optimizer._handedness_penalty` → a public `handedness_penalty` in `optimizer.py` and import that. Decide during implementation; keep ONE definition (DRY).

- [ ] **Step 4: Run to verify pass** — `uv run pytest tests/test_lineup_score.py -v`. The new test passes; pre-existing tests in this file asserting the old scale will fail — reconcile in Task 6.

- [ ] **Step 5: Commit**

```bash
git add app/lineup_model/lineup_score.py tests/test_lineup_score.py
git commit -m "feat(lineup): score lineups by expected runs (run-expectancy)"
```
Fix any pre-commit issues; re-commit.

---

## Task 6: Full-suite regression — reconcile scale & order expectations

**Files:**
- Test: scale/order-asserting tests across `tests/` (notably `test_lineup_score.py`, `test_recommendation.py`, `test_pregame_api.py`, `test_postgame_api.py`, `test_pitcher_quality_eval.py`).

The score scale changed (OPS-space ~0.x → expected runs ~3–6) and batting order changed (heuristic → run-expectancy). Tests that asserted the old scale or the old slot rules must be reconciled.

- [ ] **Step 1: Run the full suite and catalog failures**

Run: `uv run pytest -q`
List each failure and classify it: (A) old numeric scale (e.g. asserting total_score in 0.6–1.0, or a `+0.05` completeness bonus, or `-1/-2` handedness), (B) old batting-order rule (e.g. "slot 1 = highest OBP", "slot 4 = highest SLG"), or (C) a genuine regression (NOT explained by the intended scale/order change).

- [ ] **Step 2: Reconcile (A) and (B); STOP on (C)**

For (A) scale assertions: update the expected ranges/values to the run scale, recomputing any literal (including `output_hash`) from the ACTUAL test output — never fabricate. Add a one-line comment noting the run-expectancy scale change.
For (B) order assertions: the slot-content rules ("slot 1 highest OBP", etc.) no longer hold under run-expectancy optimization. Replace them with the invariants that still hold (9 distinct players, batting_order 1–9, valid positions, determinism) OR with an assertion that the order matches `optimize_order`. Remove assertions that encode the retired heuristic.
For any (C): STOP and report BLOCKED with specifics — do not paper over a real regression.

- [ ] **Step 3: Update the pregame/postgame doc-comment expectations**

`tests/test_pregame_api.py` (~line 828-852) has prose comments describing the old "OPS-rate-stat space (~0.6–1.0) plus position_completeness bonus (0 or +0.05) and handedness_balance_penalty (0, -1, or -2)." Update these comments AND any numeric assertions to the run-expectancy scale. Likewise check `pregame_views.py` doc-comments referencing `compute_lineup_score` scale and update prose if inaccurate (no behavior change).

- [ ] **Step 4: Re-run the full suite** — `uv run pytest -q` → all pass.

- [ ] **Step 5: Commit**

```bash
git add tests/ app/services/pregame_views.py
git commit -m "test(lineup): reconcile expectations with run-expectancy scale and ordering"
```
Fix any pre-commit issues; re-commit.

---

## Task 7: Verify & harness

- [ ] **Step 1:** `cd apps/api && uv run pytest -q` → all pass.
- [ ] **Step 2:** `pre-commit run --all-files` → clean (ruff, mypy, bandit, **vulture** — confirms all the removed heuristic/slot-weight machinery is gone with no dangling dead code).
- [ ] **Step 3:** `/harness-audit` → structural + semantic green; record the marker before opening a PR. The CLAUDE.md invariant ("Deterministic scoring and defensive position assignment … must stay deterministic") still holds — every run-expectancy component is pure/deterministic. Confirm no harness doc described the lineup score as an OPS-scale weighted average; update any that did.

---

## Self-Review notes

- **Spec coverage:** rate_provider → Task 1; run_expectancy Markov → Task 2; order_optimizer → Task 3; integration replacing `_assign_batting_order` → Task 4 and `compute_lineup_score` → Task 5; scale/order regression → Task 6; verify/harness → Task 7. Determinism/output_hash invariant addressed in each component's tests + Task 7.
- **No placeholders:** complete code for rates/markov/optimizer and both integration edits. The only data-dependent step is Task 6 (recompute changed scale/`output_hash` literals from actual output — explicitly never fabricated).
- **Type consistency:** `EventRates` (rates.py) is used by `expected_runs(order: tuple[EventRates,...])` (markov.py) and `optimize_order` (optimizer.py); `_assign_batting_order(assigned, opp_handedness) -> list[LineupSlot]` signature unchanged (Task 4) so `orderer._fallback` and `generate_recommendation` keep working; `LineupScoreBreakdown` shape unchanged (Task 5) so `lineup_evaluator` is untouched. The handedness penalty has ONE definition (`optimizer`), imported by `lineup_score` (or promoted to public — noted in Task 5).
- **Determinism:** no RNG/clock; dict-built distributions accumulate in deterministic insertion order; optimizer start is fixed (OBP desc, player_id asc) with canonical tie-breaks; covered by determinism/input-independence tests in Tasks 2, 3 and the existing `test_recommendation_deterministic_*`.
- **Backward-compat surface:** `_assign_batting_order` + `compute_lineup_score` signatures and `LineupScoreBreakdown`/`BattingOrderResult` shapes preserved; only values/scale change (intended). LLM batting-order layer untouched.
