# `kbo-lab run` Exit-Code on No-Game Days Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop `kbo-lab run` (and therefore the `ingestion-canary` workflow) from reporting a **failure** on a legitimate KBO off-day (no LG game scheduled). The daily ingestion itself completes fine on those days; only the absence of a game must no longer be conflated with a broken pipeline.

**Architecture (Approach A — surgical exit-code distinction):** `DailyPipelineResult` already exposes `games_found`. Thread it onto `FullPipelineResult`, add a `no_game_scheduled` property that is True exactly when ingestion completed, produced no game, and found 0 scheduled games (with no error), and have the CLI `run` command exit 0 in that case while still exiting 1 on genuine failures.

**Tech Stack:** Python 3.13, Typer, SQLAlchemy, pytest, uv. Run from `apps/api`.

**Context (why):** The only scheduled job is `ingestion-canary` (`.github/workflows/ingestion-canary.yml`), which runs `kbo-lab run --date <yesterday KST>` and treats a non-zero exit as an alert. `kbo-lab run` (`app/cli.py:36-39`) raises `typer.Exit(code=1)` whenever `FullPipelineResult.succeeded` is False, and `succeeded` (`app/jobs/full_pipeline.py:49-57`) requires `game_id is not None`. On an off-day the daily pipeline returns `status="completed"` with `games_found=0` and no game/lineup is found, so `run_full_pipeline` returns `game_id=None` → `succeeded=False` → exit 1 → the canary goes RED. KBO usually has no Monday games, so this fires a false failure most weeks (confirmed: the 2026-06-01 canary run failed with `daily=completed, game_id=None` and exit code 1). This erodes the canary's signal (alert fatigue), masking real source/pipeline breakage.

**Why `games_found` (not just `game_id is None`):** `game_id is None` alone cannot tell "no game scheduled" (benign) apart from "a game WAS scheduled but its lineup wasn't ingested" (worth alerting). `daily.games_found == 0` is the precise off-day signal. The `no_game_scheduled` property additionally requires `game_id is None` and `error is None` so it can never overlap a genuine-failure or game-present outcome.

**Determinism / safety:** No change to the deterministic scoring/position layer. Pure orchestration/exit-code change. Genuine failures (daily run failed; or a game was found but eval/postgame errored; or a scheduled game had no lineup → `games_found > 0`) still exit 1 and still alert. Only the true off-day (`completed` + `games_found == 0` + no game) flips from exit 1 to exit 0.

---

## File Structure

- `app/jobs/full_pipeline.py` — ADD `games_found` field (defaulted) to `FullPipelineResult`; ADD `no_game_scheduled` property; populate `games_found` from `daily.games_found` at the construction sites in `run_full_pipeline`; include `games_found` in `summary()`.
- `app/cli.py` — MODIFY the `run` command's exit decision: exit 0 when `succeeded OR no_game_scheduled`, else exit 1.
- `tests/test_full_pipeline.py` — ADD unit tests for `no_game_scheduled` (off-day True; game present False; daily failed False; error set False) and that `summary()` includes games.
- `tests/test_cli_run.py` — NEW: CLI-level tests (typer `CliRunner`, `run_full_pipeline` monkeypatched) proving exit 0 on off-day, exit 0 on success, exit 1 on genuine failure.

---

## Task 1: Surface `games_found` and `no_game_scheduled` on `FullPipelineResult`

**Files:**
- Modify: `app/jobs/full_pipeline.py`
- Test: `tests/test_full_pipeline.py`

- [ ] **Step 1: Write the failing tests**

Read `tests/test_full_pipeline.py` first (it constructs `FullPipelineResult` with keyword args and asserts on `succeeded`/`summary`). Append tests like:

```python
def test_no_game_scheduled_true_on_off_day() -> None:
    """no_game_scheduled is True when ingestion completed but no LG game was scheduled."""
    result = FullPipelineResult(
        target_date=date(2026, 6, 1),
        daily_status="completed",
        teams_created=0,
        game_id=None,
        evaluation_run_id=None,
        postgame_review_run_id=None,
        games_found=0,
    )
    assert result.no_game_scheduled is True
    assert result.succeeded is False


def test_no_game_scheduled_false_when_game_present() -> None:
    """A scheduled game (games_found > 0) is never treated as an off-day."""
    result = FullPipelineResult(
        target_date=date(2026, 5, 30),
        daily_status="completed",
        teams_created=0,
        game_id=1,
        evaluation_run_id=2,
        postgame_review_run_id=3,
        games_found=1,
    )
    assert result.no_game_scheduled is False


def test_no_game_scheduled_false_when_daily_failed() -> None:
    """A failed daily run is a genuine failure, not an off-day."""
    result = FullPipelineResult(
        target_date=date(2026, 6, 1),
        daily_status="failed",
        teams_created=0,
        game_id=None,
        evaluation_run_id=None,
        postgame_review_run_id=None,
        games_found=0,
    )
    assert result.no_game_scheduled is False


def test_no_game_scheduled_false_when_error_set() -> None:
    """An analysis error is a genuine failure, not an off-day, even if games_found is 0."""
    result = FullPipelineResult(
        target_date=date(2026, 5, 30),
        daily_status="completed",
        teams_created=0,
        game_id=1,
        evaluation_run_id=None,
        postgame_review_run_id=None,
        games_found=0,
        error="500: boom",
    )
    assert result.no_game_scheduled is False


def test_summary_includes_games_found() -> None:
    """summary() surfaces games_found for canary log readability."""
    result = FullPipelineResult(
        target_date=date(2026, 6, 1),
        daily_status="completed",
        teams_created=0,
        game_id=None,
        evaluation_run_id=None,
        postgame_review_run_id=None,
        games_found=0,
    )
    assert "games=0" in result.summary()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_full_pipeline.py -v`
Expected: the new tests FAIL — `FullPipelineResult` has no `games_found` field / `no_game_scheduled` property / `games=` in summary.

- [ ] **Step 3: Write minimal implementation**

In `app/jobs/full_pipeline.py`:

1. Add a defaulted `games_found` field (place it AFTER the non-defaulted fields, next to `error`, so the existing keyword-arg construction stays valid):

```python
    games_found: int = 0
    error: str | None = None
```

Update the class docstring's Attributes list to mention `games_found` (the number of LG games the schedule returned for the date; 0 on a true off-day).

2. Add the property next to `succeeded`:

```python
    @property
    def no_game_scheduled(self) -> bool:
        """True when ingestion completed but no LG game was scheduled (a benign off-day).

        Distinct from a genuine failure: requires a clean completed run that produced
        no game and found zero scheduled games. A scheduled-but-unlineup'd game
        (``games_found > 0``) or any analysis ``error`` is NOT an off-day.
        """
        return (
            self.daily_status == "completed"
            and self.game_id is None
            and self.games_found == 0
            and self.error is None
        )
```

3. Include games in `summary()` (add `games={self.games_found}` to the formatted text, e.g. right after `daily={self.daily_status}`).

