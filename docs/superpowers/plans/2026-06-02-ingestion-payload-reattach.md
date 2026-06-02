# Ingestion Payload Re-attach (dedup vs run_id) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `save_raw_payload` re-attach a deduplicated raw payload to the current ingestion run, so a same-day second ingestion run's normalizers (which filter by `ingestion_run_id`) can see payloads whose body is byte-identical to an earlier run's.

**Architecture:** One-line-ish fix in `app/ingestion/raw_store.py`: on an idempotency hit, update the existing row's `ingestion_run_id` (and `fetched_at`) to the incoming payload's before returning `(existing, False)`. Plus a unit test for cross-run re-attach and a pipeline regression test proving two same-day game ingests both yield populated stat snapshots.

**Tech Stack:** Python 3.13, SQLAlchemy, pytest, uv. Run from `apps/api`.

**Context (why):** `daily_pipeline` fetches **date-independent** URLs per roster hitter — Naver `players/kbo/{code}/playerend-record` and KBO `HitterDetail/Situation|Basic.aspx?playerId={code}`. `save_raw_payload` is idempotent on `(source_name, source_url, payload_hash)` and currently returns the existing row **without changing its `ingestion_run_id`** (`raw_store.py:42-43`). On the same calendar day the bodies are byte-identical, so the second game's run re-fetches them, gets the existing rows (still attributed to the FIRST run), and `normalize_player_stats` / `normalize_kbo_hitter_splits` — which filter `RawIngestionPayload.ingestion_run_id == run_id` — find **nothing** → empty stat snapshot → pregame eval fails. (Discovered 2026-06-02 during Phase-1 full re-ingest; see memory `ingestion-dedup-runid-bug`.) Normal daily operation never hits this because the season body changes daily (different hash → fresh payload per run); it only bites same-day multi-game (re-)ingestion, which we now do for verification/backfill.

**Why re-attach (not a link table):** raw payloads are replay/audit copies of an identical body; "which run owns it" is not load-bearing, and re-attaching to the most-recent fetcher is exactly what each run's normalizer needs (the normalizer runs right after that run's own collection). A run↔payload link table would be cleaner data modeling but is a much larger change for no functional gain here (YAGNI). The trade-off: the payload's `ingestion_run_id` becomes "last run that fetched it" rather than "first" — acceptable for this single-team MVP.

**Determinism / safety:** No behavior change for normal single-game-per-day runs (no dedup hit). The existing same-run idempotency contract is preserved (re-attaching to the same run id is a no-op). Snapshots are independent of raw payloads, so nothing already built is affected.

---

## File Structure

- `app/ingestion/raw_store.py` — MODIFY `save_raw_payload`: re-attach on idempotency hit.
- `tests/test_raw_ingestion_store.py` — ADD a cross-run re-attach test (existing idempotency/duplicate tests must still pass).
- `tests/test_pipeline_jobs.py` — ADD a regression test: two same-day daily-pipeline runs (different game dates, identical player bodies) both produce non-empty stat snapshots.

---

## Task 1: Re-attach deduplicated payloads to the current run

