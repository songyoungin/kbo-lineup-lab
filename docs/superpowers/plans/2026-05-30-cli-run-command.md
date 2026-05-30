# `kbo-lab run --date` Single-Command Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a single `kbo-lab run --date YYYY-MM-DD` command that bootstraps, ingests live data, and runs pregame evaluation + postgame review for that date's LG game in one invocation — absorbing the orchestration that currently lives only in the `scripts/seed_real.py` demo helper.

**Architecture:** Extract the bootstrap→ingest→evaluate→review orchestration into a reusable job function `run_full_pipeline(target_date) -> FullPipelineResult` in `app/jobs/full_pipeline.py` (lifted verbatim from `seed_real.main`, but parameterised, returning a structured result instead of printing). A new `kbo-lab run --date` Typer command calls it, echoes the summary, and exits non-zero on a hard failure. `scripts/seed_real.py` is refactored to delegate to `run_full_pipeline` (becoming a thin wrapper) so there is a single implementation. Deleting `seed_real.py` entirely is a separate, already-tracked follow-up.

**Tech Stack:** Python 3.13, FastAPI, SQLAlchemy 2.x, Typer, pytest, uv. Run all commands from `apps/api` with `uv run`. English commits/docstrings/comments (project README convention). Pre-commit hooks (ruff, ruff-format, mypy, bandit, vulture) must pass.

---

## Background

`scripts/seed_real.py` already chains the full real-data flow in one script run: `run_bootstrap()` → `run_daily_pipeline(date)` (live Naver collect) → `replay_evaluation(...)` (pregame eval) → `generate_postgame_review_for_request(...)` (postgame review), auto-discovering the ingested lineup/box snapshots and deriving the eval cutoff from `lineup.announced_at`. The `kbo-lab` CLI has the individual capabilities (`bootstrap`, `ingest-daily`, `ingest-pregame`, `ingest-postgame`) but no single command does the whole chain, and `seed_real` is a "local helper, not a production entrypoint" using a `sys.argv`/`SEED_REAL_DATE` date hack.

This plan promotes that orchestration to a first-class `kbo-lab run --date` command and makes `seed_real` delegate to it (one implementation, no duplication).

**Verified facts the implementation relies on:**
- `run_bootstrap() -> BootstrapResult` (`app/jobs/bootstrap.py`): idempotent migrate + seed; `BootstrapResult.teams_created`, `.model_version_id`.
- `run_daily_pipeline(target_date: date) -> DailyPipelineResult` (`app/jobs/daily_pipeline.py`): `DailyPipelineResult.status` (`"completed"`/`"failed"`), `.error_message`, `.summary()`. Collection only (no eval/review).
- `replay_evaluation(session, request=ReplayEvaluationRequest(game_id, team_id, evaluation_cutoff_at, model_version_id))` (`app/services/pregame_views.py`) → response with `.evaluation_run_id`, `.created`, `.status`.
- `generate_postgame_review_for_request(session, request=GeneratePostgameReviewRequest(evaluation_run_id, box_score_snapshot_id))` (`app/services/postgame_reviews.py`) → response with `.postgame_review_run_id`, `.created`, `.status`.
- Snapshot discovery (as in seed_real): `session.scalars(select(ActualLineupSnapshot)).first()` and `select(BoxScoreSnapshot)`; cutoff = `lineup.announced_at.replace(tzinfo=UTC)`.
- `SessionLocal` (`app/db/session.py`) is bound at import time to `KBO_DATABASE_URL`; the live CLI sets that env before start, so using `SessionLocal` for the eval/review step is correct in production (this matches the working `seed_real`). The daily pipeline and bootstrap manage their own sessions/engines.

## File Structure

