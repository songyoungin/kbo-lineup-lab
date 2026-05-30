# Team Home Pipeline Status — Unify Vocabulary with Canonical Ingestion Status

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `GET /api/team/{code}/home`'s `pipeline_status` use the richer canonical status vocabulary (`waiting|collected|normalized|complete|failed|needs_review`) by reusing the existing `build_game_ingestion_status` service, while preserving the demo/product experience via a presence overlay — without changing the admin-facing canonical service.

**Architecture (Hybrid, admin-preserved):** `build_game_ingestion_status` (apps/api/app/services/ingestion_status.py) stays UNCHANGED — it remains the operational source of truth for the admin ingestion-status page ("did the pipeline run?"). `build_team_home` (apps/api/app/services/pregame_views.py) now calls `build_game_ingestion_status(session, game.id)` to obtain the rich per-category vocabulary, maps the canonical categories to its five existing keys, and applies a **presence overlay**: when a category reads `"waiting"` (pipeline never ran) but the underlying artifact actually exists (snapshot / completed run — detected by the already-present `_lineup_snapshot_exists` / `_box_score_exists` / `_latest_completed_run` / `_latest_completed_postgame_run` helpers), it is upgraded to that category's terminal success status. This gives: real ingestion → canonical values (including `failed`); directly-seeded fixture/demo data → `complete`/`normalized`. The frontend `pipelineTone` is extended to map the new vocabulary. Single vocabulary across the app; admin semantics intact.

**Tech Stack:** Backend — Python 3.13, FastAPI, SQLAlchemy 2.x, pydantic v2, pytest, uv (run from `apps/api` with `uv run`). Frontend — Next.js (see `apps/web/AGENTS.md`), TypeScript, ESLint, Prettier (run from `apps/web`). English commits/docstrings/comments. Pre-commit hooks (ruff, ruff-format, mypy, bandit, vulture, ESLint, Prettier) must pass.

---

## Background

After PRs #28/#29, `build_team_home`'s `pipeline_status` is a binary `"ok"`/`"missing"` map computed locally from artifact presence:

```python
pipeline_status: dict[str, str] = {
    "schedule": "ok",
    "lineup": "ok" if _lineup_snapshot_exists(session, game.id, team_id) else "missing",
    "eval": "ok" if completed_run is not None else "missing",
    "box": "ok" if _box_score_exists(session, game.id) else "missing",
    "postgame": (
        "ok" if _latest_completed_postgame_run(session, game.id, team_id) is not None else "missing"
    ),
}
```

Meanwhile `apps/api/app/services/ingestion_status.py` already computes a richer per-category status (`build_game_ingestion_status`) used by the admin page, with vocabulary `waiting | collected | normalized | complete | failed | needs_review`. Team-home's binary map is a simplified parallel vocabulary. This plan unifies team-home onto the canonical vocabulary.

**Key semantic fact (why the overlay exists):** the canonical builders are *pipeline-run-centric* — each returns `"waiting"` first if there is no `pipeline:ingest-pregame:{ext}` / `pipeline:ingest-postgame:{ext}` `IngestionRun` (see `_build_lineup_status` etc. in ingestion_status.py). The fixture/demo path (`fixture_loader.py`, `scripts/seed_demo.py`) seeds snapshots and runs WITHOUT creating those pipeline runs (its source is `fixture:lg_2026_sample`). So for the demo, canonical lineup/eval/box/postgame are all `"waiting"`. The presence overlay upgrades those to the terminal success status so the demo (and any directly-seeded data) reads correctly — matching the intent of PRs #28/#29. The admin service is NOT modified, so its operational "waiting" semantics are preserved.

**Canonical category → team-home key mapping and terminal success status:**

