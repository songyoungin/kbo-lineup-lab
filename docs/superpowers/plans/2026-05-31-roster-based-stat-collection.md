# Roster-Based Stat Collection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expand the lineup recommender's candidate pool from "the 9 announced starters" to "every hitter available for that game" by collecting season stats for the full bench, not just the starting nine.

**Architecture:** The Naver preview payload we already fetch (`homeTeamLineUp`) carries `fullLineUp` (1 pitcher + 9 batters) **and** `batterCandidate` (bench hitters). We (1) extend the lineup normalizer to upsert `batterCandidate` bench hitters as `Player` rows, and (2) change the daily pipeline's season-stat collection to iterate over **all team hitters** (`Player.position != 'P'`) instead of only lineup batters. No new HTTP endpoint, no schema change. The evaluator already pools by `team_id` + stat snapshot, so a fuller stat snapshot automatically widens the recommendation pool.

**Tech Stack:** Python 3.10+, SQLAlchemy, pytest, httpx MockTransport. Source data: Naver Sports api-gw preview JSON.

---

## Context for the implementer (read first)

**The problem this fixes.** Today the candidate pool equals the announced lineup, so the "recommendation" can only reshuffle the same 9 players' batting order — it can never bench a starter or promote a bench bat. Root cause: `daily_pipeline._collect_lineup_player_season_stats` fetches season stats only for lineup rows with a non-null `batting_order` (the 9 starters), so the stat snapshot only ever has 9 hitters.

**Key facts established during design:**
- `Player.external_id` IS the Naver player code; `collect_player_season_stats(player_code=...)` already takes that code. So any hitter with a `Player` row can have stats fetched — no ID mapping needed.
- `normalize_player_stats` **looks up** `Player` by `external_id` and **skips** payloads with no matching player (it does not create players). So bench hitters MUST have `Player` rows before their stat payloads are normalized → Task 1 is a prerequisite of Task 2/3 producing rows.
- Field-shape difference between the two arrays (verified against `tests/fixtures/sources/naver/preview_20250514WOLG02025.json`):
  - `fullLineUp` entry: `{"playerCode","playerName","position":"<numeric>","positionName":"<korean>","hitType","batsThrows","batorder?"}` — pitcher has `position == "1"` and no `batorder`.
  - `batterCandidate` entry: `{"playerCode","playerName","pos":"<numeric>","position":"<korean word e.g. 포수>","hitType","batsThrows"}` — **numeric position is in `pos`**, and `position` is a Korean word that `to_position` does NOT recognize (returns DH). So bench entries must be mapped via `pos`, not `position`.
- `to_position` (in `app/ingestion/normalizers/_shared.py`) maps numeric tokens `"1".."9","0"` and single Korean chars to canonical `Position` values; `Position.P.value == "P"`, `Position.C.value == "C"`, `Position.CENTER.value == "CF"`, etc.
- For the 2025-05-14 fixture: 9 starters + 6 candidates, zero overlap → **15 distinct hitters**. This number drives the updated test assertions.

**Why no evaluator change is needed.** `app/services/lineup_evaluator.evaluate_lineup_for_run` already builds the pool from `PlayerStatSnapshotRow` joined to `Player` filtered by `Player.team_id == run.team_id`. Once the stat snapshot holds 15 hitters, the pool is 15 automatically.

**Scope guard.** This is LG-only MVP, consistent with the rest of the ingestion code. Do not generalize to other teams. Pitchers are excluded from the pool (hitters-only, no plate-appearance threshold — per design decision).

**Known limitation to document (not fix here).** Stat collection drives off "all `Player` rows for the team that are not pitchers." Across a long season the `Player` table accumulates every hitter ever seen, so the pool can include players no longer on the active entry. For the current single-/few-date MVP this is acceptable; a future iteration can scope the pool to the current preview's `batterCandidate` set per game date. Note this in the verification task's summary.

---

## File Structure

