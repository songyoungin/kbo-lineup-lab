# Team Home Pipeline Status (box / postgame) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `GET /api/team/{code}/home`'s `pipeline_status` report `box` and `postgame` from real database state instead of the hardcoded `"missing"` placeholder.

**Architecture:** `build_team_home` in `pregame_views.py` already derives `eval` from `_latest_completed_run(...)`. We add two sibling helpers — `_box_score_exists(...)` (a `BoxScoreSnapshot` exists for the game) and `_latest_completed_postgame_run(...)` (a completed `PostgameReviewRun` exists for the game+team, found by joining through `LineupEvaluationRun`, mirroring the existing query in `postgame_reviews.py:357-376`) — then wire their results into the `pipeline_status` dict. No schema, route, or frontend change: the web layer renders `pipeline_status` generically, so correct values flow through automatically.

**Tech Stack:** Python 3.13, FastAPI, SQLAlchemy 2.x, pydantic v2, pytest, uv. Run all commands from `apps/api` with `uv run`. English commits/docstrings/comments (project README convention). Pre-commit hooks (ruff, ruff-format, mypy, bandit, vulture) must pass.

---

## Background

`apps/api/app/services/pregame_views.py:152-158` currently builds:

```python
pipeline_status: dict[str, str] = {
    "schedule": "ok",
    "lineup": "ok",
    "eval": "ok" if completed_run is not None else "missing",
    "box": "missing",        # hardcoded — never reflects real state
    "postgame": "missing",   # hardcoded — never reflects real state
}
```

`/games/{id}/postgame` reads postgame data through a *separate* path (`postgame_reviews.build_postgame_view`), so it returns data correctly even while team-home reports `postgame: missing`. This mismatch is documented as the "Known Gap" in `.claude/skills/running-fixture-demo/SKILL.md:79-84`. This plan closes that gap and removes the stale Known Gap note.

**Data-model facts that drive the fix:**
- `BoxScoreSnapshot` (`app/models/snapshot.py:156`) has a direct `game_id` column → box-score presence = "a `BoxScoreSnapshot` row exists for this game".
- `PostgameReviewRun` (`app/models/postgame.py:16`) has `evaluation_run_id` + a `status` column, but no direct `game_id`/`team_id`. It links to the game+team by joining `LineupEvaluationRun` on `evaluation_run_id == LineupEvaluationRun.id`. `postgame_reviews.py:357-376` already does exactly this join filtered by `status == "completed"`.

## File Structure

- **Modify** `apps/api/app/services/pregame_views.py`
  - Extend the `app.models.snapshot` import to include `BoxScoreSnapshot`.
  - Add an import for `PostgameReviewRun` from `app.models.postgame`.
  - Add `_box_score_exists(session, game_id) -> bool`.
  - Add `_latest_completed_postgame_run(session, game_id, team_id) -> PostgameReviewRun | None`.
  - Replace the two hardcoded `"missing"` literals in `build_team_home` with calls to these helpers.