- **Create** `apps/api/app/jobs/full_pipeline.py` — `FullPipelineResult` dataclass (with `summary()` + `succeeded` property) and `run_full_pipeline(target_date) -> FullPipelineResult` orchestration.
- **Create** `apps/api/tests/test_full_pipeline.py` — unit tests for `FullPipelineResult.summary()`/`succeeded` and the CLI `run` command wiring (orchestrator patched; no network).
- **Modify** `apps/api/app/cli.py` — add the `run` command (`--date`).
- **Modify** `apps/api/scripts/seed_real.py` — delegate to `run_full_pipeline` (thin wrapper).
- **Modify** `README.md` — show `kbo-lab run --date` as the one-shot path.

---

### Task 1: `run_full_pipeline` orchestration module + result tests

**Files:**
- Create: `apps/api/app/jobs/full_pipeline.py`
- Test: `apps/api/tests/test_full_pipeline.py`

- [ ] **Step 1: Write the failing tests (result type behavior)**

Create `apps/api/tests/test_full_pipeline.py`:

```python
"""Tests for the full-pipeline orchestration result type."""

from __future__ import annotations

from datetime import date

from app.jobs.full_pipeline import FullPipelineResult


def test_full_pipeline_result_succeeded_true_when_all_present() -> None:
    """succeeded is True when the daily run completed and a game was ingested."""
    result = FullPipelineResult(
        target_date=date(2026, 5, 30),
        daily_status="completed",
        teams_created=10,
        game_id=1,
        evaluation_run_id=2,
        postgame_review_run_id=3,
    )
    assert result.succeeded is True
    text = result.summary()
    assert "2026-05-30" in text
    assert "completed" in text
    assert "eval_run=2" in text
    assert "postgame_run=3" in text


def test_full_pipeline_result_not_succeeded_when_daily_failed() -> None:
    """succeeded is False when the daily pipeline did not complete."""
    result = FullPipelineResult(
        target_date=date(2026, 5, 30),
        daily_status="failed",
        teams_created=0,
        game_id=None,
        evaluation_run_id=None,
        postgame_review_run_id=None,
    )
    assert result.succeeded is False
    assert "failed" in result.summary()


def test_full_pipeline_result_not_succeeded_when_no_game() -> None:
    """succeeded is False when the daily run completed but no LG game was found."""
    result = FullPipelineResult(
        target_date=date(2026, 5, 30),
        daily_status="completed",
        teams_created=0,
        game_id=None,
        evaluation_run_id=None,
        postgame_review_run_id=None,
    )
    assert result.succeeded is False
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd /Users/serena/Documents/kbo-lineup-lab/apps/api && uv run pytest tests/test_full_pipeline.py -v`
Expected: FAIL at import — `ModuleNotFoundError: No module named 'app.jobs.full_pipeline'`.

- [ ] **Step 3: Create the orchestration module**

Create `apps/api/app/jobs/full_pipeline.py`:

```python
"""One-shot real-data pipeline: bootstrap + ingest + evaluate + postgame review.

`run_full_pipeline` chains the whole flow for a single date's LG game and returns
a structured result. It is the implementation behind the `kbo-lab run` command and
the `scripts/seed_real.py` demo helper. Live network access (api-gw.sports.naver.com)
is required for the daily ingestion step.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date

from sqlalchemy import select

from app.db.session import SessionLocal
from app.jobs.bootstrap import run_bootstrap
from app.jobs.daily_pipeline import run_daily_pipeline
from app.models.snapshot import ActualLineupSnapshot, BoxScoreSnapshot
from app.schemas.postgame import GeneratePostgameReviewRequest
from app.schemas.pregame import ReplayEvaluationRequest
from app.services.postgame_reviews import generate_postgame_review_for_request
from app.services.pregame_views import replay_evaluation


@dataclass(frozen=True)
class FullPipelineResult:
    """Outcome of a full one-shot run for a single date.

    Attributes:
        target_date: The date that was ingested.
        daily_status: Status of the daily ingestion run ("completed"/"failed").
        teams_created: Teams seeded by the bootstrap step (0 if all existed).
        game_id: Ingested LG game id, or None if no game/lineup was found.
        evaluation_run_id: Pregame evaluation run id, or None if it did not run.
        postgame_review_run_id: Postgame review run id, or None if it did not run.
    """

    target_date: date
    daily_status: str
    teams_created: int
    game_id: int | None
    evaluation_run_id: int | None
    postgame_review_run_id: int | None

    @property
    def succeeded(self) -> bool:
        """True when ingestion completed and a game was ingested."""
        return self.daily_status == "completed" and self.game_id is not None

    def summary(self) -> str:
        """One-line human-readable summary of the run."""
        return (
            f"run {self.target_date.isoformat()}: daily={self.daily_status}, "
            f"game_id={self.game_id}, eval_run={self.evaluation_run_id}, "
            f"postgame_run={self.postgame_review_run_id}"
        )


def run_full_pipeline(target_date: date) -> FullPipelineResult:
    """Bootstrap, ingest live data for target_date, then evaluate + review.

    Args:
        target_date: The date whose LG game should be ingested and analysed.

    Returns:
        FullPipelineResult capturing the daily status and the ids produced. When
        the daily run fails or no lineup is ingested, the evaluation/review ids
        are None and `succeeded` is False.
    """
    boot = run_bootstrap()

    daily = run_daily_pipeline(target_date=target_date)
    if daily.status != "completed":
        return FullPipelineResult(
            target_date=target_date,
            daily_status=daily.status,
            teams_created=boot.teams_created,
            game_id=None,
            evaluation_run_id=None,
            postgame_review_run_id=None,
        )

    evaluation_run_id: int | None = None
    postgame_review_run_id: int | None = None
    game_id: int | None = None

    session = SessionLocal()
    try:
        lineup = session.scalars(select(ActualLineupSnapshot)).first()
        box_score = session.scalars(select(BoxScoreSnapshot)).first()
        if lineup is None:
            return FullPipelineResult(
                target_date=target_date,
                daily_status=daily.status,
                teams_created=boot.teams_created,
                game_id=None,
                evaluation_run_id=None,
                postgame_review_run_id=None,
            )

        game_id = int(lineup.game_id)
        cutoff = lineup.announced_at.replace(tzinfo=UTC)
        eval_resp = replay_evaluation(
            session,
            request=ReplayEvaluationRequest(
                game_id=game_id,
                team_id=int(lineup.team_id),
                evaluation_cutoff_at=cutoff,
                model_version_id=boot.model_version_id,
            ),
        )
        evaluation_run_id = eval_resp.evaluation_run_id

        if box_score is not None:
            postgame_resp = generate_postgame_review_for_request(
                session,
                request=GeneratePostgameReviewRequest(
                    evaluation_run_id=evaluation_run_id,
                    box_score_snapshot_id=int(box_score.id),
                ),
            )
            postgame_review_run_id = postgame_resp.postgame_review_run_id

        session.commit()
    finally:
        session.close()

    return FullPipelineResult(
        target_date=target_date,
        daily_status=daily.status,
        teams_created=boot.teams_created,
        game_id=game_id,
        evaluation_run_id=evaluation_run_id,
        postgame_review_run_id=postgame_review_run_id,
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd /Users/serena/Documents/kbo-lineup-lab/apps/api && uv run pytest tests/test_full_pipeline.py -v`
Expected: all three PASS.

> Note on test scope: `run_full_pipeline` itself drives live Naver ingestion, so it is not unit-tested end-to-end here (the network boundary; its sub-steps — bootstrap, daily pipeline, evaluation, postgame review — are each already tested). The pure result/decision logic is unit-tested above; the CLI wiring is tested in Task 2 with the orchestrator patched; and Task 4 runs a real live smoke test.

- [ ] **Step 5: Commit**