- `apps/api/app/ingestion/normalizers/lineup.py` — **modify**: after the `fullLineUp` loop, upsert `batterCandidate` bench hitters as `Player` rows (no snapshot rows).
- `apps/api/tests/ingestion/test_lineup_naver.py` — **modify**: add a test that bench hitters become `Player` rows without lineup rows.
- `apps/api/app/jobs/daily_pipeline.py` — **modify**: replace `_collect_lineup_player_season_stats` with `_collect_roster_player_season_stats` (iterate team hitters); rewire `run_daily_pipeline`; fix imports.
- `apps/api/tests/ingestion/test_daily_pipeline_naver.py` — **modify**: add a focused unit test for the new helper; bump end-to-end stat-row counts 9 → 15.

---

### Task 1: Upsert bench hitters (`batterCandidate`) as Player rows

**Files:**
- Modify: `apps/api/app/ingestion/normalizers/lineup.py` (insert after the `for entry in entries:` loop, before the final `session.flush()` near line 257)
- Test: `apps/api/tests/ingestion/test_lineup_naver.py`

- [ ] **Step 1: Write the failing test**

Add to `apps/api/tests/ingestion/test_lineup_naver.py` (the `Player`, `ActualLineupSnapshotRow`, `select`, `_seed_teams`, `_seed_game`, `_save_preview_payload`, `IngestionRun` symbols are already imported/defined in this file):

```python
def test_normalize_lineup_upserts_bench_hitters_without_rows(
    session: Session,
    load_source: Callable[[str], str],
) -> None:
    """batterCandidate bench hitters become Player rows but produce no lineup rows.

    Verifies the bench hitter 박동원 (code 79365) is upserted with the position
    derived from its numeric ``pos`` ("2" -> C) and handedness from hitType
    ("우투우타" -> bats R, throws R), and that it has no ActualLineupSnapshotRow.
    """
    lg, wo = _seed_teams(session)
    _seed_game(session, lg, wo)
    run = IngestionRun(source="test:lineup", status="running")
    session.add(run)
    session.flush()
    payload = _save_preview_payload(session, run, load_source)

    result = normalize_lineup(session, payload)

    bak = session.execute(
        select(Player).where(Player.external_id == "79365")
    ).scalar_one()
    assert bak.name == "박동원"
    assert bak.position == "C"  # pos="2" -> C (NOT the Korean "포수")
    assert bak.bats == "R"
    assert bak.throws == "R"

    # Bench hitter has NO lineup snapshot row (no batting order).
    bench_rows = (
        session.execute(
            select(ActualLineupSnapshotRow).where(
                ActualLineupSnapshotRow.snapshot_id == result.snapshot_id,
                ActualLineupSnapshotRow.player_id == bak.id,
            )
        )
        .scalars()
        .all()
    )
    assert bench_rows == []

    # The snapshot still contains exactly the nine starters.
    assert result.rows_created == 9
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/api && uv run pytest tests/ingestion/test_lineup_naver.py::test_normalize_lineup_upserts_bench_hitters_without_rows -v`
Expected: FAIL — `sqlalchemy.exc.NoResultFound` (no Player with external_id 79365, because `batterCandidate` is not yet processed).

- [ ] **Step 3: Write minimal implementation**

In `apps/api/app/ingestion/normalizers/lineup.py`, locate the end of the batter loop (after the `rows_created += 1` block) and BEFORE the final `session.flush()` at the end of `normalize_lineup`. Insert:

```python
    # Bench hitters (batterCandidate) are upserted as Player rows so the
    # recommender's candidate pool extends beyond the starting nine. They carry
    # no batting order, so no ActualLineupSnapshotRow is created. The numeric
    # position lives in ``pos`` here (``position`` is the Korean position word,
    # which to_position does not recognize), so it is mapped onto the
    # ``position`` key _upsert_player expects.
    for candidate in lineup_block.get("batterCandidate") or []:
        if not candidate.get("playerCode"):
            rows_skipped += 1
            needs_review_reasons.append(
                f"batterCandidate missing playerCode: {candidate!r}"
            )
            continue
        _upsert_player(
            session,
            team.id,
            {
                "playerCode": candidate.get("playerCode"),
                "playerName": candidate.get("playerName"),
                "position": candidate.get("pos"),
                "hitType": candidate.get("hitType"),
                "batsThrows": candidate.get("batsThrows"),
            },
        )
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd apps/api && uv run pytest tests/ingestion/test_lineup_naver.py -v`
Expected: PASS — the new test plus all existing lineup tests (e.g. `test_normalize_lineup_creates_snapshot_with_nine_rows` still asserts `len(players) >= 9`, which holds at 16).

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/ingestion/normalizers/lineup.py apps/api/tests/ingestion/test_lineup_naver.py
git commit -m "feat(ingestion): upsert batterCandidate bench hitters as Player rows"
```

---

### Task 2: Collect season stats for all team hitters (new helper)

**Files:**
- Modify: `apps/api/app/jobs/daily_pipeline.py` (replace `_collect_lineup_player_season_stats`; fix imports)
- Test: `apps/api/tests/ingestion/test_daily_pipeline_naver.py`

- [ ] **Step 1: Write the failing test**

Add to `apps/api/tests/ingestion/test_daily_pipeline_naver.py` (add `Player`, `IngestionRun` to the existing `from app.models...` imports, and `from app.models.player import Player` if not present; `re`, `httpx`, `HttpClient`, `Team` are already imported):

```python
def test_collect_roster_player_season_stats_covers_hitters_not_just_lineup(
    session: Session,
) -> None:
    """The roster collector fetches every team hitter and excludes pitchers."""
    from app.jobs.daily_pipeline import _collect_roster_player_season_stats
    from app.models.player import Player
    from app.models.snapshot import IngestionRun

    lg = Team(code="LG", name="LG")
    session.add(lg)
    session.flush()
    session.add_all(
        [
            Player(team_id=lg.id, external_id="100", name="hitter1", position="CF"),
            Player(team_id=lg.id, external_id="101", name="hitter2", position="DH"),
            Player(team_id=lg.id, external_id="900", name="pitcher1", position="P"),
        ]
    )
    run = IngestionRun(source="test:roster-stats", status="running")
    session.add(run)
    session.flush()

    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        match = re.search(r"/players/kbo/([^/]+)/playerend-record", str(request.url))
        assert match is not None
        seen.append(match.group(1))
        return httpx.Response(200, text="{}", headers={"content-type": "application/json"})

    http = HttpClient(
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        retry_backoff=(0.0,),
    )

    count = _collect_roster_player_season_stats(
        session, ingestion_run=run, team_id=lg.id, http=http
    )

    assert count == 2
    assert set(seen) == {"100", "101"}  # pitcher 900 excluded
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/api && uv run pytest tests/ingestion/test_daily_pipeline_naver.py::test_collect_roster_player_season_stats_covers_hitters_not_just_lineup -v`
Expected: FAIL — `ImportError: cannot import name '_collect_roster_player_season_stats'`.

- [ ] **Step 3: Write minimal implementation**

In `apps/api/app/jobs/daily_pipeline.py`:

a) Fix imports. Change the snapshot import line (currently `from app.models.snapshot import ActualLineupSnapshotRow, IngestionRun`) to:

```python
from app.lineup_model.types import Position
from app.models.snapshot import ActualLineupSnapshot, IngestionRun
```

(`ActualLineupSnapshotRow` is only used by the function being removed, so it is dropped; `Player` is already imported.)

b) Replace the entire `_collect_lineup_player_season_stats` function with:

```python
def _collect_roster_player_season_stats(
    session: Session,
    *,
    ingestion_run: IngestionRun,
    team_id: int,
    http: HttpClient,
) -> int:
    """Fetch season stats for every hitter on the team's roster.

    Drives off ``Player`` rows (excluding pitchers, ``position == 'P'``) rather
    than the announced lineup, so the recommender's candidate pool includes
    bench hitters — not just the starting nine. Each hitter's record endpoint is
    hit once; the production client's per-host throttle keeps the GETs polite.

    Args:
        session: Active SQLAlchemy session. Caller controls the transaction.
        ingestion_run: Parent ingestion run the fetched payloads belong to.
        team_id: Team whose hitters to fetch (LG in the single-team MVP).
        http: Configured HttpClient. Inject a mock client in tests.

    Returns:
        Number of hitters whose season stats were fetched.
    """
    codes = session.execute(
        select(Player.external_id).where(
            Player.team_id == team_id,
            Player.position != Position.P.value,
        )
    ).scalars()
    count = 0
    for code in codes:
        collect_player_season_stats(
            session=session, ingestion_run=ingestion_run, player_code=code, http=http
        )
        count += 1
    return count
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd apps/api && uv run pytest tests/ingestion/test_daily_pipeline_naver.py::test_collect_roster_player_season_stats_covers_hitters_not_just_lineup -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/jobs/daily_pipeline.py apps/api/tests/ingestion/test_daily_pipeline_naver.py
git commit -m "feat(ingestion): add roster-wide season-stat collector (hitters only)"
```

---

### Task 3: Wire the roster collector into the daily pipeline + update end-to-end counts

**Files:**
- Modify: `apps/api/app/jobs/daily_pipeline.py` (`run_daily_pipeline` body + `DailyPipelineResult` docstring)
- Test: `apps/api/tests/ingestion/test_daily_pipeline_naver.py` (bump 9 → 15)

- [ ] **Step 1: Update the end-to-end tests to expect the wider pool (failing)**

In `apps/api/tests/ingestion/test_daily_pipeline_naver.py`:

In `test_daily_pipeline_naver_end_to_end`, replace the stat-row assertion block:

```python
    # One stat row per lineup batter (9); the starting pitcher 51111 has no
    # batting order and is excluded.
    assert result.stat_snapshots_created == 1
    assert session.execute(select(func.count()).select_from(PlayerStatSnapshotRow)).scalar() == 9