| team-home key | canonical category | success status (overlay) | presence helper |
|---|---|---|---|
| `schedule` | `schedule` | `complete` | always present (the `game` row exists in this block) |
| `lineup` | `lineup` | `normalized` | `_lineup_snapshot_exists(session, game.id, team_id)` |
| `eval` | `evaluation` | `complete` | `completed_run is not None` (`_latest_completed_run`) |
| `box` | `box_score` | `normalized` | `_box_score_exists(session, game.id)` |
| `postgame` | `postgame_review` | `complete` | `_latest_completed_postgame_run(session, game.id, team_id) is not None` |

(Success statuses match what the canonical builders emit for those categories when the pipeline DOES run: lineup/box terminate at `normalized`, evaluation/postgame at `complete`.)

## File Structure

- **Modify** `apps/api/app/services/pregame_views.py`
  - Import `build_game_ingestion_status` from `app.services.ingestion_status`.
  - Add a small pure helper `_overlay_presence(canonical_status, present, success_status) -> str`.
  - Rewrite the `pipeline_status` construction in `build_team_home` to map canonical categories + overlay. Keep all four existing presence helpers (they become the overlay inputs). Update the docstring bullet.
- **Modify** `apps/api/tests/test_pregame_api.py`
  - Update the three value-asserting team-home tests (`lineup`, `box`, `postgame`) to the new vocabulary.
  - Add `test_team_home_pipeline_status_surfaces_failed_run` proving canonical `failed` passes through (not overlaid).
- **Modify** `apps/web/app/page.tsx`
  - Extend `pipelineTone` to map the canonical vocabulary; keep existing `ok`/`missing`/`pending`/`error` mappings for backward compatibility with `MOCK_TEAM_HOME`.
- **UNCHANGED:** `apps/api/app/services/ingestion_status.py` and `apps/api/tests/test_ingestion_status_api.py` (admin semantics preserved). `apps/web/lib/mock.ts` (its `ok`/`missing` values still render correctly via the retained tone mappings).

**Import-cycle note:** `ingestion_status.py` imports only models (no service imports), so `pregame_views.py → ingestion_status.py` is a safe one-way dependency.

---

### Task 1: Backend — team-home reuses canonical status with presence overlay

**Files:**
- Modify: `apps/api/app/services/pregame_views.py` (import; new `_overlay_presence` helper; `build_team_home` pipeline_status block + docstring)
- Test: `apps/api/tests/test_pregame_api.py` (update 3 tests, add 1)

- [ ] **Step 1: Update the three existing value tests to the new vocabulary**

In `apps/api/tests/test_pregame_api.py`, change the assertions in these three tests (the setup/structure stays identical; only the asserted strings change):

In `test_team_home_lineup_status_reflects_snapshot`:
- change `assert before.today.pipeline_status["lineup"] == "missing"` → `== "waiting"`
- change `assert after.today.pipeline_status["lineup"] == "ok"` → `== "normalized"`
- update the docstring to: `"""pipeline_status['lineup'] is 'waiting' with no lineup, 'normalized' once one exists."""`

In `test_team_home_box_status_reflects_snapshot`:
- change `assert before.today.pipeline_status["box"] == "missing"` → `== "waiting"`
- change `assert after.today.pipeline_status["box"] == "ok"` → `== "normalized"`
- update the docstring to: `"""pipeline_status['box'] is 'waiting' with no box score, 'normalized' once one exists."""`

In `test_team_home_postgame_status_reflects_review_run`:
- change `assert before.today.pipeline_status["postgame"] == "missing"` → `== "waiting"`
- change `assert after.today.pipeline_status["postgame"] == "ok"` → `== "complete"`
- update the docstring to: `"""pipeline_status['postgame'] flips to 'complete' once a completed review exists."""`

- [ ] **Step 2: Add the failed-passthrough test**

Add this test in the `# GET /api/team/lg/home` section of `apps/api/tests/test_pregame_api.py` (after `test_team_home_box_status_reflects_snapshot`). It proves the canonical `failed` status passes through team-home unchanged (the overlay only upgrades `"waiting"`).