```bash
cd /Users/serena/Documents/kbo-lineup-lab && git add apps/api/app/jobs/full_pipeline.py apps/api/tests/test_full_pipeline.py && git commit -m "feat(jobs): add run_full_pipeline orchestration (bootstrap+ingest+eval+review)

- run_full_pipeline chains run_bootstrap, run_daily_pipeline, replay_evaluation,
  and generate_postgame_review_for_request for one date's LG game
- FullPipelineResult exposes summary() and a succeeded property
- unit tests cover the result type's summary/succeeded logic"
```

---

### Task 2: `kbo-lab run --date` CLI command + seed_real delegation

**Files:**
- Modify: `apps/api/app/cli.py` (add `run` command)
- Modify: `apps/api/scripts/seed_real.py` (delegate to `run_full_pipeline`)
- Test: `apps/api/tests/test_full_pipeline.py` (add CLI wiring tests)

- [ ] **Step 1: Write the failing CLI tests**

Append to `apps/api/tests/test_full_pipeline.py`:

```python
def test_run_cli_command_success(monkeypatch: pytest.MonkeyPatch) -> None:
    """`kbo-lab run --date` echoes the summary and exits 0 on success."""
    from typer.testing import CliRunner

    import app.cli as cli_module
    from app.cli import app as cli_app

    captured: dict[str, date] = {}

    def fake_run_full_pipeline(target_date: date) -> FullPipelineResult:
        captured["date"] = target_date
        return FullPipelineResult(
            target_date=target_date,
            daily_status="completed",
            teams_created=10,
            game_id=1,
            evaluation_run_id=2,
            postgame_review_run_id=3,
        )

    monkeypatch.setattr(cli_module, "run_full_pipeline", fake_run_full_pipeline)
    result = CliRunner().invoke(cli_app, ["run", "--date", "2026-05-30"])

    assert result.exit_code == 0, result.output
    assert captured["date"] == date(2026, 5, 30)
    assert "eval_run=2" in result.output


def test_run_cli_command_fails_nonzero_when_no_game(monkeypatch: pytest.MonkeyPatch) -> None:
    """`kbo-lab run --date` exits non-zero when no game was ingested."""
    from typer.testing import CliRunner

    import app.cli as cli_module
    from app.cli import app as cli_app

    def fake_run_full_pipeline(target_date: date) -> FullPipelineResult:
        return FullPipelineResult(
            target_date=target_date,
            daily_status="completed",
            teams_created=0,
            game_id=None,
            evaluation_run_id=None,
            postgame_review_run_id=None,
        )

    monkeypatch.setattr(cli_module, "run_full_pipeline", fake_run_full_pipeline)
    result = CliRunner().invoke(cli_app, ["run", "--date", "2026-05-30"])

    assert result.exit_code == 1
```

Add `import pytest` and `from datetime import date` at the top of the test file if not already present (Task 1 added `from datetime import date`; add `import pytest`).

- [ ] **Step 2: Run the CLI tests to verify they fail**

Run: `cd /Users/serena/Documents/kbo-lineup-lab/apps/api && uv run pytest tests/test_full_pipeline.py -k cli -v`
Expected: FAIL — Typer exits non-zero with "No such command 'run'".

- [ ] **Step 3: Add the `run` CLI command**

In `apps/api/app/cli.py`:
(a) Add these imports near the other `from app.jobs...` imports at the top:
```python
from datetime import date

from app.jobs.full_pipeline import run_full_pipeline
```
(If `from datetime import date` already exists at the top, do not duplicate it.)

(b) Add this command immediately AFTER the `bootstrap` command (so order reads bootstrap → run → ingest-*):
```python
@app.command("run")
def run(
    date_arg: str = typer.Option(..., "--date", help="ISO date (YYYY-MM-DD) to ingest and analyse."),
) -> None:
    """Bootstrap, ingest live data, and run pregame eval + postgame review for the date."""
    target = date.fromisoformat(date_arg)
    result = run_full_pipeline(target)
    typer.echo(result.summary())
    if not result.succeeded:
        raise typer.Exit(code=1)
```

