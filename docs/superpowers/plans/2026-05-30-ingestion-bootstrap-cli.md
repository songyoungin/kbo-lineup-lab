# Ingestion Bootstrap CLI Entrypoint Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Provide a single, idempotent `kbo-lab bootstrap` command that prepares a fresh database for real ingestion (schema migration + reference data), so operating the app is "bootstrap once → `ingest-daily` per day" instead of a scattered, manual sequence.

**Architecture:** A new `app/jobs/bootstrap.py` module exposes three functions: `upgrade_schema()` (runs Alembic `upgrade head` programmatically against `KBO_DATABASE_URL`), `seed_reference_data(session)` (idempotently seeds the 10 KBO teams + a default `ModelVersion`), and `run_bootstrap()` (orchestrates: migrate, then build an engine from the env URL and seed). A new `kbo-lab bootstrap` CLI command calls `run_bootstrap()`. The existing `scripts/seed_real.py` demo helper is refactored to reuse `run_bootstrap()` (replacing its private `_seed_teams`/`_ensure_model_version`) and to accept an optional date argument so its hardcoded `TARGET_DATE` is no longer an edit-to-change value.

**Tech Stack:** Python 3.13, FastAPI, SQLAlchemy 2.x, Alembic, Typer, pytest, uv. Run all commands from `apps/api` with `uv run`. English commits/docstrings/comments (project README convention). Pre-commit hooks (ruff, ruff-format, mypy, bandit, vulture) must pass.

---

## Background

Today, getting a fresh DB ready for real ingestion requires several manual steps in the right order, and the team/ModelVersion seeding lives only inside the demo helper `scripts/seed_real.py`:

1. `KBO_DATABASE_URL=... uv run alembic upgrade head` (schema)
2. seed 10 teams + a `ModelVersion` — currently ONLY in `seed_real.py` (`_seed_teams`, `_ensure_model_version`)
3. `kbo-lab ingest-daily --date ...` (the real ingestion; assumes 1+2 done)

The CLI (`app/cli.py`) has `ingest-daily`/`ingest-pregame`/`ingest-postgame` but NO bootstrap, so a fresh DB hits FK errors (no teams) or "no ModelVersion" until the demo helper is run. This plan consolidates 1+2 into one idempotent `kbo-lab bootstrap`.

**Facts that drive the implementation:**
- `app/db/session.py` reads `KBO_DATABASE_URL` at **import time** and binds `engine`/`SessionLocal` to it. So bootstrap must NOT rely on `SessionLocal` for a caller-chosen DB; it builds its own engine from the current env value (this also makes it unit-testable with a temp DB via `monkeypatch.setenv`).
- `alembic/env.py` reads `KBO_DATABASE_URL` from the environment on every invocation and overrides `sqlalchemy.url`. So programmatic `command.upgrade(cfg, "head")` targets whatever `KBO_DATABASE_URL` points to at call time.
- `alembic.ini` sets `script_location = %(here)s/alembic`, where `%(here)s` is the ini file's own directory. Locating the ini by absolute path makes the upgrade cwd-independent. The ini lives at `apps/api/alembic.ini`; the `app` package is at `apps/api/app`, so `Path(app.__file__).resolve().parent.parent / "alembic.ini"` is the robust path.
- `TEAM_CODES: dict[str, str]` (10 entries, e.g. `"LG": "LG Twins"`) is defined in `app/ingestion/game_id.py`.
- `scripts/seed_real.py` seeds the default `ModelVersion` as `name="heuristic-v1", version="v1", model_id="internal/lineup-score-v1"`. Reuse those exact values.

## File Structure

- **Create** `apps/api/app/jobs/bootstrap.py` — the bootstrap module (schema upgrade + reference-data seeding + orchestrator + `BootstrapResult`).
- **Create** `apps/api/tests/test_bootstrap.py` — unit tests (seed idempotency in-memory; `run_bootstrap` end-to-end on a temp file DB; CLI smoke test).
- **Modify** `apps/api/app/cli.py` — add the `bootstrap` Typer command.
- **Modify** `apps/api/scripts/seed_real.py` — reuse `run_bootstrap()`, drop `_seed_teams`/`_ensure_model_version`, accept an optional ISO date argument.
- **Modify** `README.md` — document the "bootstrap once → ingest-daily" real-data flow.

