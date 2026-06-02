# Phase 2 (re-scoped) — Optimal Defensive Position Assignment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the constrained-greedy defensive position assignment with an exact, deterministic **optimal** assignment (max-weight bipartite matching), so the recommended lineup maximizes total per-position hitter value.

**Architecture:** Swap the internals of one function — `select_and_assign_positions` in `app/lineup_model/recommendation.py` — from greedy fill to a dependency-free exact solver with a canonical tie-break. Signature, return type (`dict[Position, HitterStats]`), and the infeasible-pool `ValueError` contract are unchanged (drop-in). `generate_recommendation`, `_assign_batting_order`, `compute_player_score`, and `compute_lineup_score` are untouched. No new dependency, no migration.

**Tech Stack:** Python 3.13 (stdlib only — no scipy/numpy in this repo), pytest, uv. Run from `apps/api`.

**Context (why):** `select_and_assign_positions` (`recommendation.py:138-159`) fills positions in fixed order (`_POSITIONS_TO_FILL` = C,1B,2B,3B,SS,LF,CF,RF,DH), greedily taking the highest-scoring eligible player per position (`_best_player_for_position`, `:56-93`). Filling C first can consume a player worth far more elsewhere, stranding later positions and yielding a **lower total than optimal**. This is the assignment problem; we solve it exactly. The score of player `p` at position `q` is `compute_player_score(p, q, opp_handedness).total_score`, or **forbidden** when that returns `None` (ineligible). See spec `docs/superpowers/specs/2026-06-02-phase2-optimal-position-assignment-design.md`. (Phase 2 was re-scoped here from park factors, whose per-hitter KBO data was verified not to exist — see the superseded park spec.)

**Determinism (architecture invariant):** `lineup_model/` must stay deterministic; the recommended-lineup `output_hash` must be reproducible. The assignment problem can have multiple equal-total optima, so we pin a **canonical** one: among all assignments achieving the max total score, choose the one whose tuple of assigned `player_id`s, read in `_POSITIONS_TO_FILL` order, is lexicographically smallest. `output_hash` values shift wherever optimal ≠ greedy (expected for an algorithm improvement, as when `matchup` was added in Phase 1) and remain reproducible.

---

## File Structure

- `app/lineup_model/recommendation.py` — MODIFY: replace `select_and_assign_positions` internals with the exact solver; REMOVE the now-unused `_best_player_for_position` (vulture/pre-commit flags dead code); update the module docstring (Strategy section) to describe optimal assignment + canonical tie-break. Keep `_POSITIONS_TO_FILL`, signatures, and the `ValueError` contract.
- `tests/test_recommendation.py` — ADD: optimality (greedy-suboptimal pool), canonical tie-break, and `output_hash` stability tests. Existing tests must still pass (the single-eligibility `_make_pool` has exactly one valid assignment, so optimal == greedy there).

---

## Task 1: Replace greedy with an exact, deterministic optimal assignment

**Files:**
- Modify: `app/lineup_model/recommendation.py`
- Test: `tests/test_recommendation.py`

- [ ] **Step 1: Write the failing optimality test**