- [ ] **Step 4: Run the CLI tests to verify they pass**

Run: `cd /Users/serena/Documents/kbo-lineup-lab/apps/api && uv run pytest tests/test_full_pipeline.py -v`
Expected: all PASS (result tests + 2 CLI tests).

- [ ] **Step 5: Refactor `scripts/seed_real.py` to delegate**

The orchestration now lives in `run_full_pipeline`. Replace the body of `scripts/seed_real.py` so it only resolves the date and delegates. Read the current file first, then replace its entire contents with:

```python
"""Ingest REAL LG Twins data for one date and run evaluation + postgame jobs.

Local helper (not a production entrypoint). Thin wrapper over the `kbo-lab run`
command's orchestration (`app.jobs.full_pipeline.run_full_pipeline`): it bootstraps,
runs the live Naver ingestion pipeline for the target date, and produces the
evaluation + postgame review runs so the API/web render against real data.

Run from ``apps/api`` with the same KBO_DATABASE_URL the server uses::

    KBO_DATABASE_URL="sqlite:///./kbo_lineup_lab_real.db" uv run python scripts/seed_real.py 2026-05-30

Prefer ``uv run kbo-lab run --date 2026-05-30``; this script remains for convenience.
Requires live network access to api-gw.sports.naver.com.
"""

from __future__ import annotations

import os
import sys
from datetime import date

from app.jobs.full_pipeline import run_full_pipeline

_DEFAULT_TARGET_DATE = date(2025, 5, 14)  # verified game: Kiwoom (WO) @ LG, final


def _resolve_target_date() -> date:
    """Date to ingest: first CLI arg, else SEED_REAL_DATE env, else the default."""
    if len(sys.argv) > 1:
        return date.fromisoformat(sys.argv[1])
    env_value = os.environ.get("SEED_REAL_DATE")
    if env_value:
        return date.fromisoformat(env_value)
    return _DEFAULT_TARGET_DATE


def main() -> None:
    """Resolve the date and run the full real-data pipeline, printing the summary."""
    result = run_full_pipeline(_resolve_target_date())
    print(result.summary())


if __name__ == "__main__":
    main()
```

- [ ] **Step 6: Verify seed_real imports cleanly**

Run: `cd /Users/serena/Documents/kbo-lineup-lab/apps/api && uv run python -c "import importlib.util; s=importlib.util.spec_from_file_location('sr','scripts/seed_real.py'); m=importlib.util.module_from_spec(s); s.loader.exec_module(m); print('OK', m._DEFAULT_TARGET_DATE)"`
Expected: prints `OK 2025-05-14`, no ImportError. (Imports only; does not run `main()`, so no network.)

- [ ] **Step 7: Run the full_pipeline test file**

Run: `cd /Users/serena/Documents/kbo-lineup-lab/apps/api && uv run pytest tests/test_full_pipeline.py -v`
Expected: all PASS.

- [ ] **Step 8: Commit**

```bash
cd /Users/serena/Documents/kbo-lineup-lab && git add apps/api/app/cli.py apps/api/scripts/seed_real.py apps/api/tests/test_full_pipeline.py && git commit -m "feat(cli): add 'kbo-lab run --date' and delegate seed_real to it

- kbo-lab run --date bootstraps, ingests live data, and runs pregame eval +
  postgame review in one command; exits non-zero on a hard failure
- seed_real is now a thin wrapper over run_full_pipeline (single implementation)
- CLI wiring tests cover the success and no-game (exit 1) paths"
```

---

### Task 3: Document `kbo-lab run --date`

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Promote `kbo-lab run` in the real-data section**

In `README.md`, in the `### Real data ingestion` section, REPLACE the "one-shot local demo" paragraph and its code block (the part describing `scripts/seed_real.py`) — currently:

````markdown
For a one-shot local demo of a single date (bootstrap + ingest + evaluation +
postgame review in one go), `scripts/seed_real.py` accepts the date as an
argument:

```bash
KBO_DATABASE_URL="sqlite:///./kbo_lineup_lab_real.db" uv run python scripts/seed_real.py 2026-05-30
```
````

with:

````markdown
For a one-shot run of a single date (bootstrap + ingest + evaluation + postgame
review in one command), use `kbo-lab run`:

```bash
KBO_DATABASE_URL="sqlite:///./kbo_lineup_lab_real.db" uv run kbo-lab run --date 2026-05-30
```
````

Leave the rest of the section (the per-step `bootstrap`/`ingest-daily`/`ingest-pregame`/`ingest-postgame` block and the scheduling note) unchanged.

- [ ] **Step 2: Verify the docs reference the new command**

Run: `cd /Users/serena/Documents/kbo-lineup-lab && grep -n "kbo-lab run --date" README.md`
Expected: at least one match; and `grep -c "scripts/seed_real.py" README.md` returns `0` (the demo paragraph no longer points at the script).

- [ ] **Step 3: Commit**

```bash
cd /Users/serena/Documents/kbo-lineup-lab && git add README.md && git commit -m "docs: document 'kbo-lab run --date' as the one-shot real-data command"
```

---

### Task 4: Full verification (incl. live smoke)

**Files:** none (verification only)

- [ ] **Step 1: Run the full backend suite**

Run: `cd /Users/serena/Documents/kbo-lineup-lab/apps/api && uv run pytest -q`
Expected: all tests PASS (existing suite + the new full_pipeline tests).

- [ ] **Step 2: Live smoke test of `kbo-lab run --date`**

Run (requires live network to api-gw.sports.naver.com):

```bash
cd /Users/serena/Documents/kbo-lineup-lab/apps/api && \
KBO_DATABASE_URL="sqlite:////tmp/kbo_run_check.db" uv run kbo-lab run --date 2026-05-30 ; echo "exit=$?" ; \
rm -f /tmp/kbo_run_check.db
```

Expected: prints a summary like `run 2026-05-30: daily=completed, game_id=1, eval_run=1, postgame_run=1` and `exit=0`. The throwaway DB is removed afterward. (If the network is unavailable in the run environment, note that the live smoke could not be performed and rely on the suite + CLI wiring tests; do not mark this step done without evidence.)

- [ ] **Step 3: Confirm clean tree**

Run: `cd /Users/serena/Documents/kbo-lineup-lab && git status -s`
Expected: clean (no leftover `.db` files).

---

## Self-Review

**Spec coverage:**
- `kbo-lab run --date` single command → Task 2 (CLI) backed by Task 1 (`run_full_pipeline`). ✓
- Absorbs seed_real orchestration, no duplication → Task 1 extracts it; Task 2 Step 5 makes seed_real delegate. ✓
- Docs → Task 3. ✓
- Live end-to-end proof → Task 4 Step 2. ✓
- Deleting seed_real entirely is intentionally OUT of scope (tracked separately as the "임시 스크립트 삭제" follow-up); seed_real is left as a thin wrapper here. ✓

**Placeholder scan:** No TBD/TODO/"handle edge cases"/"similar to Task N". All code shown in full. The note about not unit-testing `run_full_pipeline` end-to-end is an explicit, justified scope decision (network boundary + tested sub-components + live smoke), not a skipped requirement.

**Type consistency:** `FullPipelineResult(target_date, daily_status, teams_created, game_id, evaluation_run_id, postgame_review_run_id)` with `.succeeded` and `.summary()` is defined in Task 1 and used identically in Task 1 tests, Task 2 CLI command (`result.summary()`, `result.succeeded`), and Task 2 CLI tests. `run_full_pipeline(target_date: date) -> FullPipelineResult` signature matches its call sites in the CLI command and `seed_real.main`. Response field names (`evaluation_run_id`, `postgame_review_run_id`) match the verified schemas. The CLI command is registered as `"run"` and invoked as `["run", "--date", ...]` in the tests. ✓
