# Team Home Pipeline Status (schedule / lineup) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `GET /api/team/{code}/home`'s `pipeline_status` report `lineup` from real database state, and make the `schedule` value's correctness explicit, so none of the five stages is a silent hardcoded placeholder.

**Architecture:** This continues the work that already made `box`/`postgame` DB-derived in `build_team_home` (`apps/api/app/services/pregame_views.py`). `lineup` becomes `"ok"` iff an `ActualLineupSnapshot` exists for the game+team (new `_lineup_snapshot_exists` helper, mirroring `_box_score_exists`). `schedule` is left as `"ok"` but with an explanatory comment: the entire `today_card`/`pipeline_status` block only runs inside `if game is not None`, so the schedule row provably exists whenever this code path is reached — it is already DB-derived by construction, and adding a helper would be redundant. No schema, route, or frontend change: the web layer renders `pipeline_status` generically.

**Tech Stack:** Python 3.13, FastAPI, SQLAlchemy 2.x, pydantic v2, pytest, uv. Run all commands from `apps/api` with `uv run`. English commits/docstrings/comments (project README convention). Pre-commit hooks (ruff, ruff-format, mypy, bandit, vulture) must pass.

---

## Background

After the box/postgame fix, `build_team_home` (`apps/api/app/services/pregame_views.py`, around lines 189-200) builds:

```python
pipeline_status: dict[str, str] = {
    "schedule": "ok",
    "lineup": "ok",          # hardcoded — never reflects whether a lineup was ingested
    "eval": "ok" if completed_run is not None else "missing",
    "box": "ok" if _box_score_exists(session, game.id) else "missing",
    "postgame": (
        "ok"
        if _latest_completed_postgame_run(session, game.id, team_id) is not None
        else "missing"
    ),
}
```

`lineup` is still hardcoded `"ok"` regardless of whether a lineup snapshot has actually been ingested for the game. This task closes that remaining gap.

**Data-model facts:**
- `ActualLineupSnapshot` (`app/models/snapshot.py:99`) has both a `game_id` column AND a `team_id` column. So "a lineup was ingested for this team's game" = "an `ActualLineupSnapshot` row exists for this `game_id` + `team_id`". Using `team_id` too (not just `game_id`) is correct because a single game involves two teams, each with its own lineup.
- `schedule`: there is no separate schedule-snapshot table; the `Game` row itself is the schedule. The `pipeline_status` dict is only constructed inside `if game is not None`, so the schedule data provably exists at that point. `"ok"` is therefore already correct — no DB query needed.

