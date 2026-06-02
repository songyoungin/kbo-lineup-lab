# Phase 2 (re-scoped) — Optimal Defensive Position Assignment

**Status:** Proposed design (2026-06-02), pending user review. Re-scope of Phase 2 after the park-factor data source was found not to exist (see `2026-06-02-phase2-park-factor-design.md` for that finding).

**Goal:** Replace the constrained-**greedy** defensive position assignment with an **optimal** one (maximum-weight assignment), so the recommended lineup maximizes total per-position hitter value instead of locking in early, position-order-biased picks. Pure analysis-quality win using only data already in the model — no new data source, no estimated coefficients.

**Architecture:** Swap the internals of one function, `select_and_assign_positions` (`app/lineup_model/recommendation.py`), from greedy fill to an exact, **dependency-free, deterministic** max-weight bipartite matching over the 9 defensive positions. Signature, return type (`dict[Position, HitterStats]`), and the infeasible-pool `ValueError` contract are unchanged — a drop-in replacement. `generate_recommendation` and `_assign_batting_order` are untouched.

**Tech Stack:** Python 3.13 (stdlib only — no scipy/numpy in this repo), pytest, uv. Run from `apps/api`.

---

## Why greedy is suboptimal (the actual defect)

`select_and_assign_positions` fills positions in a fixed order (`_POSITIONS_TO_FILL` = C, 1B, 2B, 3B, SS, LF, CF, RF, DH) and assigns each the highest-scoring eligible player not yet used (`recommendation.py:96-158`, `_best_player_for_position`). Filling C first can consume a player who would have contributed far more at another position, leaving later positions with weaker fits and a **lower total lineup value than the optimal assignment**. The module docstring already concedes the greedy approach is only "near-optimal" for small pools. This is a classic **assignment problem**, solvable exactly.

## Objective (what we optimize)

Maximize the **sum over the 9 slots** of `compute_player_score(stats, position, opp_handedness).total_score`, assigning 9 **distinct** players to the 9 positions, where a (player, position) pair is **forbidden** when `compute_player_score(...)` returns `None` (ineligible position). This is exactly the quantity the greedy already scores on, optimized globally instead of locally.

**Known limitation (documented, accepted):** the set-level adjustments in `compute_lineup_score` (position-completeness, handedness-balance) are not folded into the matching weights — they depend on the whole chosen set, not decomposable per (player, position). They are still applied afterward by `compute_lineup_score`, exactly as today. The matching optimizes the same per-position granularity the greedy used; this is an apples-to-apples improvement, not a new objective.

## Determinism (architecture invariant — load-bearing)

`lineup_model/` must stay deterministic; the recommended-lineup `output_hash` must be reproducible for identical inputs. The assignment problem can have **multiple optimal solutions of equal total score**, so an arbitrary solver would be non-deterministic. We pin a **canonical optimum**:

> Among all assignments achieving the maximum total score, choose the one whose tuple of assigned `player_id`s, read in fixed position order (C, 1B, 2B, 3B, SS, LF, CF, RF, DH), is **lexicographically smallest**.

This makes the result a pure, total function of the inputs. `output_hash` for a given snapshot becomes a new stable value (it changes from the greedy baseline wherever the optimal assignment differs — expected for an algorithm improvement, same as when `matchup` was added in Phase 1).

## Algorithm (dependency-free, exact, canonical)

Exact search over the 9 positions in `_POSITIONS_TO_FILL` order with memoization, returning the canonical optimum:

- State: `(position_index, used_player_bitmask)`. `used_player_bitmask` is over the eligible-player pool indexed by **ascending player_id** (so the bit order encodes the canonical tie-break).
- Transition at `position_index`: for each pool player not in `used` and **eligible** for that position (score not `None`), recurse to `position_index + 1` with that player added; value = `score(player, pos) + best_suffix`.
- Result per state: the best `(total_score, assignment_tuple)` where ties on `total_score` are broken by the lexicographically smallest `assignment_tuple` of player_ids (positions filled so far + suffix). Because candidates are tried in ascending player_id order and positions in fixed order, a strict "`>` on score, then `<` on the player_id tuple" comparison yields the canonical optimum.
- Infeasible (no way to fill all 9 with distinct eligible players) → raise `ValueError` with the same message style as today.

Complexity: `O(9 × 2^P × P)` with memoization, where `P` = pool size. Real available-hitter pools are ~13–16 (`2^16 = 65 536`), so this is trivially fast. **Guard:** if `P` exceeds a safe cap (e.g. 22), fall back is unnecessary in practice — document the assumption and add an assertion/log; do NOT silently degrade. (A roster of available hitters never approaches this.)

> Implementation note: this is a small, self-contained exact solver — preferred over hand-rolling the Hungarian algorithm, which needs its own canonical-tie handling. Keep it in `recommendation.py` (or a sibling `position_assignment.py` if it grows past ~60 lines) with a focused, well-documented function.

## Components / file structure

- **Modify** `app/lineup_model/recommendation.py`:
  - Replace the body of `select_and_assign_positions(eligible_players, opp_handedness) -> dict[Position, HitterStats]` with the optimal solver. Keep `_POSITIONS_TO_FILL`, the signature, the return type, and the `ValueError` contract.
  - `_best_player_for_position` becomes unused → remove it (vulture/pre-commit will flag dead code otherwise).
  - Update the module docstring (it currently describes the greedy approach) to describe optimal assignment + the canonical tie-break.
- **No** changes to `generate_recommendation`, `_assign_batting_order`, `compute_player_score`, `compute_lineup_score`, types, or DB models. No migration.
- **Tests** `tests/` (extend the existing recommendation test module):
  - **Optimality:** a hand-built pool where greedy is provably suboptimal (e.g. the best catcher is also the only strong SS; greedy takes him at C and strands SS) → assert the optimal total strictly exceeds the greedy total and equals the known hand-computed optimum.
  - **Determinism / canonical tie-break:** a pool with a deliberate tie → run twice, assert identical assignment; assert the canonical (lexicographically-smallest player_id tuple) winner.
  - **Eligibility respected:** a forbidden (player, position) (score `None`) is never assigned.
  - **Infeasible pool:** fewer eligible players than positions, or a position no one can fill → `ValueError` (unchanged contract).
  - **output_hash stability:** same input twice → identical `output_hash`.
  - **Existing recommendation/eval regression:** update any test/fixture that asserted a specific greedy lineup or `output_hash`; the new expectations are the optimal lineups. Document which expectations changed and why (greedy→optimal), and re-verify `kbo-lab` eval flows.

## Determinism / safety summary

- Pure, total, deterministic function of the player pool + scores + opp handedness.
- `output_hash` values shift where optimal ≠ greedy (expected, deterministic, reproducible).
- No new dependency, no network, no new data, no migration.
- Same public contract (signature + `ValueError`), so callers are unaffected.
- The LLM batting-order layer is downstream and unaffected (it reorders the chosen 9; selection quality only improves).

## Out of scope (explicit)

- Folding `compute_lineup_score`'s set-level adjustments into the matching weights (would change the objective; not this change).
- Replacing the weighted-average score aggregation with run-expectancy simulation → **Phase 3**.
- Park factors → blocked by data availability (see the superseded spec); revisit only if a credible KBO park-factor source appears.
- Batting-order logic (`_assign_batting_order`) — untouched.

## Key files
- Modify: `app/lineup_model/recommendation.py` (assignment internals + docstring; remove dead `_best_player_for_position`).
- Tests: the existing recommendation test module (optimality, determinism, eligibility, infeasible, output_hash, regression).