**Files:**
- Modify: `app/ingestion/raw_store.py:42-43`
- Test: `tests/test_raw_ingestion_store.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_raw_ingestion_store.py` (it already has a `session` fixture and a `_make_payload`-style helper — read the file's top for the exact helper name/signature; the existing tests build a `RawPayloadCreate` with an `ingestion_run_id`. Reuse that helper, passing a different `ingestion_run_id` for the second save. You will need TWO `IngestionRun` rows; create the second the same way the fixture/helpers create the first.):

```python
def test_duplicate_reattaches_to_current_run(session: Session) -> None:
    """A byte-identical payload saved under a second run re-attaches to that
    run (so the second run's normalizers, which filter by ingestion_run_id,
    can see it). created is still False; no new row is inserted."""
    # First run saves the payload.
    run_a = _make_run(session)  # adapt to the file's run-creation helper
    p_a = _make_payload(ingestion_run_id=run_a)  # identical body/url/source
    row_a, created_a = save_raw_payload(session, p_a)
    assert created_a is True

    # Second run re-fetches the byte-identical payload (same source/url/body).
    run_b = _make_run(session)
    p_b = _make_payload(ingestion_run_id=run_b)
    row_b, created_b = save_raw_payload(session, p_b)

    assert created_b is False            # no new row
    assert row_b.id == row_a.id          # same physical row
    assert row_b.ingestion_run_id == run_b   # re-attached to the current run
```

> NOTE: read `tests/test_raw_ingestion_store.py` lines ~60-115 for the real helper names. The existing `test_save_is_idempotent_on_duplicate` saves twice under the SAME run; this new test differs by using two runs. If the file has no `_make_run` helper, create the second `IngestionRun` inline exactly as the `session`/`run_id` fixture does (look at how `run_id` is produced).

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_raw_ingestion_store.py::test_duplicate_reattaches_to_current_run -v`
Expected: FAIL — `row_b.ingestion_run_id` equals run_a, not run_b (current code returns the existing row unchanged).

- [ ] **Step 3: Write minimal implementation**

In `app/ingestion/raw_store.py`, replace the idempotency-hit branch:

```python
    if existing is not None:
        return existing, False
```

with:

```python
    if existing is not None:
        # Re-attach the deduplicated payload to the current run so this run's
        # normalizers (which filter by ingestion_run_id) can see it. Bodies are
        # byte-identical, so ownership is not load-bearing; the most-recent
        # fetcher is the run that needs it. No-op when re-saving within a run.
        if existing.ingestion_run_id != payload.ingestion_run_id:
            existing.ingestion_run_id = payload.ingestion_run_id
            existing.fetched_at = payload.fetched_at
            session.flush()
        return existing, False
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_raw_ingestion_store.py -v`
Expected: PASS — the new test passes AND every existing test in the file still passes (notably `test_save_is_idempotent_on_duplicate`, which saves twice under the same run → the `!=` guard makes re-attach a no-op, row id unchanged, `created=False`).

- [ ] **Step 5: Commit**

```bash
git add app/ingestion/raw_store.py tests/test_raw_ingestion_store.py
git commit -m "fix(ingestion): re-attach deduplicated payloads to the current run"
```

Fix any pre-commit lint/type issues; re-commit (no `--no-verify`).

---

## Task 2: Pipeline regression test — two same-day game ingests both populate

**Files:**
- Test: `tests/test_pipeline_jobs.py`

This proves the bug is fixed at the level it actually manifested: two daily-pipeline runs on the same calendar day (different game dates, identical per-player season bodies) must BOTH produce non-empty stat snapshots.

- [ ] **Step 1: Write the failing test**

Read `tests/test_pipeline_jobs.py` first — reuse its existing mock-HttpClient harness and daily-pipeline invocation (the file already drives `run_daily_pipeline`/`run_full_pipeline` with a mock that maps request URLs to canned bodies, and seeds teams/roster). Add a test that runs the daily pipeline for TWO different game dates within one test (same roster, mock returns the SAME body for each player's date-independent `playerend-record` URL on both runs), then asserts BOTH games' stat snapshots have rows.

```python
def test_two_same_day_game_ingests_both_populate(...):
    """Re-ingesting a second game the same day must not yield an empty snapshot.
    The per-player season payloads are byte-identical across the two runs; the
    re-attach fix lets the second run's normalize see them."""
    # Arrange: roster seeded; mock HttpClient returns identical season bodies
    # for each player's playerend-record URL regardless of game date (match the
    # file's existing mock-routing approach).
    # Act: run the daily pipeline for game date 1, then game date 2.
    result1 = <run daily pipeline for date 1>
    result2 = <run daily pipeline for date 2>
    # Assert: both runs created a stat snapshot WITH player rows.
    assert result1.stat_snapshots_created == 1
    assert result2.stat_snapshots_created == 1
    # And both snapshots have non-zero PlayerStatSnapshotRow counts (query the
    # snapshot for each run's ingestion_run_id and assert rows > 0).
```

> Fill the arrange/act with the file's real helpers (mock client, seeding, pipeline entrypoint, result type — `DailyPipelineResult` or similar with `stat_snapshots_created`). If the existing mock keys responses by exact URL, ensure both game dates resolve the SAME `playerend-record` URL per player (they do — the URL has no date), so the second run dedups. The assertion that matters: the second run's snapshot has player rows (pre-fix it would be 0).

- [ ] **Step 2: Run test to verify it fails (on pre-Task-1 code path) / passes (post-Task-1)**

Run: `uv run pytest tests/test_pipeline_jobs.py::test_two_same_day_game_ingests_both_populate -v`
Expected: With Task 1 applied, PASS. To confirm it genuinely guards the bug, temporarily revert Task 1's re-attach (or `git stash` it), run the test, observe the second snapshot has 0 rows (FAIL), then restore. Note the result in your report.

- [ ] **Step 3: (no new impl — Task 1 is the fix)**

If the test fails with Task 1 applied, the mock isn't actually deduping (the two runs got different bodies/URLs) — adjust the mock so the per-player `playerend-record` URL+body is identical across both runs, which is the real-world condition.

- [ ] **Step 4: Run full suite for regression**

Run: `uv run pytest -q`
Expected: all pass (~440).

- [ ] **Step 5: Commit**

```bash
git add tests/test_pipeline_jobs.py
git commit -m "test(pipeline): guard same-day two-game ingest both populate snapshots"
```

Fix any lint/type issues; re-commit (no `--no-verify`).

---

## Task 3: Verify & harness

- [ ] **Step 1:** `cd apps/api && uv run pytest -q` → all pass.
- [ ] **Step 2:** `pre-commit run --all-files` → clean.
- [ ] **Step 3:** `/harness-audit` → structural + semantic green; record the marker before opening a PR. (No harness paths/architecture change expected; this is a localized fix.)
- [ ] **Step 4 (optional live confirm):** the manual same-day re-ingest dance (clearing payloads between dates) documented in memory `ingestion-dedup-runid-bug` should no longer be necessary — a plain sequential `kbo-lab run --date A` then `--date B` on the same day now populates both. Not required for merge.

---

## Self-Review notes

- **Coverage:** the root cause (dedup hit not re-attaching) → Task 1; the manifest-level failure (empty 2nd-game snapshot) → Task 2 regression test; verification/harness → Task 3.
- **No placeholders:** Task 1 has complete before/after code and the exact test. Task 2's arrange/act are intentionally described against the existing `test_pipeline_jobs.py` harness rather than inventing a parallel mock — the implementer must reuse that file's real helpers (named there), which is why those lines reference "the file's helpers" instead of fabricating signatures; the ASSERTIONS (both `stat_snapshots_created == 1` and both snapshots have rows > 0) are concrete.
- **Backward-compat:** the `!=` guard preserves the same-run idempotency contract (existing `test_save_is_idempotent_on_duplicate` passes unchanged); normal daily op has no dedup hit so behavior is identical.
- **Type consistency:** `save_raw_payload` signature/return (`tuple[RawIngestionPayload, bool]`) unchanged; only the existing-row branch mutates `ingestion_run_id`/`fetched_at`.