**Test fixture note (learned from the box fix):** `apps/api/fixtures/lg_2026_sample.json` is loaded by `load_fixture_file`, and it SEEDS an `ActualLineupSnapshot` (the fixture loader's `actual_lineup_snapshot` section). So a freshly-seeded test DB starts WITH a lineup snapshot. To exercise the `"missing"` branch the test must first `delete` the fixture-seeded `ActualLineupSnapshot` for the game — exactly mirroring how `test_team_home_box_status_reflects_snapshot` deletes the fixture box score.

## File Structure

- **Modify** `apps/api/app/services/pregame_views.py`
  - Extend the `app.models.snapshot` import to include `ActualLineupSnapshot`.
  - Add `_lineup_snapshot_exists(session, game_id, team_id) -> bool` (placed next to `_box_score_exists`).
  - Replace the hardcoded `"lineup": "ok"` with a call to the helper.
  - Add a one-line comment on the `"schedule": "ok"` entry explaining it is guaranteed by the `game is not None` guard.
- **Modify** `apps/api/tests/test_pregame_api.py`
  - Add `test_team_home_lineup_status_reflects_snapshot` (reuses the existing `_make_session_with_fixture` helper; deletes the fixture-seeded lineup snapshot to reach the `"missing"` branch, then inserts one to reach `"ok"`).

The existing `app.models.snapshot` import line currently reads:
`from app.models.snapshot import ActualLineupSnapshotRow, BoxScoreSnapshot, PlayerStatSnapshotRow`
(note: `ActualLineupSnapshotRow` is the per-row child table; we need the parent `ActualLineupSnapshot` as well.)

---

### Task 1: `lineup` status reflects lineup-snapshot presence; `schedule` made explicit

**Files:**
- Modify: `apps/api/app/services/pregame_views.py` (import line; new helper after `_box_score_exists` ~line 100; the `pipeline_status` dict ~lines 191-192)
- Test: `apps/api/tests/test_pregame_api.py` (one new test in the `# GET /api/team/lg/home` section)

- [ ] **Step 1: Write the failing test**

Add this test in `apps/api/tests/test_pregame_api.py` in the `# GET /api/team/lg/home` section, immediately AFTER `test_team_home_recent_is_empty_list` (so it sits with the other team-home tests). It reuses the existing module-level `_make_session_with_fixture()` helper — do NOT redefine that helper.

```python
def test_team_home_lineup_status_reflects_snapshot() -> None:
    """pipeline_status['lineup'] is 'missing' with no lineup, 'ok' once one exists."""
    from sqlalchemy import delete

    from app.models.snapshot import ActualLineupSnapshot, IngestionRun
    from app.services.pregame_views import build_team_home

    factory, g_id, t_id, _mv_id = _make_session_with_fixture()

    # The fixture seeds a lineup snapshot; remove it so we can test the 'missing' branch.
    with factory() as s:
        s.execute(delete(ActualLineupSnapshot).where(ActualLineupSnapshot.game_id == g_id))
        s.commit()

    with factory() as s:
        before = build_team_home(s, "LG")
        assert before.today is not None
        assert before.today.pipeline_status["lineup"] == "missing"

    with factory() as s:
        ingestion = IngestionRun(source="test-lineup", status="completed")
        s.add(ingestion)
        s.commit()
        lineup = ActualLineupSnapshot(
            game_id=g_id,
            team_id=t_id,
            ingestion_run_id=ingestion.id,
            announced_at=datetime(2026, 4, 15, 8, 30, tzinfo=UTC),
            content_hash="lineuphash-1",
        )
        s.add(lineup)
        s.commit()

    with factory() as s:
        after = build_team_home(s, "LG")
        assert after.today is not None
        assert after.today.pipeline_status["lineup"] == "ok"
```

Notes: `datetime`, `UTC` are already imported at the top of the test file. `_make_session_with_fixture` returns `(factory, game_id, team_id, model_version_id)` — this test uses `g_id` and `t_id`. `ActualLineupSnapshot` required non-null fields are `game_id`, `team_id`, `ingestion_run_id`, `announced_at`, `content_hash` (verified against `app/models/snapshot.py`).

- [ ] **Step 2: Run the test to verify it fails (for the right reason)**

Run: `cd /Users/serena/Documents/kbo-lineup-lab/apps/api && uv run pytest tests/test_pregame_api.py::test_team_home_lineup_status_reflects_snapshot -v`
Expected: FAIL on the FIRST assertion (`assert 'ok' == 'missing'`) — because `lineup` is hardcoded to `"ok"`, even after the fixture lineup is deleted. (This is the correct failing signal: the hardcoded value can never be `"missing"`.)

- [ ] **Step 3: Extend the snapshot import**

In `apps/api/app/services/pregame_views.py`, change the import line:

```python
from app.models.snapshot import ActualLineupSnapshotRow, BoxScoreSnapshot, PlayerStatSnapshotRow
```

to (add `ActualLineupSnapshot`, keep alphabetical-ish order matching the existing style):

```python
from app.models.snapshot import (
    ActualLineupSnapshot,
    ActualLineupSnapshotRow,
    BoxScoreSnapshot,
    PlayerStatSnapshotRow,
)
```

- [ ] **Step 4: Add the `_lineup_snapshot_exists` helper**

In `apps/api/app/services/pregame_views.py`, insert this function immediately AFTER `_box_score_exists` (which ends around line 100, before `_latest_completed_postgame_run`):

```python
def _lineup_snapshot_exists(session: Session, game_id: int, team_id: int) -> bool:
    """Return True when an actual lineup has been ingested for the team's game."""
    return (
        session.execute(
            select(ActualLineupSnapshot.id)
            .where(
                ActualLineupSnapshot.game_id == game_id,
                ActualLineupSnapshot.team_id == team_id,
            )
            .limit(1)
        ).first()
        is not None
    )
```

`select` and `Session` are already imported at the top of the file.

- [ ] **Step 5: Wire `lineup` and annotate `schedule`**

In `apps/api/app/services/pregame_views.py`, in the `pipeline_status` dict, replace these two lines:

```python
            "schedule": "ok",
            "lineup": "ok",
```

with:

```python
            # schedule is guaranteed: this block only runs inside `if game is not None`,
            # so the Game (schedule) row provably exists.
            "schedule": "ok",
            "lineup": "ok" if _lineup_snapshot_exists(session, game.id, team_id) else "missing",
```

(`team_id` is already in scope — computed at the top of `build_team_home` via `_lookup_team_id`. `game` is the non-None game in this block.)

- [ ] **Step 6: Run the test to verify it passes**

Run: `cd /Users/serena/Documents/kbo-lineup-lab/apps/api && uv run pytest tests/test_pregame_api.py::test_team_home_lineup_status_reflects_snapshot -v`
Expected: PASS.

Then run the whole file to confirm no regression: `uv run pytest tests/test_pregame_api.py -q`
Expected: all pass — including `test_team_home_returns_200_with_today_game` (it only asserts `"schedule" in pipeline_status`, so the new dynamic `lineup` value does not break it).

- [ ] **Step 7: Commit**

```bash
cd /Users/serena/Documents/kbo-lineup-lab && git add apps/api/app/services/pregame_views.py apps/api/tests/test_pregame_api.py && git commit -m "fix(api): derive team-home lineup pipeline status from lineup snapshot

- add _lineup_snapshot_exists helper (game+team) and wire it into build_team_home
- pipeline_status['lineup'] now reflects an ingested ActualLineupSnapshot
  instead of a hardcoded 'ok' literal
- annotate schedule='ok' as guaranteed by the game-exists guard
- add lineup status regression test"
```

(Use `git` from the repo root, NOT `git -C`.)

---

### Task 2: Full verification

**Files:** none (verification only)

- [ ] **Step 1: Run the whole API test suite**

Run: `cd /Users/serena/Documents/kbo-lineup-lab/apps/api && uv run pytest -q`
Expected: all tests PASS.

- [ ] **Step 2: Confirm the change is complete**

Run: `cd /Users/serena/Documents/kbo-lineup-lab && git grep -n '"lineup": "ok",' apps/api/app/services/pregame_views.py`
Expected: NO output (the bare hardcoded `"lineup": "ok",` literal is gone; only the conditional form remains).

---

## Self-Review

**Spec coverage:**
- `lineup` hardcoded → Task 1 makes it DB-derived via `_lineup_snapshot_exists`. ✓
- `schedule` → Task 1 documents why `"ok"` is already correct (game-exists guard); no needless helper (YAGNI). ✓
- No regression in existing team-home test → Task 1 Step 6 verifies. ✓
- Frontend renders `pipeline_status` generically; no change required. ✓

**Placeholder scan:** No TBD/TODO/"handle edge cases"/"similar to Task N". All code shown in full. The `_make_session_with_fixture` helper is pre-existing (created in the box/postgame work) and reused by name — it is a real function in the file. ✓

**Type consistency:** `_lineup_snapshot_exists(session, game_id, team_id) -> bool` matches the `_box_score_exists` shape and is called with `(session, game.id, team_id)`. Import adds the parent `ActualLineupSnapshot` (not the `*Row` child). `ActualLineupSnapshot` constructor fields (`game_id`, `team_id`, `ingestion_run_id`, `announced_at`, `content_hash`) match the model. ✓