```

with:

```python
    # One stat row per available team hitter: 9 starters + 6 batterCandidate
    # bench hitters = 15 (zero overlap in the 2025-05-14 fixture). The starting
    # pitcher 51111 is position "P" and is excluded.
    assert result.stat_snapshots_created == 1
    assert session.execute(select(func.count()).select_from(PlayerStatSnapshotRow)).scalar() == 15
```

In `test_daily_pipeline_naver_is_idempotent`, replace:

```python
    # No duplicate stat rows on the short-circuit second run.
    assert session.execute(select(func.count()).select_from(PlayerStatSnapshotRow)).scalar() == 9
```

with:

```python
    # No duplicate stat rows on the short-circuit second run (15 hitters).
    assert session.execute(select(func.count()).select_from(PlayerStatSnapshotRow)).scalar() == 15
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd apps/api && uv run pytest tests/ingestion/test_daily_pipeline_naver.py -v`
Expected: FAIL — count is still 9 because the pipeline still calls the old lineup-only path (not yet rewired).

- [ ] **Step 3: Rewire `run_daily_pipeline`**

In `apps/api/app/jobs/daily_pipeline.py`, inside `run_daily_pipeline`, replace this block:

```python
                    _collect_lineup_player_season_stats(
                        session,
                        ingestion_run=run,
                        lineup_snapshot_id=lr.snapshot_id,
                        http=http_client,
                    )
```

with:

```python
                    lineup_snapshot = session.get(ActualLineupSnapshot, lr.snapshot_id)
                    assert lineup_snapshot is not None
                    _collect_roster_player_season_stats(
                        session,
                        ingestion_run=run,
                        team_id=lineup_snapshot.team_id,
                        http=http_client,
                    )
```

Then update the inline comment just above it (currently "fetch each batter's season record from the per-player Naver endpoint") to read:

```python
                    # The lineup normalizer has now upserted both the starters
                    # and the batterCandidate bench hitters as Player rows. Fetch
                    # every team hitter's season record, then normalize all
                    # PLAYER_STATS payloads of this run into one StatSnapshot so
                    # the recommender runs on the full available-hitter pool.
```

- [ ] **Step 4: Update the `DailyPipelineResult.stat_snapshots_created` docstring**

In `DailyPipelineResult`, the `stat_snapshots_created` attribute doc currently implies one row per lineup batter. Replace its sentence with:

```python
        stat_snapshots_created: Number of games whose player-stats normalizer
            inserted at least one new row on this run (``rows_created > 0``). The
            snapshot now covers every available team hitter (starters +
            batterCandidate bench), not only the starting nine.