```python
def test_team_home_pipeline_status_surfaces_failed_run() -> None:
    """A failed pregame ingestion run surfaces as 'failed' in team-home (canonical passthrough)."""
    from app.models.game import Game
    from app.models.snapshot import IngestionRun
    from app.services.pregame_views import build_team_home

    factory, g_id, _t_id, _mv_id = _make_session_with_fixture()

    with factory() as s:
        game = s.get(Game, g_id)
        assert game is not None
        ext_id = game.external_id
        # The lineup category keys off the pregame ingestion run; a failed run
        # must surface as 'failed' regardless of any seeded snapshot.
        s.add(
            IngestionRun(
                source=f"pipeline:ingest-pregame:{ext_id}",
                status="failed",
                error_message="boom",
            )
        )
        s.commit()

    with factory() as s:
        home = build_team_home(s, "LG")
        assert home.today is not None
        assert home.today.pipeline_status["lineup"] == "failed"
```

Note: `_make_session_with_fixture` and the `Game` model expose `external_id`; the canonical `_build_lineup_status` returns `"failed"` immediately when the pregame run is failed, and `_overlay_presence` does not touch non-`"waiting"` statuses.

- [ ] **Step 3: Run the updated/new tests to verify they FAIL**

Run: `cd /Users/serena/Documents/kbo-lineup-lab/apps/api && uv run pytest tests/test_pregame_api.py -k "team_home and (lineup or box or postgame or failed_run)" -v`
Expected: the three updated tests FAIL (they currently get `"ok"`/`"missing"`, not the new vocabulary) and `test_team_home_pipeline_status_surfaces_failed_run` FAILS (current code returns `"ok"`/`"missing"`, never `"failed"`).

- [ ] **Step 4: Add the import**

In `apps/api/app/services/pregame_views.py`, add (with the other `app.services` imports, e.g. after the `from app.services.evaluation_runs import get_or_create_evaluation_run` line):

```python
from app.services.ingestion_status import build_game_ingestion_status
```

- [ ] **Step 5: Add the `_overlay_presence` helper**

In `apps/api/app/services/pregame_views.py`, add this module-level helper immediately AFTER `_latest_completed_postgame_run` (before `_player_name`):

```python
def _overlay_presence(canonical_status: str, present: bool, success_status: str) -> str:
    """Upgrade a 'waiting' canonical status to a success status when data is present.

    The canonical ingestion status is pipeline-run-centric, so directly-seeded data
    (no pipeline run) reads as 'waiting'. For the team-home product view we surface the
    terminal success status when the underlying artifact actually exists. Non-'waiting'
    statuses (collected, normalized, complete, failed, needs_review) pass through unchanged.
    """
    if canonical_status == "waiting" and present:
        return success_status
    return canonical_status
```

- [ ] **Step 6: Rewrite the `pipeline_status` construction in `build_team_home`**

In `apps/api/app/services/pregame_views.py`, inside `build_team_home`'s `if game is not None:` block, the current code computes `completed_run = _latest_completed_run(session, game.id, team_id)` and then builds the `pipeline_status` dict. Replace the existing `pipeline_status: dict[str, str] = { ... }` literal (the whole dict, including the `schedule` comment lines added in #29) with:

```python
        # Canonical per-category status (rich vocabulary, operational view). The
        # canonical service is pipeline-run-centric, so directly-seeded data reads as
        # "waiting"; _overlay_presence upgrades those to the terminal success status
        # for the product view. Admin (build_game_ingestion_status) is unchanged.
        canonical = {
            c.category: c.status
            for c in build_game_ingestion_status(session, game.id).categories
        }
        pipeline_status: dict[str, str] = {
            "schedule": _overlay_presence(canonical.get("schedule", "waiting"), True, "complete"),
            "lineup": _overlay_presence(
                canonical.get("lineup", "waiting"),
                _lineup_snapshot_exists(session, game.id, team_id),
                "normalized",
            ),
            "eval": _overlay_presence(
                canonical.get("evaluation", "waiting"),
                completed_run is not None,
                "complete",
            ),
            "box": _overlay_presence(
                canonical.get("box_score", "waiting"),
                _box_score_exists(session, game.id),
                "normalized",
            ),
            "postgame": _overlay_presence(
                canonical.get("postgame_review", "waiting"),
                _latest_completed_postgame_run(session, game.id, team_id) is not None,
                "complete",
            ),
        }
```