---

### Task 1: `bootstrap.py` module + unit tests

**Files:**
- Create: `apps/api/app/jobs/bootstrap.py`
- Test: `apps/api/tests/test_bootstrap.py`

- [ ] **Step 1: Write the failing tests**

Create `apps/api/tests/test_bootstrap.py`:

```python
"""Tests for the bootstrap job (schema migration + reference-data seeding)."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401 — registers models with Base.metadata
from app.db.base import Base
from app.ingestion.game_id import TEAM_CODES
from app.jobs.bootstrap import run_bootstrap, seed_reference_data


def test_seed_reference_data_is_idempotent() -> None:
    """First call seeds all teams + a model version; a second call adds nothing."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)

    with Session(engine) as s:
        first = seed_reference_data(s)
        s.commit()
    with Session(engine) as s:
        second = seed_reference_data(s)
        s.commit()

    assert first.teams_created == len(TEAM_CODES)
    assert second.teams_created == 0
    assert first.model_version_id == second.model_version_id

    with Session(engine) as s:
        team_count = s.execute(text("SELECT COUNT(*) FROM teams")).scalar_one()
    assert team_count == len(TEAM_CODES)


def test_run_bootstrap_creates_schema_and_seeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """run_bootstrap migrates a fresh DB and seeds reference data, idempotently."""
    db_file = tmp_path / "boot.db"
    monkeypatch.setenv("KBO_DATABASE_URL", f"sqlite:///{db_file}")

    first = run_bootstrap()
    assert first.teams_created == len(TEAM_CODES)
    assert first.model_version_id > 0

    # Schema was created by Alembic and rows are present.
    engine = create_engine(f"sqlite:///{db_file}")
    with engine.connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM teams")).scalar_one() == len(TEAM_CODES)
    engine.dispose()

    # Re-running is safe and seeds nothing new.
    second = run_bootstrap()
    assert second.teams_created == 0
    assert second.model_version_id == first.model_version_id
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd /Users/serena/Documents/kbo-lineup-lab/apps/api && uv run pytest tests/test_bootstrap.py -v`
Expected: FAIL at import — `ModuleNotFoundError: No module named 'app.jobs.bootstrap'` (the module does not exist yet).

- [ ] **Step 3: Create the bootstrap module**

Create `apps/api/app/jobs/bootstrap.py`:

