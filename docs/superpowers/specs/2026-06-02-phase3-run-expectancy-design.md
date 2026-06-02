# Phase 3 — Run-Expectancy Lineup Model Design

**Status:** Proposed design (2026-06-02), pending user review. Third phase of the analysis-quality program (Phase 1 matchup/clutch PR #47; Phase 2 optimal position assignment PR #50). This is the largest and most determinism-sensitive phase.

**Goal:** Replace the static weighted-average lineup aggregation with a deterministic **run-expectancy** model that (a) scores a lineup by its **expected runs** and (b) chooses the batting order that maximizes expected runs — capturing sequencing (table-setters ahead of sluggers, turnover) that the current per-slot-weight heuristic cannot.

**Tech Stack:** Python 3.13 (stdlib only — no numpy/scipy), pytest, uv. Run from `apps/api`.

**Decided in brainstorming:**
- **Scope:** run-expectancy becomes both the lineup score AND the deterministic batting-order optimizer (replaces the `_assign_batting_order` heuristic). The LLM batting-order layer stays an additive override; whatever order is finally used is scored by the same run-expectancy estimator (a common yardstick).
- **Model:** analytic **Markov expected-value** (no RNG → deterministic), not Monte Carlo.
- **Event rates:** derived approximately from season **OBP/SLG** + league constants (no new ingestion); the rate provider is an isolated boundary so real counting-line rates can replace it later.

---

## Architecture invariant (load-bearing)

`apps/api/app/lineup_model/` must stay deterministic; the recommended-lineup `output_hash` must be reproducible for identical inputs. Every component below is a **pure function with no randomness, wall-clock, or set/dict-ordering dependence**. `total_score` changes meaning (now ≈ expected runs, ~3–6 scale) and `output_hash` values shift accordingly — expected and reproducible, exactly as when Phase 1 `matchup` and Phase 2 optimal assignment shifted it.

Player **selection** and **defensive position assignment** are unchanged (Phase 2 optimal matching on `compute_player_score`). Phase 3 changes only the batting **order** among the chosen 9 and the lineup **score**.

---

## Components (each independently testable)

### 1. `rate_provider` — OBP/SLG → per-PA event probabilities
**File:** new `app/lineup_model/run_expectancy/rates.py`

Pure function `event_rates(obp: float, slg: float) -> EventRates` returning a probability vector over `{bb, single, double, triple, hr, out}` summing to 1.0. Derivation (documented league-average approximation; HBP folded into `bb`, SF/SH/ROE ignored):

1. `bb = _LEAGUE_BB_RATE` (per-PA walk+HBP constant) — clamped so `bb < obp`.
2. `hit = obp - bb` (H per PA), `out = 1 - obp`.
3. `ab_per_pa = 1 - bb`; `tb_per_pa = slg * ab_per_pa` (total bases per PA).
4. `extra_bases = max(0, tb_per_pa - hit)` (bases beyond a single per hit).
5. Distribute `extra_bases` across 2B/3B/HR using fixed league shares `_XB_SHARES = {double, triple, hr}` weighted by their extra-base value (2B=+1, 3B=+2, HR=+3): solve for counts so the weighted extra bases match, then `single = hit - (double + triple + hr)`, clamped to ≥ 0.
6. Renormalize the six probabilities to sum to exactly 1.0 (guards float drift and clamps).

`_LEAGUE_BB_RATE` and `_XB_SHARES` are module constants set to recent KBO-league averages (documented as approximations; pinned with concrete values in the plan). **Honesty:** these rates are approximate; accuracy improves when real per-hitter counting lines are ingested later (the `event_rates` interface is unchanged by that upgrade).

> Note (v1 scope): rates derive from **season** OBP/SLG only. Folding recent-form / handedness-matchup adjustments into an "effective OBP/SLG" before this step is a documented future refinement (out of scope for v1) — player **selection** still uses the full `compute_player_score` (recent/matchup/park/etc.), so those signals are not lost from the recommendation, only from the order/score model for now.

### 2. `run_expectancy` — ordered lineup → expected runs
**File:** new `app/lineup_model/run_expectancy/markov.py`

Pure function `expected_runs(order: tuple[EventRates, ...], innings: int = 9) -> float`. Analytic Markov chain, no sampling:

- **State:** base configuration (8: each of 1B/2B/3B occupied or not) × outs (0/1/2) = 24 transient states; reaching 3 outs ends the inning.
- **Batter pointer** carries across innings (the inning leads off with the batter after the one who made the last out / the lineup wraps 1→…→9→1).
- **Per-PA transition** for the batter due up, applying that batter's `EventRates` with a fixed deterministic base-advancement model (documented assumption; no double plays, no sac, runners advance exactly by the hit value):
  - `out`: outs += 1.
  - `bb`: batter to 1B; force-advance only runners that are forced.
  - `single`/`double`/`triple`: batter advances 1/2/3 bases; each existing runner advances the same number of bases; runners reaching home (base ≥ 4) score.
  - `hr`: batter + all runners score.
- **Computation:** propagate a probability distribution over (base-out, batter-pointer) states through PAs, accumulating expected runs (probability-weighted runners crossing home) until the inning's 3-out absorption; carry the batter pointer to the next inning with bases empty; sum over `innings`. Fully analytic (expected value), so identical inputs always yield the identical float.

### 3. `order_optimizer` — choose the run-maximizing order
**File:** new `app/lineup_model/run_expectancy/optimizer.py`

`optimize_order(players: list[HitterStats], opp_handedness) -> tuple[LineupSlot-order]`. 9! = 362 880 permutations is too slow to brute-force in pure Python, so use **deterministic best-improvement local search**:

- Start from a fixed, deterministic order: season OBP descending, ties by ascending `player_id`.
- Objective `J(order) = expected_runs(order) - handedness_penalty(order)`, where `handedness_penalty` charges a small **run-unit** cost for long same-handedness streaks (5 consecutive → `_HAND_PEN_5`, 6+ → `_HAND_PEN_6`; constants in run units, documented).
- Repeatedly evaluate all C(9,2)=36 pairwise swaps; apply the single swap with the greatest `J` improvement; stop when no swap improves `J`. Ties between equal-`J` swaps are broken canonically (prefer the resulting order whose `(batting_order → player_id)` tuple is lexicographically smallest), so the result is order-independent and reproducible.
- This yields a **deterministic local optimum** (honestly labeled — global 9! optimization is out of scope for performance). Each recommendation costs ≈ tens–hundreds of `expected_runs` evaluations (sub-second).

### 4. Integration
**Files:** `app/lineup_model/lineup_score.py`, `app/lineup_model/recommendation.py`

- `compute_lineup_score(slots, stats_by_player, opp_handedness)` — replace the weighted-average block (and `slot_emphasis_adjustment` / `BATTING_ORDER_WEIGHTS` usage) with `total_score = expected_runs(<rates for slots in batting order>)`. Keep the `LineupScoreBreakdown` shape; `weighted_player_score` is repurposed to carry the raw expected-runs (or renamed — decided in the plan); `reasons` explain the run estimate. **Drop** `position_completeness_adjustment` (always satisfied for a valid 9 → a degenerate constant) and move handedness into the optimizer objective (§3). Document the scale change.
- `generate_recommendation` — replace the `_assign_batting_order` heuristic call with `optimize_order(...)`; keep Phase-2 `select_and_assign_positions` for the 9-player/position choice unchanged. `_assign_batting_order`, `BATTING_ORDER_WEIGHTS`, `_SLOT_*_BOOST`, and `slot_emphasis_adjustment` become dead → remove (vulture).
- The LLM batting-order layer (`batting_order/`) is untouched; when enabled it overrides the order, and `compute_lineup_score` then reports the expected runs of the LLM order.

---

## Determinism / safety
- Pure, total, deterministic functions; no RNG, clocks, or iteration-order dependence. Same input → same order → same `expected_runs` float → same `output_hash`.
- `total_score` rescales to expected runs; `output_hash` values change (reproducible). Selection/position layer unaffected.
- Graceful inputs: `event_rates` clamps/renormalizes degenerate OBP/SLG; `expected_runs` handles any valid 9-rate order; `optimize_order` always returns a valid permutation.

## Testing
- `event_rates`: probabilities sum to 1.0; monotonic (higher OBP → higher on-base, higher SLG → more total bases); clamps for extreme/degenerate inputs.
- `expected_runs`: known bounds (all-out order → 0 runs; all-HR order → analytic value); **sequencing** (high-OBP-then-high-SLG order scores higher than its reverse); determinism (repeat + input-order independence); `innings` scaling.
- `optimize_order`: converges to a local optimum (no improving swap remains); deterministic / start-independent in result; handedness penalty actually avoids long same-handed streaks when it changes `J`.
- Integration: `compute_lineup_score` returns expected-runs-scaled totals (update existing expectation tests; recompute any persisted `output_hash` from actual runs, never fabricate); `generate_recommendation` produces a valid 9, distinct players, batting_order 1–9; full-suite regression.

## Out of scope (explicit)
- Real per-hitter counting-line ingestion (future; swaps the `rate_provider` internals only).
- Recent-form / handedness-matchup-adjusted effective rates (future refinement).
- Global 9! exact order optimization (performance); pitcher/defense/baserunning modeling; Monte Carlo; double plays / sacrifices in the advancement model.

## Key files
- Add: `app/lineup_model/run_expectancy/{rates.py, markov.py, optimizer.py, __init__.py}` (plus a small `EventRates` type).
- Modify: `app/lineup_model/lineup_score.py`, `app/lineup_model/recommendation.py` (remove the now-dead heuristic order + slot-weight machinery).
- Tests: new test modules per component + updates to existing recommendation/lineup-score tests for the scale change.