4. In `run_full_pipeline`, pass `games_found=daily.games_found` to EVERY `FullPipelineResult(...)` constructed after `daily = run_daily_pipeline(...)` — there are four sites: the daily-not-completed early return, the lineup-is-None early return, the HTTPException branch, and the final success return. (For the daily-not-completed site, `daily.games_found` is still a valid int; `no_game_scheduled` stays False there because `daily_status != "completed"`.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_full_pipeline.py -v`
Expected: PASS — new tests pass AND every existing test still passes (existing constructions omit `games_found`, which now defaults to 0; `succeeded` is unchanged).

- [ ] **Step 5: Commit**

```bash
git add app/jobs/full_pipeline.py tests/test_full_pipeline.py
git commit -m "feat(pipeline): surface games_found and no_game_scheduled on FullPipelineResult"
```

Fix any pre-commit lint/type issues; re-commit (no `--no-verify`).

---

## Task 2: `kbo-lab run` exits 0 on a no-game day

**Files:**
- Modify: `app/cli.py`
- Test: `tests/test_cli_run.py` (NEW)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_cli_run.py`. Use Typer's `CliRunner` against the `app` object in `app/cli.py`, monkeypatching `run_full_pipeline` (patch it where `cli.py` looks it up — i.e. `app.cli.run_full_pipeline`) to return a crafted `FullPipelineResult`. Read `app/cli.py` to confirm the import name/path before patching. Three cases:

```python
def test_run_exits_zero_on_off_day(monkeypatch) -> None:
    """No LG game scheduled (completed + games_found 0) exits 0, not a failure."""
    # monkeypatch app.cli.run_full_pipeline -> returns an off-day FullPipelineResult
    # (daily_status="completed", game_id=None, games_found=0)
    result = runner.invoke(app, ["run", "--date", "2026-06-01"])
    assert result.exit_code == 0


def test_run_exits_zero_on_success(monkeypatch) -> None:
    """A fully successful run exits 0."""
    # monkeypatch -> succeeded FullPipelineResult (game_id, eval id present, games_found 1)
    result = runner.invoke(app, ["run", "--date", "2026-05-30"])
    assert result.exit_code == 0


def test_run_exits_one_on_genuine_failure(monkeypatch) -> None:
    """A genuine failure (daily failed, or game present but error) still exits 1."""
    # monkeypatch -> failed FullPipelineResult (daily_status="failed", games_found 0)
    result = runner.invoke(app, ["run", "--date", "2026-06-01"])
    assert result.exit_code == 1
```

Fill in the monkeypatched return values with real `FullPipelineResult(...)` instances (import from `app.jobs.full_pipeline`). Match the existing test style in the repo (test functions, decorator-style mocking where applicable; here `monkeypatch` is simplest). For the genuine-failure case, also add (or instead use) a "game present but error set" variant if cheap (`game_id=1, error="500: boom", games_found=1`) to prove an analysis failure still exits 1.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_cli_run.py -v`
Expected: `test_run_exits_zero_on_off_day` FAILS (current code exits 1 because `succeeded` is False); the other two may already pass.

- [ ] **Step 3: Write minimal implementation**

In `app/cli.py`, change the `run` command's tail from:

```python
    typer.echo(result.summary())
    if not result.succeeded:
        raise typer.Exit(code=1)
```

to:

```python
    typer.echo(result.summary())
    # A completed ingestion with no LG game scheduled (a KBO off-day) is not a
    # failure: the canary must stay green so a real source/pipeline break still
    # stands out. Genuine failures (daily run failed, or a game was found but
    # eval/review errored) keep exiting non-zero.
    if not result.succeeded and not result.no_game_scheduled:
        raise typer.Exit(code=1)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_cli_run.py -v`
Expected: all three PASS.

- [ ] **Step 5: Run full suite for regression**

Run: `uv run pytest -q`
Expected: all pass (previously 440 + the new tests).

- [ ] **Step 6: Commit**

```bash
git add app/cli.py tests/test_cli_run.py
git commit -m "fix(cli): kbo-lab run exits 0 on a no-game day so the canary stays green"
```

Fix any lint/type issues; re-commit (no `--no-verify`).

---

## Task 3: Verify & harness

- [ ] **Step 1:** `cd apps/api && uv run pytest -q` → all pass.
- [ ] **Step 2:** `pre-commit run --all-files` → clean.
- [ ] **Step 3:** `/harness-audit` → structural + semantic green; record the marker before opening a PR. The canary's contract ("`kbo-lab run` exits non-zero on failure, so a broken source or pipeline fails this workflow and alerts") is documented in `.github/workflows/ingestion-canary.yml` and the running-supabase-dev skill. Confirm the off-day exception does not contradict that wording; if the auditor flags it, refine the workflow comment to say "fails on a genuine pipeline error (a no-game off-day exits 0)".

---

## Self-Review notes

- **Coverage:** root cause (succeeded conflates off-day with failure) → Task 1 (`no_game_scheduled`); the manifest-level fix (canary RED on off-days) → Task 2 (CLI exit-code) + its tests; verification/harness → Task 3.
- **Backward-compat:** `games_found` is defaulted (=0), so existing `FullPipelineResult` constructions and `succeeded` semantics are unchanged. Genuine-failure exit-1 behavior is preserved; only the true off-day flips to exit 0.
- **Precision:** `no_game_scheduled` requires `completed` + `game_id is None` + `games_found == 0` + `error is None`, so it can never mask a daily failure, an analysis error, or a scheduled-but-unlineup'd game.
- **Edge (documented, low-risk):** on a completed-run short-circuit re-run of a real game-day, `daily.games_found` is 0, but the persisted lineup is still found → `game_id` is set → `no_game_scheduled` is False (guarded by `game_id is None`) → handled by `succeeded`. So the short-circuit path does not spuriously report an off-day.