```python
"""Idempotent database bootstrap: schema migration + reference-data seeding.

`run_bootstrap` is the single entrypoint a fresh deployment runs once before
ingestion. It applies Alembic migrations against ``KBO_DATABASE_URL`` and then
seeds the static reference data (the 10 KBO teams and a default ModelVersion)
that ingestion and evaluation depend on. Every step is idempotent.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

import app  # for locating alembic.ini relative to the installed package
from alembic import command
from alembic.config import Config
from app.ingestion.game_id import TEAM_CODES
from app.models.evaluation import ModelVersion
from app.models.team import Team

_DEFAULT_DATABASE_URL = "sqlite:///./kbo_lineup_lab.db"
_DEFAULT_MODEL_NAME = "heuristic-v1"
_DEFAULT_MODEL_VERSION = "v1"
_DEFAULT_MODEL_ID = "internal/lineup-score-v1"


@dataclass(frozen=True)
class BootstrapResult:
    """Outcome of a bootstrap run.

    Attributes:
        teams_created: Number of Team rows inserted this run (0 if all existed).
        model_version_id: PK of the existing-or-created default ModelVersion.
    """

    teams_created: int
    model_version_id: int


def _database_url() -> str:
    """Return the configured database URL (same source as app.db.session)."""
    return os.environ.get("KBO_DATABASE_URL", _DEFAULT_DATABASE_URL)


def _alembic_config() -> Config:
    """Build an Alembic Config pointing at the project's alembic.ini.

    Located by absolute path so the upgrade works regardless of cwd. env.py reads
    KBO_DATABASE_URL from the environment, so no URL is set here.
    """
    ini_path = Path(app.__file__).resolve().parent.parent / "alembic.ini"
    return Config(str(ini_path))


def upgrade_schema() -> None:
    """Apply all Alembic migrations (``upgrade head``) against KBO_DATABASE_URL."""
    command.upgrade(_alembic_config(), "head")


def seed_reference_data(session: Session) -> BootstrapResult:
    """Idempotently seed the 10 KBO teams and a default ModelVersion.

    Args:
        session: Active SQLAlchemy session (caller commits).

    Returns:
        BootstrapResult with the number of teams created and the model version id.
    """
    teams_created = 0
    for code, name in TEAM_CODES.items():
        existing = session.execute(select(Team).where(Team.code == code)).scalars().first()
        if existing is None:
            session.add(Team(code=code, name=name))
            teams_created += 1
    session.flush()

    model_version = session.execute(select(ModelVersion)).scalars().first()
    if model_version is None:
        model_version = ModelVersion(
            name=_DEFAULT_MODEL_NAME,
            version=_DEFAULT_MODEL_VERSION,
            model_id=_DEFAULT_MODEL_ID,
        )
        session.add(model_version)
        session.flush()

    return BootstrapResult(teams_created=teams_created, model_version_id=int(model_version.id))


def run_bootstrap() -> BootstrapResult:
    """Migrate the schema and seed reference data against KBO_DATABASE_URL.

    Builds its own engine from the current environment URL rather than reusing
    app.db.session.SessionLocal (which is bound at import time), so the target
    database always matches KBO_DATABASE_URL at call time.

    Returns:
        BootstrapResult summarising what was seeded.
    """
    upgrade_schema()

    url = _database_url()
    connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
    engine = create_engine(url, connect_args=connect_args)
    try:
        with Session(engine) as session:
            result = seed_reference_data(session)
            session.commit()
        return result
    finally:
        engine.dispose()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd /Users/serena/Documents/kbo-lineup-lab/apps/api && uv run pytest tests/test_bootstrap.py -v`
Expected: both tests PASS. (`test_run_bootstrap_creates_schema_and_seeds` actually runs Alembic against a temp file DB.)

- [ ] **Step 5: Commit**

```bash
cd /Users/serena/Documents/kbo-lineup-lab && git add apps/api/app/jobs/bootstrap.py apps/api/tests/test_bootstrap.py && git commit -m "feat(jobs): add idempotent bootstrap (schema migrate + reference-data seed)

- run_bootstrap applies Alembic migrations and seeds the 10 KBO teams plus a
  default ModelVersion against KBO_DATABASE_URL
- seed_reference_data is idempotent; builds its own engine so the target DB
  matches the env URL at call time
- unit tests cover seed idempotency and end-to-end migrate+seed on a temp DB"
```

---

### Task 2: `kbo-lab bootstrap` CLI command + seed_real refactor

**Files:**
- Modify: `apps/api/app/cli.py` (add `bootstrap` command)
- Modify: `apps/api/scripts/seed_real.py` (reuse `run_bootstrap`; optional date arg)
- Test: `apps/api/tests/test_bootstrap.py` (add a CLI smoke test)

- [ ] **Step 1: Write the failing CLI test**

Append to `apps/api/tests/test_bootstrap.py`:

```python
def test_bootstrap_cli_command(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`kbo-lab bootstrap` exits 0 and reports the seeded teams against a fresh DB."""
    from typer.testing import CliRunner

    from app.cli import app as cli_app

    monkeypatch.setenv("KBO_DATABASE_URL", f"sqlite:///{tmp_path / 'cli.db'}")
    result = CliRunner().invoke(cli_app, ["bootstrap"])

    assert result.exit_code == 0, result.output
    assert "teams" in result.output.lower()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd /Users/serena/Documents/kbo-lineup-lab/apps/api && uv run pytest tests/test_bootstrap.py::test_bootstrap_cli_command -v`