```

- [ ] **Step 5: Run the full ingestion test suite**

Run: `cd apps/api && uv run pytest tests/ingestion/ -v`
Expected: PASS — end-to-end stat rows now 15, idempotent run still 15, plus all Task 1/2 tests.

- [ ] **Step 6: Run the broader suite to catch regressions in dependent tests**

Run: `cd apps/api && uv run pytest -q`
Expected: PASS. If `tests/test_pipeline_jobs.py` or `tests/test_full_pipeline.py` assert a hitter/stat-row count of 9, update them to 15 with the same rationale comment, then re-run.

- [ ] **Step 7: Commit**

```bash
git add apps/api/app/jobs/daily_pipeline.py apps/api/tests/
git commit -m "feat(ingestion): collect season stats for full hitter roster, not just lineup"
```

---

### Task 4: Verify against real data + document the source assumption

**Files:**
- Verify: live Supabase project `syirdgelfqrjizvncawi` (read-only via MCP) or a fresh local run
- Modify: `docs/data-sources/` (add/append a short note on `batterCandidate` as the bench-hitter source) — create `docs/data-sources/lg-batter-candidate.md` if no suitable file exists.

- [ ] **Step 1: Re-run ingestion for a date and confirm the wider pool**

Locally against Supabase (see the `running-supabase-dev` skill for the `KBO_DATABASE_URL`):

```bash
cd apps/api
uv run --env-file .env kbo-lab run --date 2026-05-30
```

Note: the daily ingestion run for that date is idempotent — if it already completed, it short-circuits without re-collecting. To force a clean re-evaluation you must re-run against a date whose ingestion run is not yet `completed`, or use a fresh date.

- [ ] **Step 2: Confirm the stat snapshot now holds the full hitter pool**

Run this query (via the Supabase MCP `execute_sql`, read-only, or `psql`):

```sql
SELECT s.id AS snapshot_id, count(*) AS hitter_rows
FROM stat_snapshots s
JOIN player_stat_snapshot_rows r ON r.snapshot_id = s.id
GROUP BY s.id
ORDER BY s.id DESC;
```

Expected: the newest snapshot has materially more than 9 rows (≈ the team's available hitters for that game, typically 13–15).

- [ ] **Step 3: Confirm the recommendation can now diverge from the actual lineup**

```sql
SELECT key_insights_json->'players_added_vs_actual'   AS added,
       key_insights_json->'players_removed_vs_actual' AS removed
FROM lineup_evaluation_summaries
ORDER BY id DESC
LIMIT 1;
```

Expected: `added` / `removed` are now capable of being non-empty (they were always empty when the pool equalled the lineup). A bench bat outscoring a starter will appear here.

- [ ] **Step 4: Document the source assumption**

Write `docs/data-sources/lg-batter-candidate.md` capturing: (a) the bench-hitter pool comes from `result.previewData.homeTeamLineUp.batterCandidate` in the Naver preview; (b) field shape (`playerCode`, `playerName`, `pos` = numeric position, `position` = Korean word, `hitType`, `batsThrows`); (c) the open question to verify across multiple game dates — whether `batterCandidate` reliably lists every entry hitter not in the starting nine, or can omit held-out entry hitters; (d) the known limitation that stat collection currently iterates all team `Player` rows, so the pool can include off-entry players late in a season.

- [ ] **Step 5: Commit**

```bash
git add docs/data-sources/lg-batter-candidate.md
git commit -m "docs(data-sources): document batterCandidate as the bench-hitter pool source"
```

---

## Self-Review (completed during authoring)

- **Spec coverage:** roster-based pool (Tasks 1–3), hitters-only/no-PA-threshold (Task 2 `position != 'P'`, no threshold), Naver squad source = existing preview's `batterCandidate` (Task 1), verification + source doc (Task 4). ✓
- **Type/name consistency:** new helper `_collect_roster_player_season_stats` referenced identically in Task 2 (definition) and Task 3 (call site); `ActualLineupSnapshot` import added where used; `Position.P.value` ("P") matches the stored `Player.position` for pitchers. ✓
- **Placeholder scan:** every code step shows full code; counts (9 starters + 6 candidates = 15) are derived from the actual fixture, not assumed. ✓
- **Open assumption flagged:** `batterCandidate` completeness and long-season pool staleness are documented in Task 4 rather than silently ignored. ✓