Append to `tests/test_recommendation.py` (it already imports `select_and_assign_positions`, `HitterStats`, `Handedness`, `Position`). This pool makes greedy provably suboptimal: a strong player **A** is eligible at both 1B (primary) and C (secondary); **B** is C-only; **W** is 1B-only and weak. Greedy fills C first, pulls A to C (A's offense dwarfs the ~0.02 position term), then strands 1B with the weak W. Optimal puts A at his primary 1B and B at C — a strictly higher total.

```python
def _single_pos(player_id: int, position: Position, ops: float) -> HitterStats:
    """A hitter eligible only at `position` (primary), with offense scaled by ops."""
    return HitterStats(
        player_id=player_id,
        handedness=Handedness.RIGHT,
        ops=ops,
        obp=ops - 0.40,
        slg=ops - 0.20,
        primary_position=position,
        starts_last_5_games=3,
    )


def test_assignment_is_optimal_not_greedy() -> None:
    """Greedy fills C first and strands 1B; the optimal solver keeps A at 1B."""
    a = HitterStats(  # strong, eligible 1B (primary) + C (secondary)
        player_id=1,
        handedness=Handedness.RIGHT,
        ops=0.950,
        obp=0.420,
        slg=0.560,
        primary_position=Position.FIRST,
        secondary_positions={Position.C},
        starts_last_5_games=3,
    )
    b = _single_pos(2, Position.C, 0.780)       # C-only, moderate
    w = _single_pos(3, Position.FIRST, 0.600)   # 1B-only, weak
    fillers = [
        _single_pos(10, Position.SECOND, 0.700),
        _single_pos(11, Position.THIRD, 0.700),
        _single_pos(12, Position.SHORT, 0.700),
        _single_pos(13, Position.LEFT, 0.700),
        _single_pos(14, Position.CENTER, 0.700),
        _single_pos(15, Position.RIGHT, 0.700),
        _single_pos(16, Position.DH, 0.700),
    ]
    pool = [a, b, w, *fillers]

    assigned = select_and_assign_positions(pool, Handedness.RIGHT)

    # Optimal: A at his primary 1B, B at C. (Greedy would put A at C, W at 1B.)
    assert assigned[Position.FIRST].player_id == 1
    assert assigned[Position.C].player_id == 2
    assert 3 not in {s.player_id for s in assigned.values()}  # weak W left out
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_recommendation.py::test_assignment_is_optimal_not_greedy -v`
Expected: FAIL — greedy puts player 1 (A) at C, so `assigned[Position.FIRST].player_id` is 3 (W), not 1.

- [ ] **Step 3: Write the optimal solver**

In `app/lineup_model/recommendation.py`:

(a) Add a module-level sentinel near the top (after imports):

```python
_MISSING = object()
```

(b) Replace the entire body of `select_and_assign_positions` with the exact solver (keep the signature):

```python
def select_and_assign_positions(
    eligible_players: list[HitterStats],
    opp_handedness: Handedness,
) -> dict[Position, HitterStats]:
    """Assign the 9 defensive positions to maximise total per-position score.

    Solves the assignment problem exactly: a maximum-weight matching of distinct
    players to ``_POSITIONS_TO_FILL``, where a (player, position) pair is
    forbidden when ``compute_player_score`` returns None. Among equally optimal
    assignments the canonical one is chosen — the tuple of assigned player_ids,
    read in ``_POSITIONS_TO_FILL`` order, that is lexicographically smallest —
    so the result is a pure, deterministic function of the inputs.

    Complexity is O(positions * 2^P * P) with memoisation, where P is the pool
    size; available-hitter pools are small (~13-16), so this is trivially fast.

    Args:
        eligible_players: Pool of available hitters.
        opp_handedness: Opposing starter's handedness.

    Returns:
        Mapping of position to the assigned HitterStats (9 entries).

    Raises:
        ValueError: If the 9 positions cannot be filled with distinct eligible
            players from the pool.
    """
    # Canonical player order: ascending player_id. Bit i corresponds to pool[i].
    pool = sorted(eligible_players, key=lambda s: s.player_id)
    n = len(pool)

    # score[i][k] = total score of pool[i] at _POSITIONS_TO_FILL[k], or None
    # (forbidden cell) when the player is ineligible for that position.
    score: list[list[float | None]] = []
    for stats in pool:
        row: list[float | None] = []
        for pos in _POSITIONS_TO_FILL:
            breakdown = compute_player_score(stats, pos, opp_handedness)
            row.append(breakdown.total_score if breakdown is not None else None)
        score.append(row)

    # Precise message for the common infeasible case (a position no one can play).
    for k, pos in enumerate(_POSITIONS_TO_FILL):
        if all(score[i][k] is None for i in range(n)):
            raise ValueError(
                f"Cannot fill position {pos}: no eligible player remaining in pool."
            )

    num_positions = len(_POSITIONS_TO_FILL)
    memo: dict[tuple[int, int], tuple[float, tuple[int, ...]] | None] = {}

    def solve(k: int, used: int) -> tuple[float, tuple[int, ...]] | None:
        """Best (total, player_id tuple) for positions k..end given used players."""
        if k == num_positions:
            return (0.0, ())
        key = (k, used)
        cached = memo.get(key, _MISSING)
        if cached is not _MISSING:
            return cached  # type: ignore[return-value]
        best: tuple[float, tuple[int, ...]] | None = None
        for i in range(n):  # ascending player_id -> canonical tie-break
            if used & (1 << i):
                continue
            cell = score[i][k]
            if cell is None:
                continue
            sub = solve(k + 1, used | (1 << i))
            if sub is None:
                continue
            cand = (cell + sub[0], (pool[i].player_id, *sub[1]))
            if (
                best is None
                or cand[0] > best[0]
                or (cand[0] == best[0] and cand[1] < best[1])
            ):
                best = cand
        memo[key] = best
        return best

    result = solve(0, 0)
    if result is None:
        raise ValueError(
            "Cannot fill all defensive positions: no assignment of distinct "
            "eligible players covers every position."
        )
    _, player_ids = result
    by_id = {stats.player_id: stats for stats in pool}
    return {pos: by_id[pid] for pos, pid in zip(_POSITIONS_TO_FILL, player_ids, strict=True)}
```

(c) DELETE the `_best_player_for_position` function (`recommendation.py:56-93`) — it is now unused and vulture will fail pre-commit otherwise.

(d) Update the module docstring's "Strategy" section (`recommendation.py:3-25`): replace the greedy description (item 1 and the "greedy approach is O(positions × players)… A future version may enumerate permutations." paragraph) with an optimal-assignment description, e.g.:

```
1. Assign the 9 defensive positions by solving the assignment problem exactly:
   a maximum-weight matching of distinct eligible players to positions, where
   a player's weight at a position is compute_player_score(...).total_score and
   ineligible (None-scoring) pairs are forbidden. Equal-total optima are broken
   canonically (lexicographically smallest assigned-player_id tuple in position
   order) so the result is deterministic.
```
Leave items 2-3 (batting order, breakdown) intact.

- [ ] **Step 4: Run the optimality test + the existing recommendation tests**

Run: `uv run pytest tests/test_recommendation.py -v`
Expected: PASS — `test_assignment_is_optimal_not_greedy` passes, AND every pre-existing test still passes (`_make_pool` is single-eligibility → exactly one valid assignment → optimal == greedy; determinism/`different_player_id_order`/`catcher_ineligible`/`impossible_position`/`9_distinct`/`1_to_9` all hold; the no-catcher case still raises `ValueError` matching "C" via the precise per-position message).

- [ ] **Step 5: Add the canonical tie-break test**

Append to `tests/test_recommendation.py`. (`output_hash`/run-to-run determinism is already covered by the existing `test_recommendation_is_deterministic` and `test_recommendation_deterministic_different_player_id_order`; this adds the missing case — choosing canonically *among equal-total optima*.) The test uses two **identical** players both eligible at 2B (primary) + SS (secondary); swapping them yields the same total, so the canonical rule must place the smaller player_id at the earlier position (2B precedes SS in `_POSITIONS_TO_FILL`).

```python
def test_canonical_tiebreak_places_smaller_id_at_earlier_position() -> None:
    """Equal-total optima resolve to the lexicographically smallest assignment."""
    def cross(player_id: int) -> HitterStats:
        return HitterStats(
            player_id=player_id,
            handedness=Handedness.RIGHT,
            ops=0.800,
            obp=0.360,
            slg=0.440,
            primary_position=Position.SECOND,
            secondary_positions={Position.SHORT},
            starts_last_5_games=3,
        )

    p, q = cross(20), cross(10)  # identical except id; pass in non-sorted order
    fillers = [
        _single_pos(1, Position.C, 0.700),
        _single_pos(2, Position.FIRST, 0.700),
        _single_pos(3, Position.THIRD, 0.700),
        _single_pos(4, Position.LEFT, 0.700),
        _single_pos(5, Position.CENTER, 0.700),
        _single_pos(6, Position.RIGHT, 0.700),
        _single_pos(7, Position.DH, 0.700),
    ]
    assigned = select_and_assign_positions([p, q, *fillers], Handedness.RIGHT)

    # 2B (earlier in position order) takes the smaller id; SS takes the larger.
    assert assigned[Position.SECOND].player_id == 10
    assert assigned[Position.SHORT].player_id == 20

- [ ] **Step 6: Run the new tests**

Run: `uv run pytest tests/test_recommendation.py -v`
Expected: PASS — all tests in the file pass.

- [ ] **Step 7: Commit**

```bash
git add app/lineup_model/recommendation.py tests/test_recommendation.py
git commit -m "feat(lineup): optimal defensive position assignment (replaces greedy)"
```

Fix any pre-commit lint/type issues (notably: ensure `_best_player_for_position` is fully removed so vulture passes; mypy on the `solve` closure types); re-commit (no `--no-verify`).

---

## Task 2: Full-suite regression + eval/output_hash reconciliation

**Files:**
- Test: any test/fixture across `tests/` that asserted a greedy-specific lineup or `output_hash`.

The single-eligibility pools used by most tests have one valid assignment, so they are unaffected. But any test that drives the DB eval path (`evaluate_lineup_for_run`) or the real LG fixture **may** now produce a different recommended lineup / `output_hash` if that data has multi-position-eligible hitters. This task finds and reconciles them.

- [ ] **Step 1: Run the full suite and catalog failures**

Run: `uv run pytest -q`
Expected: either all pass, OR a small number of failures that assert a specific pre-change lineup/`output_hash`. List each failure.

- [ ] **Step 2: For each failure, confirm it is an expected greedy→optimal change (not a regression)**

For every failing assertion, verify the new value is the **optimal** assignment (higher or equal total per-position score than the old greedy lineup) — not a correctness break. A quick check: the new recommended lineup's summed per-position score must be ≥ the old one; player selection must still be 9 distinct eligible players with valid positions. If a failure is NOT explained by the greedy→optimal improvement, STOP and treat it as a real regression (do not "update" the expectation to make it green).

- [ ] **Step 3: Update the expected values**

Update each such test's expected lineup / `output_hash` to the new optimal value, with a one-line comment noting the expectation changed due to greedy→optimal assignment. For `output_hash` literals, recompute by running the specific test and reading the actual value from the failure output (do not invent a hash).

- [ ] **Step 4: Re-run the full suite**

Run: `uv run pytest -q`
Expected: all pass.

- [ ] **Step 5: Commit (only if Step 3 changed anything)**

```bash
git add tests/
git commit -m "test(lineup): reconcile expectations with optimal position assignment"
```

If Step 1 already showed all-green (no greedy-specific expectations existed), skip this commit and note it in your report.

---

## Task 3: Verify & harness

- [ ] **Step 1:** `cd apps/api && uv run pytest -q` → all pass.
- [ ] **Step 2:** `pre-commit run --all-files` → clean (ruff, mypy, bandit, **vulture** — confirms no dead `_best_player_for_position`).
- [ ] **Step 3:** `/harness-audit` → structural + semantic green; record the marker before opening a PR. The CLAUDE.md architecture invariant ("Deterministic scoring and defensive position assignment in `apps/api/app/lineup_model/` … must stay deterministic") still holds — the assignment remains a pure deterministic function; confirm the auditor agrees and that no harness doc described the assignment as "greedy" (update if it did).

---

## Self-Review notes

- **Spec coverage:** objective (max total per-position score) → Task 1 solver; determinism/canonical tie-break → Task 1 Step 5 + solver comparison rule; run-to-run/order-independent determinism → existing `test_recommendation_is_deterministic` + `test_recommendation_deterministic_different_player_id_order` (still pass); eligibility forbidden cells → solver `None` skip + existing `catcher_ineligible` test; infeasible → per-position pre-check (`ValueError`, keeps `match="C"`) + global `None` guard; greedy→optimal regression of existing expectations → Task 2; verify/harness → Task 3.
- **No placeholders:** solver and all tests are complete code; the only deliberately data-dependent step is Task 2 Step 3 (recompute changed `output_hash` from actual test output — never fabricate a hash).
- **Type consistency:** `select_and_assign_positions(eligible_players, opp_handedness) -> dict[Position, HitterStats]` unchanged; `compute_player_score(stats, position, opp_handedness)` returns a breakdown with `.total_score` or `None` (used as the forbidden-cell signal); `_single_pos`/`cross` helpers build `HitterStats` with the same fields the file's `_make_pool` uses (`player_id`, `handedness`, `ops`, `obp`, `slg`, `primary_position`, `secondary_positions`, `starts_last_5_games`).
- **Determinism:** pool sorted by `player_id`; canonical comparison (`> total`, then `< player_id tuple`) yields a unique result independent of input order — covered by `test_recommendation_deterministic_different_player_id_order` (existing) and `test_canonical_tiebreak_*` (new).
- **Backward-compat:** signature + `ValueError` contract preserved; `generate_recommendation`/`_assign_batting_order`/scoring/models/migrations untouched.