Expected: FAIL — Typer exits non-zero with "No such command 'bootstrap'" (command not registered yet).

- [ ] **Step 3: Add the `bootstrap` CLI command**

In `apps/api/app/cli.py`, add the import near the other job imports (top of file, after the existing `from app.jobs...` lines):

```python
from app.jobs.bootstrap import run_bootstrap
```

Then add this command (place it FIRST, before `ingest_daily`, so it reads as the setup step):

```python
@app.command("bootstrap")
def bootstrap() -> None:
    """Prepare a fresh database: apply migrations and seed teams + a model version."""
    result = run_bootstrap()
    typer.echo(
        f"bootstrap: schema migrated; teams created={result.teams_created}; "
        f"model_version_id={result.model_version_id}"
    )
```

- [ ] **Step 4: Run the CLI test to verify it passes**

Run: `cd /Users/serena/Documents/kbo-lineup-lab/apps/api && uv run pytest tests/test_bootstrap.py::test_bootstrap_cli_command -v`
Expected: PASS.

- [ ] **Step 5: Refactor `seed_real.py` to reuse bootstrap + accept a date argument**

In `apps/api/scripts/seed_real.py`, make these changes:

(a) Replace the imports of the now-shared seeding with the bootstrap import. Remove the `from app.ingestion.game_id import TEAM_CODES`, `from app.models.evaluation import ModelVersion`, and `from app.models.team import Team` imports IF they become unused after the edits below, and add:

```python
import os
import sys
from app.jobs.bootstrap import run_bootstrap
```

(b) Delete the `_seed_teams` and `_ensure_model_version` functions entirely (their logic now lives in `app.jobs.bootstrap.seed_reference_data`).

(c) Replace the module-level constant line:

```python
TARGET_DATE = date(2025, 5, 14)  # verified game: Kiwoom (WO) @ LG, final
```

with a small resolver that accepts an ISO date from argv or the `SEED_REAL_DATE` env var, defaulting to the previous value:

```python
_DEFAULT_TARGET_DATE = date(2025, 5, 14)  # verified game: Kiwoom (WO) @ LG, final


def _resolve_target_date() -> date:
    """Date to ingest: first CLI arg, else SEED_REAL_DATE env, else the default."""
    if len(sys.argv) > 1:
        return date.fromisoformat(sys.argv[1])
    env_value = os.environ.get("SEED_REAL_DATE")
    if env_value:
        return date.fromisoformat(env_value)
    return _DEFAULT_TARGET_DATE
```

(d) Rewrite `main()`'s step 1 (teams + ModelVersion) and step 3's model-version lookup to use `run_bootstrap()`. The new `main()` body:

