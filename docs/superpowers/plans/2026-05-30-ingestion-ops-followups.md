# Ingestion Ops Follow-ups: remove seed_real + scheduled ingestion canary

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the now-redundant `scripts/seed_real.py` (superseded by `kbo-lab run --date`) and add a daily GitHub Actions workflow that runs `kbo-lab run` against live Naver data as a canary, surfacing source/ingestion breakage.

**Architecture:** Two small, independent changes. (1) Delete `apps/api/scripts/seed_real.py` and fix the one docstring reference to it in `app/jobs/full_pipeline.py`; `scripts/seed_demo.py` is unrelated (fixture demo loader, used by the running-fixture-demo skill) and stays. (2) Add `.github/workflows/ingestion-canary.yml`: a scheduled (daily) + manually-dispatchable workflow that bootstraps + ingests + evaluates + reviews yesterday's KST LG game via `kbo-lab run`. It uses a throwaway SQLite DB by default (GitHub runners are ephemeral) or a `KBO_DATABASE_URL` repo secret if configured; because `kbo-lab run` exits non-zero on failure, a broken Naver source fails the workflow and alerts.

**Tech Stack:** Python 3.13, uv, Typer CLI (`kbo-lab`), GitHub Actions. The repo already runs GitHub Actions (`.github/workflows/{pre-commit,test}.yaml`) with `actions/checkout@v6.0.2`, `actions/setup-python@v6.2.0` (3.13), `astral-sh/setup-uv@v8.1.0`, `uv sync`. Match those exact versions/conventions. English commits/comments. Pre-commit hooks (incl. a GitHub-workflow validator) must pass.

---

## Background