Keep the existing `completed_run = _latest_completed_run(session, game.id, team_id)` line above this block (it is reused for the `eval` overlay). Do not remove any of the four presence helpers — they are all still used here.

- [ ] **Step 7: Update the `build_team_home` docstring bullet**

In `apps/api/app/services/pregame_views.py`, in `build_team_home`'s docstring, replace the bullet (added in #29):

```
    - Pipeline status for each stage is derived from the presence of the corresponding
      ingested data or run (schedule/lineup snapshots, evaluation run, box score, postgame review).
```

with:

```
    - Pipeline status reuses the canonical per-category vocabulary from
      build_game_ingestion_status, overlaid with artifact presence so directly-seeded
      data (no pipeline run) reads as complete/normalized rather than waiting.
```

- [ ] **Step 8: Run the tests to verify they pass**

Run: `cd /Users/serena/Documents/kbo-lineup-lab/apps/api && uv run pytest tests/test_pregame_api.py -q`
Expected: all pass (including the 3 updated value tests and the new failed-passthrough test, and `test_team_home_returns_200_with_today_game` which only asserts `"schedule" in pipeline_status`).

Then confirm the admin suite is unaffected: `uv run pytest tests/test_ingestion_status_api.py -q`
Expected: all pass, with NO edits to that file (admin semantics preserved).

- [ ] **Step 9: Commit**

```bash
cd /Users/serena/Documents/kbo-lineup-lab && git add apps/api/app/services/pregame_views.py apps/api/tests/test_pregame_api.py && git commit -m "fix(api): unify team-home pipeline status with canonical ingestion vocabulary

- build_team_home reuses build_game_ingestion_status for the rich status
  vocabulary (waiting/collected/normalized/complete/failed/needs_review)
- add _overlay_presence: upgrade 'waiting' to the terminal success status when
  the artifact exists, so directly-seeded/demo data reads correctly
- canonical ingestion_status service (admin) is unchanged
- update team-home status tests to the new vocabulary; add failed-passthrough test"
```

---

### Task 2: Frontend — map the canonical vocabulary in `pipelineTone`

**Files:**
- Modify: `apps/web/app/page.tsx` (the `pipelineTone` function, currently lines ~23-29)

**Note on the codebase:** `apps/web/AGENTS.md` warns this Next.js version differs from training data and to read `node_modules/next/dist/docs/` before writing code. This task edits ONLY the pure TypeScript function `pipelineTone` (string comparisons returning a `StatusTone`) — it uses no Next.js APIs, components, or framework features, so no Next.js doc reading is required. Stay within the existing function shape.

- [ ] **Step 1: Extend `pipelineTone`**

In `apps/web/app/page.tsx`, replace the existing function:

```typescript
// Map pipeline status value → tone
function pipelineTone(status: string): StatusTone {
  if (status === "ok") return "good";
  if (status === "missing" || status === "error") return "danger";
  if (status === "pending") return "warning";
  return "neutral";
}
```

with:

```typescript
// Map pipeline status value → tone.
// Canonical ingestion vocabulary: waiting | collected | normalized | complete | failed | needs_review.
// Legacy/mock values (ok | missing | pending | error) are kept for backward compatibility.
function pipelineTone(status: string): StatusTone {
  if (status === "ok" || status === "complete" || status === "normalized") return "good";
  if (status === "missing" || status === "error" || status === "failed") return "danger";
  if (status === "pending" || status === "collected" || status === "needs_review") return "warning";
  return "neutral"; // includes "waiting"
}
```