```python
def main() -> None:
    """Bootstrap, ingest real data live for the target date, then eval + postgame."""
    target_date = _resolve_target_date()

    # 1. Schema + reference data (idempotent).
    boot = run_bootstrap()
    model_version_id = boot.model_version_id
    print(f"bootstrap: teams created={boot.teams_created}; model_version_id={model_version_id}")

    # 2. Real ingestion pipeline (opens its own session; live Naver fetch).
    result = run_daily_pipeline(target_date=target_date)
    print("pipeline:", result.summary())
    if result.status != "completed":
        print(f"pipeline did not complete: {result.error_message}")
        return

    # 3. Evaluation + postgame review on the ingested snapshots.
    session = SessionLocal()
    try:
        lineup = session.scalars(select(ActualLineupSnapshot)).first()
        box_score = session.scalars(select(BoxScoreSnapshot)).first()
        if lineup is None:
            print("no lineup snapshot ingested; skipping eval/postgame")
            return

        cutoff = lineup.announced_at.replace(tzinfo=UTC)
        try:
            eval_resp = replay_evaluation(
                session,
                request=ReplayEvaluationRequest(
                    game_id=int(lineup.game_id),
                    team_id=int(lineup.team_id),
                    evaluation_cutoff_at=cutoff,
                    model_version_id=model_version_id,
                ),
            )
            print(
                f"evaluation: id={eval_resp.evaluation_run_id} "
                f"created={eval_resp.created} status={eval_resp.status}"
            )
        except Exception as exc:  # noqa: BLE001 - local helper, report and continue
            print(f"evaluation failed: {type(exc).__name__}: {exc}")
            session.rollback()
            return

        if box_score is not None:
            try:
                postgame_resp = generate_postgame_review_for_request(
                    session,
                    request=GeneratePostgameReviewRequest(
                        evaluation_run_id=eval_resp.evaluation_run_id,
                        box_score_snapshot_id=int(box_score.id),
                    ),
                )
                print(
                    f"postgame: id={postgame_resp.postgame_review_run_id} "
                    f"created={postgame_resp.created} status={postgame_resp.status}"
                )
            except Exception as exc:  # noqa: BLE001 - local helper, report and continue
                print(f"postgame failed: {type(exc).__name__}: {exc}")

        session.commit()
        print(f"done. demo game_id={int(lineup.game_id)}")
    finally:
        session.close()
```

Also update the module docstring's usage example to show the new options:

```
    KBO_DATABASE_URL="sqlite:///./kbo_lineup_lab_real.db" uv run python scripts/seed_real.py 2026-05-30
```

After editing, verify no now-unused imports remain (e.g. `select` is still used in step 3; `ModelVersion`/`Team`/`TEAM_CODES` should be removed if no longer referenced — ruff/vulture in pre-commit will flag leftovers).

- [ ] **Step 6: Verify seed_real still imports and parses**

Run: `cd /Users/serena/Documents/kbo-lineup-lab/apps/api && uv run python -c "import ast, pathlib; ast.parse(pathlib.Path('scripts/seed_real.py').read_text()); import importlib.util; spec=importlib.util.spec_from_file_location('seed_real','scripts/seed_real.py'); m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m); print('seed_real import OK; default date', m._resolve_target_date.__name__)"`
Expected: prints `seed_real import OK; default date _resolve_target_date` with no ImportError. (This imports the module without running `main()`, confirming the refactor is import-clean.)

- [ ] **Step 7: Run the bootstrap tests + full quick check**

Run: `cd /Users/serena/Documents/kbo-lineup-lab/apps/api && uv run pytest tests/test_bootstrap.py -v`
Expected: all three tests PASS.

- [ ] **Step 8: Commit**

```bash
cd /Users/serena/Documents/kbo-lineup-lab && git add apps/api/app/cli.py apps/api/scripts/seed_real.py apps/api/tests/test_bootstrap.py && git commit -m "feat(cli): add 'kbo-lab bootstrap' and reuse it from seed_real

- kbo-lab bootstrap runs run_bootstrap (migrate + seed teams/model version)
- seed_real reuses run_bootstrap (drops its private seeding helpers) and accepts
  an optional ISO date via argv or SEED_REAL_DATE instead of a hardcoded constant
- add CLI smoke test for the bootstrap command"
```

---

### Task 3: Document the real-data operating flow

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Add a "Real data ingestion" subsection**

In `README.md`, under the `## Development` section, AFTER the `### Backend` fenced block (the one ending with `cd apps/api && uv run pytest`), insert this new subsection:

````markdown
### Real data ingestion

Bootstrap a database once (idempotent: applies migrations and seeds the 10 KBO
teams + a default model version), then ingest per day. All commands read
`KBO_DATABASE_URL`; use the same value everywhere.