- **Modify** `apps/api/tests/test_pregame_api.py`
  - Add an isolated DB-setup helper `_make_session_with_fixture()` (returns a session factory + ids, distinct from the module-level shared state so box/postgame rows don't leak into other tests).
  - Add `test_team_home_box_status_reflects_snapshot`.
  - Add `test_team_home_postgame_status_reflects_review_run`.
- **Modify** `.claude/skills/running-fixture-demo/SKILL.md`
  - Remove the now-resolved "Known Gap" section.

**Testing note (SQLite FK enforcement):** the test engine is `create_engine("sqlite:///:memory:")` with no `PRAGMA foreign_keys=ON`, so SQLite does **not** enforce foreign keys. A `LineupEvaluationRun` can therefore be inserted with placeholder `stat_snapshot_id=1` / `lineup_snapshot_id=1` without those rows existing — the helpers query only `game_id`/`team_id`/`status`, so the unused FK targets are irrelevant to the result.

---

### Task 1: `box` status reflects box-score presence

**Files:**
- Modify: `apps/api/app/services/pregame_views.py:17` (import), add helper after `_latest_completed_run` (`pregame_views.py:89`), and `pregame_views.py:156` (wire-in)
- Test: `apps/api/tests/test_pregame_api.py` (add helper + one test)

- [ ] **Step 1: Add the isolated test-DB helper**

In `apps/api/tests/test_pregame_api.py`, add this function in the "Helpers" section (after `_replay_body`, around line 202). It builds a fresh in-memory DB per call so inserted box/postgame rows stay isolated from the module-level shared state.

```python
def _make_session_with_fixture() -> tuple[sessionmaker[Session], int, int, int]:
    """Create an isolated in-memory DB with the LG fixture and a ModelVersion.

    Returns (session_factory, game_id, team_id, model_version_id). Unlike the
    module-level shared state, each call is a fresh engine, so tests can insert
    box-score and postgame rows without leaking into other tests.
    """
    from sqlalchemy import select

    from app.models.game import Game
    from app.models.team import Team

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory: sessionmaker[Session] = sessionmaker(bind=engine)

    with factory() as s:
        mv = ModelVersion(name="test-model-status", version="v1", model_id="anthropic/claude-test")
        s.add(mv)
        s.commit()
        mv_id = int(mv.id)

    with factory() as s:
        load_fixture_file(FIXTURE_PATH, s)

    with factory() as s:
        game = s.execute(select(Game)).scalars().first()
        assert game is not None
        g_id = int(game.id)
        team = s.execute(select(Team).where(Team.code == "LG")).scalars().first()
        assert team is not None
        t_id = int(team.id)

    return factory, g_id, t_id, mv_id
```

- [ ] **Step 2: Write the failing test for `box`**

Add this test in the `# GET /api/team/lg/home` section of `apps/api/tests/test_pregame_api.py` (after `test_team_home_recent_is_empty_list`, around line 243).

```python
def test_team_home_box_status_reflects_snapshot() -> None:
    """pipeline_status['box'] is 'missing' with no box score, 'ok' once one exists."""
    from app.models.snapshot import BoxScoreSnapshot, IngestionRun
    from app.services.pregame_views import build_team_home

    factory, g_id, _t_id, _mv_id = _make_session_with_fixture()

    with factory() as s:
        before = build_team_home(s, "LG")
        assert before.today is not None
        assert before.today.pipeline_status["box"] == "missing"

    with factory() as s:
        ingestion = IngestionRun(source="test-box", status="completed")
        s.add(ingestion)
        s.commit()
        box = BoxScoreSnapshot(
            game_id=g_id,
            ingestion_run_id=ingestion.id,
            taken_at=datetime(2026, 4, 15, 13, 0, tzinfo=UTC),
            content_hash="boxhash-1",
        )
        s.add(box)
        s.commit()

    with factory() as s:
        after = build_team_home(s, "LG")
        assert after.today is not None
        assert after.today.pipeline_status["box"] == "ok"
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `uv run pytest tests/test_pregame_api.py::test_team_home_box_status_reflects_snapshot -v`
Expected: FAIL on the final assertion — `assert 'missing' == 'ok'` (box is still hardcoded to `"missing"`).

- [ ] **Step 4: Extend the snapshot import**

In `apps/api/app/services/pregame_views.py:17`, change:

```python
from app.models.snapshot import ActualLineupSnapshotRow, PlayerStatSnapshotRow
```

to:

```python
from app.models.snapshot import ActualLineupSnapshotRow, BoxScoreSnapshot, PlayerStatSnapshotRow
```

- [ ] **Step 5: Add the `_box_score_exists` helper**

In `apps/api/app/services/pregame_views.py`, insert this function immediately after `_latest_completed_run` (which ends at line 89, before `_player_name`):

```python
def _box_score_exists(session: Session, game_id: int) -> bool:
    """Return True when a box score snapshot has been ingested for the game."""
    return (
        session.execute(
            select(BoxScoreSnapshot.id).where(BoxScoreSnapshot.game_id == game_id).limit(1)
        ).first()
        is not None
    )
```

- [ ] **Step 6: Wire `box` into pipeline_status**

In `apps/api/app/services/pregame_views.py:156`, replace:

```python
            "box": "missing",
```

with:

```python
            "box": "ok" if _box_score_exists(session, game.id) else "missing",
```

- [ ] **Step 7: Run the test to verify it passes**

Run: `uv run pytest tests/test_pregame_api.py::test_team_home_box_status_reflects_snapshot -v`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add apps/api/app/services/pregame_views.py apps/api/tests/test_pregame_api.py
git commit -m "fix(api): derive team-home box pipeline status from box score snapshot

- add _box_score_exists helper and wire it into build_team_home
- pipeline_status['box'] now reflects ingested box scores instead of a
  hardcoded 'missing' literal
- add isolated test-DB helper and box status regression test"
```

---

### Task 2: `postgame` status reflects completed review runs

**Files:**
- Modify: `apps/api/app/services/pregame_views.py` (add import + helper after `_box_score_exists`, and `pregame_views.py:157` wire-in)
- Test: `apps/api/tests/test_pregame_api.py` (add one test; reuses `_make_session_with_fixture` from Task 1)

- [ ] **Step 1: Write the failing test for `postgame`**

Add this test in the `# GET /api/team/lg/home` section of `apps/api/tests/test_pregame_api.py`, after `test_team_home_box_status_reflects_snapshot`.

```python
def test_team_home_postgame_status_reflects_review_run() -> None:
    """pipeline_status['postgame'] flips to 'ok' once a completed review exists."""
    from app.models.evaluation import LineupEvaluationRun
    from app.models.postgame import PostgameReviewRun
    from app.models.snapshot import BoxScoreSnapshot, IngestionRun
    from app.services.pregame_views import build_team_home

    factory, g_id, t_id, mv_id = _make_session_with_fixture()

    with factory() as s:
        before = build_team_home(s, "LG")
        assert before.today is not None
        assert before.today.pipeline_status["postgame"] == "missing"

    with factory() as s:
        ingestion = IngestionRun(source="test-box", status="completed")
        s.add(ingestion)
        s.commit()
        box = BoxScoreSnapshot(
            game_id=g_id,
            ingestion_run_id=ingestion.id,
            taken_at=datetime(2026, 4, 15, 13, 0, tzinfo=UTC),
            content_hash="boxhash-2",
        )
        s.add(box)
        s.commit()
        # FK enforcement is off in the SQLite test engine, so placeholder
        # stat/lineup snapshot ids are fine — the helper joins on game/team/status.
        eval_run = LineupEvaluationRun(
            game_id=g_id,
            team_id=t_id,
            model_version_id=mv_id,
            stat_snapshot_id=1,
            lineup_snapshot_id=1,
            evaluation_cutoff_at=CUTOFF,
            status="completed",
            finished_at=datetime(2026, 4, 15, 12, 0, tzinfo=UTC),
        )
        s.add(eval_run)
        s.commit()
        review = PostgameReviewRun(
            evaluation_run_id=eval_run.id,
            box_score_snapshot_id=box.id,
            model_version_id=mv_id,
            status="completed",
            finished_at=datetime(2026, 4, 15, 14, 0, tzinfo=UTC),
        )
        s.add(review)
        s.commit()

    with factory() as s:
        after = build_team_home(s, "LG")
        assert after.today is not None
        assert after.today.pipeline_status["postgame"] == "ok"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_pregame_api.py::test_team_home_postgame_status_reflects_review_run -v`
Expected: FAIL on the final assertion — `assert 'missing' == 'ok'` (postgame is still hardcoded to `"missing"`).

- [ ] **Step 3: Add the `PostgameReviewRun` import**

In `apps/api/app/services/pregame_views.py`, add this import alongside the other `app.models` imports (e.g. directly after the `app.models.player` import at line 16):

```python
from app.models.postgame import PostgameReviewRun
```

- [ ] **Step 4: Add the `_latest_completed_postgame_run` helper**

In `apps/api/app/services/pregame_views.py`, insert this function immediately after `_box_score_exists` (added in Task 1). It mirrors the join already used in `postgame_reviews.py:357-376`.

```python
def _latest_completed_postgame_run(
    session: Session, game_id: int, team_id: int
) -> PostgameReviewRun | None:
    """Return the most recent completed postgame review for a game+team pair.

    PostgameReviewRun has no direct game/team columns, so this joins through
    LineupEvaluationRun, matching the lookup in postgame_reviews.build_postgame_view.
    """
    return (
        session.execute(
            select(PostgameReviewRun)
            .join(
                LineupEvaluationRun,
                PostgameReviewRun.evaluation_run_id == LineupEvaluationRun.id,
            )
            .where(
                LineupEvaluationRun.game_id == game_id,
                LineupEvaluationRun.team_id == team_id,
                PostgameReviewRun.status == "completed",
            )
            .order_by(PostgameReviewRun.finished_at.desc())
            .limit(1)
        )
        .scalars()
        .first()
    )
```

- [ ] **Step 5: Wire `postgame` into pipeline_status**

In `apps/api/app/services/pregame_views.py:157`, replace:

```python
            "postgame": "missing",
```

with:

```python
            "postgame": (
                "ok"
                if _latest_completed_postgame_run(session, game.id, team_id) is not None
                else "missing"
            ),
```

(`team_id` is already in scope — it is computed at the top of `build_team_home` via `_lookup_team_id`.)

- [ ] **Step 6: Run the test to verify it passes**

Run: `uv run pytest tests/test_pregame_api.py::test_team_home_postgame_status_reflects_review_run -v`
Expected: PASS.

- [ ] **Step 7: Run the full team-home + regression suite**

Run: `uv run pytest tests/test_pregame_api.py -v`
Expected: PASS — including the pre-existing `test_team_home_returns_200_with_today_game` (it only asserts `"schedule" in pipeline_status`, so the new dynamic box/postgame values don't break it).

- [ ] **Step 8: Commit**

```bash
git add apps/api/app/services/pregame_views.py apps/api/tests/test_pregame_api.py
git commit -m "fix(api): derive team-home postgame pipeline status from review runs

- add _latest_completed_postgame_run helper joining PostgameReviewRun
  through LineupEvaluationRun by game+team, mirroring build_postgame_view
- pipeline_status['postgame'] now reflects completed reviews instead of a
  hardcoded 'missing' literal
- add postgame status regression test"
```

---

### Task 3: Remove the resolved "Known Gap" from the fixture-demo skill

**Files:**
- Modify: `.claude/skills/running-fixture-demo/SKILL.md:79-84`

- [ ] **Step 1: Delete the Known Gap section**

In `.claude/skills/running-fixture-demo/SKILL.md`, remove the entire trailing section (currently the last lines of the file):

```markdown
## Known Gap

`team/lg/home` `pipeline_status` shows `box`/`postgame` as `missing` even after
a postgame run exists and `/games/1/postgame` returns data. The status
computation looks inconsistent with the run state — worth checking when real
ingestion lands. This is a fixture/demo helper, not a production ingestion path.
```

Leave the preceding `## Common Mistakes` section intact as the new end of the file (ensure the file ends with a single trailing newline).

- [ ] **Step 2: Verify the section is gone**

Run: `grep -n "Known Gap" .claude/skills/running-fixture-demo/SKILL.md`
Expected: no output (exit code 1).

- [ ] **Step 3: Commit**

```bash
git add .claude/skills/running-fixture-demo/SKILL.md
git commit -m "docs(skill): drop resolved team-home pipeline-status Known Gap"
```

---

### Task 4: Full verification

**Files:** none (verification only)

- [ ] **Step 1: Run the whole API test suite**

Run: `uv run pytest -q`
Expected: all tests PASS.

- [ ] **Step 2: Run pre-commit on the changed files**

Run (from repo root):

```bash
pre-commit run --files apps/api/app/services/pregame_views.py apps/api/tests/test_pregame_api.py .claude/skills/running-fixture-demo/SKILL.md
```

Expected: ruff, ruff-format, mypy, bandit, vulture all PASS. If pre-commit is not installed in `.venv`, stop and tell the user (per project convention) rather than running the tools directly.

- [ ] **Step 3: Optional manual smoke check**

If a seeded demo DB is available (see `running-fixture-demo` skill), after running a postgame review confirm:

```bash
curl -s http://127.0.0.1:8000/api/team/lg/home | python -m json.tool
```

Expected: `today.pipeline_status.box` and `.postgame` now read `"ok"` (not `"missing"`) once box-score ingestion and a postgame review exist for the game.

---

## Self-Review

**Spec coverage:**
- Bug = hardcoded `box`/`postgame` → Task 1 (box), Task 2 (postgame). ✓
- Stale Known Gap doc → Task 3. ✓
- No regression in existing team-home test → Task 2 Step 7 explicitly verifies. ✓
- Frontend: renders `pipeline_status` generically; no change required (stated in Architecture). ✓

**Placeholder scan:** No TBD/TODO/"handle edge cases"/"similar to Task N". All code shown in full. The shared `_make_session_with_fixture` helper is defined once (Task 1 Step 1) and referenced by name thereafter — it is a real function in the file by Task 2. ✓

**Type consistency:** Helper names used consistently — `_box_score_exists` (Task 1) and `_latest_completed_postgame_run` (Task 2) match between definition and wire-in. Imports (`BoxScoreSnapshot`, `PostgameReviewRun`, `LineupEvaluationRun`) are all added or pre-existing. `team_id` referenced in Task 2 Step 5 is confirmed in scope from `build_team_home`'s opening lines. ✓