- `scripts/seed_real.py` is now a ~42-line thin wrapper over `app.jobs.full_pipeline.run_full_pipeline`, fully superseded by `kbo-lab run --date` (added in PR #32). The only reference to it outside the file is one line in `app/jobs/full_pipeline.py`'s module docstring (verified via grep). The README no longer references it. So deleting it is clean.
- `scripts/seed_demo.py` is the **fixture** demo loader (referenced by `.claude/skills/running-fixture-demo/SKILL.md`) — a different purpose; it is NOT touched here.
- The repo schedules nothing today; `kbo-lab ingest-daily`/`run` are manual. A GitHub Actions cron workflow is the natural "workflow" in this repo. GitHub runners are ephemeral, so the workflow's primary value is a **canary**: prove live Naver ingestion still works end-to-end every day (and persist to a real DB only if a `KBO_DATABASE_URL` secret is set).

## File Structure

- **Delete** `apps/api/scripts/seed_real.py`.
- **Modify** `apps/api/app/jobs/full_pipeline.py` — drop the `scripts/seed_real.py` mention from the module docstring.
- **Create** `.github/workflows/ingestion-canary.yml` — scheduled + `workflow_dispatch` workflow running `kbo-lab run`.

---

### Task 1: Remove `seed_real.py`

**Files:**
- Delete: `apps/api/scripts/seed_real.py`
- Modify: `apps/api/app/jobs/full_pipeline.py` (module docstring, ~line 5)

- [ ] **Step 1: Confirm the only external reference**

Run: `cd /Users/serena/Documents/kbo-lineup-lab && grep -rn "seed_real" --include="*.py" --include="*.md" --include="*.yaml" --include="*.yml" . | grep -v node_modules | grep -v "docs/superpowers/plans" | grep -v "scripts/seed_real.py:"`
Expected: exactly one line — `apps/api/app/jobs/full_pipeline.py:5:...the `scripts/seed_real.py` demo helper...`. (If other references appear, stop and report — the plan assumed only this one.)

- [ ] **Step 2: Update the docstring in `full_pipeline.py`**

In `apps/api/app/jobs/full_pipeline.py`, the module docstring's second sentence currently reads:

```
`run_full_pipeline` chains the whole flow for a single date's LG game and returns
a structured result. It is the implementation behind the `kbo-lab run` command and
the `scripts/seed_real.py` demo helper. Live network access (api-gw.sports.naver.com)
is required for the daily ingestion step.
```

Replace that sentence so it no longer references the deleted script:

```
`run_full_pipeline` chains the whole flow for a single date's LG game and returns
a structured result. It is the implementation behind the `kbo-lab run` command.
Live network access (api-gw.sports.naver.com) is required for the daily ingestion step.
```

- [ ] **Step 3: Delete the script**

Run: `cd /Users/serena/Documents/kbo-lineup-lab && git rm apps/api/scripts/seed_real.py`
Expected: `rm 'apps/api/scripts/seed_real.py'`.

- [ ] **Step 4: Verify no references remain and the suite still passes**

Run: `cd /Users/serena/Documents/kbo-lineup-lab && grep -rn "seed_real" --include="*.py" apps/api | grep -v "docs/" || echo "no seed_real references in apps/api"`
Expected: `no seed_real references in apps/api` (the docstring line was removed; the file is gone).

Run: `cd /Users/serena/Documents/kbo-lineup-lab/apps/api && uv run pytest -q`
Expected: all tests PASS (nothing imported `seed_real`).

- [ ] **Step 5: Commit**

```bash
cd /Users/serena/Documents/kbo-lineup-lab && git add apps/api/app/jobs/full_pipeline.py && git commit -m "chore: remove seed_real.py (superseded by 'kbo-lab run --date')

- delete the thin-wrapper script now fully replaced by the kbo-lab run command
- drop its mention from the run_full_pipeline module docstring
- seed_demo.py (fixture demo loader) is unaffected"
```

(The `git rm` already staged the deletion; `git add` stages the docstring edit. Both are included in the commit.)

---

### Task 2: Scheduled ingestion canary workflow

**Files:**
- Create: `.github/workflows/ingestion-canary.yml`

- [ ] **Step 1: Create the workflow file**

Create `.github/workflows/ingestion-canary.yml`:

```yaml
name: ingestion-canary

# Daily canary: run the full real-data pipeline against live Naver to prove
# ingestion still works end-to-end. GitHub runners are ephemeral, so this uses a
# throwaway SQLite DB unless a KBO_DATABASE_URL repo secret is configured (in which
# case results persist). `kbo-lab run` exits non-zero on failure, so a broken
# source or pipeline fails this workflow and alerts.

on:
  schedule:
    # 18:00 UTC = 03:00 KST — ingest the KST day whose games have finished.
    - cron: "0 18 * * *"
  workflow_dispatch:
    inputs:
      date:
        description: "ISO date to ingest (YYYY-MM-DD). Defaults to yesterday (KST)."
        required: false
        default: ""

permissions:
  contents: read

jobs:
  ingest:
    name: kbo-lab run (live Naver)
    runs-on: ubuntu-latest
    env:
      KBO_DATABASE_URL: ${{ secrets.KBO_DATABASE_URL || 'sqlite:////tmp/kbo_lineup_lab_canary.db' }}
    steps:
      - uses: actions/checkout@v6.0.2

      - uses: actions/setup-python@v6.2.0
        with:
          python-version: "3.13"

      - uses: astral-sh/setup-uv@v8.1.0
        with:
          enable-cache: true

      - name: Install dependencies
        run: uv sync

      - name: Resolve target date (yesterday KST unless provided)
        id: date
        run: |
          if [ -n "${{ inputs.date }}" ]; then
            echo "value=${{ inputs.date }}" >> "$GITHUB_OUTPUT"
          else
            echo "value=$(TZ=Asia/Seoul date -d 'yesterday' +%F)" >> "$GITHUB_OUTPUT"
          fi

      - name: Run ingestion pipeline (kbo-lab run)
        run: cd apps/api && uv run kbo-lab run --date "${{ steps.date.outputs.value }}"
```

Rationale captured in the file's comments: ephemeral-by-default DB, optional persistence via secret, exit-code-as-canary, 18:00 UTC / 03:00 KST timing, `inputs.date` override for manual dispatch.

- [ ] **Step 2: Validate the workflow via pre-commit**

Run (from repo root): `pre-commit run --files .github/workflows/ingestion-canary.yml`
Expected: the GitHub-workflow validation / YAML hooks PASS (no schema errors). If pre-commit is not installed in `.venv`, stop and tell the user (per project convention) rather than running validators directly.

- [ ] **Step 3: Sanity-check the YAML parses and key fields are present**

Run: `cd /Users/serena/Documents/kbo-lineup-lab && python3 -c "import yaml,sys; d=yaml.safe_load(open('.github/workflows/ingestion-canary.yml')); assert 'schedule' in d['on'] and 'workflow_dispatch' in d['on']; assert d['jobs']['ingest']['steps'][-1]['run'].strip().endswith('kbo-lab run --date \"\${{ steps.date.outputs.value }}\"'); print('workflow OK')"`
Expected: prints `workflow OK`. (Note: PyYAML parses the `on:` key as the boolean `True` in some versions — if the assertion on `d['on']` raises a KeyError because the key is `True`, adjust the check to `d[True]`; this is a known PyYAML quirk and not a workflow defect. Confirm the file content is correct regardless.)

- [ ] **Step 4: Commit**

```bash
cd /Users/serena/Documents/kbo-lineup-lab && git add .github/workflows/ingestion-canary.yml && git commit -m "ci: add daily ingestion canary workflow

- scheduled (03:00 KST) + workflow_dispatch run of 'kbo-lab run' against live
  Naver to prove ingestion works end-to-end; fails (alerts) on breakage
- ephemeral throwaway SQLite by default; persists if KBO_DATABASE_URL secret set
- ingests yesterday (KST) by default; manual dispatch accepts a date input"
```

---

### Task 3: Full verification

**Files:** none (verification only)

- [ ] **Step 1: Full backend suite**

Run: `cd /Users/serena/Documents/kbo-lineup-lab/apps/api && uv run pytest -q`
Expected: all tests PASS.

- [ ] **Step 2: Confirm seed_real is gone and seed_demo is intact**

Run: `cd /Users/serena/Documents/kbo-lineup-lab && ls apps/api/scripts/`
Expected: `seed_real.py` absent; `seed_demo.py` present.

- [ ] **Step 3: Confirm both changed areas are clean**

Run: `cd /Users/serena/Documents/kbo-lineup-lab && git status -s && git log --oneline main..HEAD`
Expected: clean working tree; two commits (chore: remove seed_real; ci: add canary workflow).

> Note: the canary workflow only triggers on `schedule`/`workflow_dispatch`, so it will NOT run on the PR. After merge, it can be verified live with `gh workflow run ingestion-canary.yml` (a manual dispatch on main) — this is a post-merge check, not part of CI.

---

## Self-Review

**Spec coverage:**
- #8 remove temp script → Task 1 (delete seed_real, fix docstring, keep seed_demo). ✓
- #9 cron workflow → Task 2 (GitHub Actions canary). ✓
- Verification → Task 3 + the post-merge `workflow_dispatch` note. ✓

**Placeholder scan:** No TBD/TODO/"handle edge cases". The workflow YAML is complete. The PyYAML `on`→`True` note is a real, documented quirk to keep the sanity-check honest, not a placeholder.

**Type consistency:** N/A (no new Python types). The CLI command invoked is `kbo-lab run --date <value>` — matches the command added in PR #32 (`@app.command("run")`, `--date` option). The workflow's action versions (`checkout@v6.0.2`, `setup-python@v6.2.0`, `setup-uv@v8.1.0`) match `.github/workflows/test.yaml`. The DB-default expression and `cd apps/api && uv run kbo-lab` invocation match the repo's run conventions.