```bash
cd apps/api
export KBO_DATABASE_URL="sqlite:///./kbo_lineup_lab_real.db"

# One-time setup (safe to re-run)
uv run kbo-lab bootstrap

# Daily: collect schedule + each LG game's lineup, stats, and box score (live Naver)
uv run kbo-lab ingest-daily --date 2026-05-30

# Per game (game id is the Naver game id, e.g. 20260530HTLG02026)
uv run kbo-lab ingest-pregame  --game-id <game-id>   # lineup + pregame evaluation
uv run kbo-lab ingest-postgame --game-id <game-id>   # box score + postgame review
```

For a one-shot local demo of a single date (bootstrap + ingest + evaluation +
postgame review in one go), `scripts/seed_real.py` accepts the date as an
argument:

```bash
KBO_DATABASE_URL="sqlite:///./kbo_lineup_lab_real.db" uv run python scripts/seed_real.py 2026-05-30
```

> Scheduling is external for now (e.g. a cron job invoking `kbo-lab ingest-daily`);
> there is no built-in scheduler daemon yet.
````

- [ ] **Step 2: Verify the section renders and the commands match the CLI**

Run: `cd /Users/serena/Documents/kbo-lineup-lab && grep -n "kbo-lab bootstrap\|Real data ingestion" README.md`
Expected: both strings present (the subsection was added).

- [ ] **Step 3: Commit**

```bash
cd /Users/serena/Documents/kbo-lineup-lab && git add README.md && git commit -m "docs: document bootstrap-once then ingest-daily real-data flow"
```

---

### Task 4: Full verification

**Files:** none (verification only)

- [ ] **Step 1: Run the full backend suite**

Run: `cd /Users/serena/Documents/kbo-lineup-lab/apps/api && uv run pytest -q`
Expected: all tests PASS (existing suite + the 3 new bootstrap tests).

- [ ] **Step 2: Exercise the bootstrap CLI end-to-end against a throwaway DB**

Run:

```bash
cd /Users/serena/Documents/kbo-lineup-lab/apps/api && \
KBO_DATABASE_URL="sqlite:////tmp/kbo_bootstrap_check.db" uv run kbo-lab bootstrap && \
KBO_DATABASE_URL="sqlite:////tmp/kbo_bootstrap_check.db" uv run kbo-lab bootstrap && \
rm -f /tmp/kbo_bootstrap_check.db
```

Expected: first run prints `teams created=10`; second run prints `teams created=0` (idempotent); both exit 0. The temp DB is removed at the end.

- [ ] **Step 3: Confirm no stray files**

Run: `cd /Users/serena/Documents/kbo-lineup-lab && git status -s`
Expected: only the intended tracked changes (no leftover `.db` files; the verification DB was under `/tmp` and removed).

---

## Self-Review

**Spec coverage:**
- Single bootstrap entrypoint (schema + teams + model) → Task 1 (`run_bootstrap`) + Task 2 (`kbo-lab bootstrap`). ✓
- Reuse from the demo helper → Task 2 Step 5 (seed_real refactor). ✓
- Address the hardcoded-date pain → Task 2 Step 5 (`_resolve_target_date` via argv/env). ✓
- Document the operating flow → Task 3. ✓
- Scheduler intentionally deferred → noted in Task 3 docs (out of scope). ✓

**Placeholder scan:** No TBD/TODO/"handle edge cases"/"similar to Task N". All code shown in full. The `try/except Exception` blocks in `seed_real.py` are pre-existing in that local helper and carry their existing `# noqa: BLE001` justification — not new placeholders.

**Type consistency:** `BootstrapResult(teams_created: int, model_version_id: int)` is defined in Task 1 and consumed in Task 2 (`result.teams_created`, `result.model_version_id`) and `seed_real` (`boot.teams_created`, `boot.model_version_id`). `run_bootstrap() -> BootstrapResult` and `seed_reference_data(session) -> BootstrapResult` signatures match across tasks. `upgrade_schema() -> None`. The CLI command name `"bootstrap"` matches the test invocation `["bootstrap"]`. `_resolve_target_date() -> date` is referenced by the verification command in Task 2 Step 6. ✓