- [ ] **Step 2: Lint and format-check the change**

Run (from `apps/web`):

```bash
cd /Users/serena/Documents/kbo-lineup-lab/apps/web && npm run lint && npm run format:check
```

Expected: both pass. If `format:check` flags `page.tsx`, run `npx prettier --write app/page.tsx` and re-run.

- [ ] **Step 3: Type-check / build the change**

Run (from `apps/web`): `cd /Users/serena/Documents/kbo-lineup-lab/apps/web && npm run build`
Expected: build succeeds. (The page is a server component that calls the API and falls back to `MOCK_TEAM_HOME` on `ApiError`, so the build does not require a running backend.) If `next build` is impractical in the environment, fall back to `npx tsc --noEmit` and report which was used.

- [ ] **Step 4: Commit**

```bash
cd /Users/serena/Documents/kbo-lineup-lab && git add apps/web/app/page.tsx && git commit -m "feat(web): map canonical ingestion status vocabulary to pipeline tone

- pipelineTone now handles waiting/collected/normalized/complete/failed/needs_review
- legacy ok/missing/pending/error mappings retained for mock fallback"
```

---

### Task 3: Full verification

**Files:** none (verification only)

- [ ] **Step 1: Backend suite**

Run: `cd /Users/serena/Documents/kbo-lineup-lab/apps/api && uv run pytest -q`
Expected: all tests PASS.

- [ ] **Step 2: Confirm admin file untouched**

Run: `cd /Users/serena/Documents/kbo-lineup-lab && git diff main --stat -- apps/api/app/services/ingestion_status.py apps/api/tests/test_ingestion_status_api.py`
Expected: NO output (these files are unchanged on the branch — admin semantics preserved).

- [ ] **Step 3: Confirm the binary vocabulary is gone from team-home**

Run: `cd /Users/serena/Documents/kbo-lineup-lab && git grep -nE '"(ok|missing)"' apps/api/app/services/pregame_views.py`
Expected: NO output (team-home no longer emits the binary `"ok"`/`"missing"` literals).

- [ ] **Step 4: Frontend lint/format**

Run: `cd /Users/serena/Documents/kbo-lineup-lab/apps/web && npm run lint && npm run format:check`
Expected: both PASS.

---

## Self-Review

**Spec coverage:**
- Unify team-home onto canonical vocabulary → Task 1 (reuse `build_game_ingestion_status`). ✓
- Preserve demo/product experience → `_overlay_presence` upgrades `"waiting"` when artifacts exist. ✓
- Keep admin operational semantics → `ingestion_status.py` and its tests untouched; Task 3 Step 2 verifies. ✓
- Frontend renders the new vocabulary → Task 2 (`pipelineTone`). ✓
- Surface real `failed` states → Task 1 Step 2 failed-passthrough test. ✓

**Placeholder scan:** No TBD/TODO/"handle edge cases"/"similar to Task N". All code shown in full. `_make_session_with_fixture` and the four presence helpers pre-exist (PRs #28/#29) and are reused by name. ✓

**Type consistency:** `_overlay_presence(canonical_status: str, present: bool, success_status: str) -> str` is called with each category's canonical value, a bool presence expression, and the matching success string. `build_game_ingestion_status(session, game_id) -> GameIngestionStatus` with `.categories: tuple[CategoryStatus, ...]`, each `CategoryStatus.category`/`.status` are `str` — the dict comprehension `{c.category: c.status for c in ...}` is type-correct. Category names (`schedule`, `lineup`, `evaluation`, `box_score`, `postgame_review`) match those emitted by `build_game_ingestion_status`. Frontend `pipelineTone` returns the existing `StatusTone` union. ✓
