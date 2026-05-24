# Project Scaffold Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Create the initial Python backend and formal frontend monorepo scaffold, plus a unified pre-commit and CI gate.

**Architecture:** Use a two-app repository: `apps/api` for FastAPI and `apps/web` for Next.js. The repository root is a `uv` workspace that owns shared developer tooling (pre-commit). Keep shared documentation at the repository root and avoid domain logic in the frontend.

**Tech Stack:** Python 3.13, FastAPI, SQLAlchemy, Alembic, pytest, Next.js, TypeScript, Tailwind, shadcn/ui, uv (workspace), pre-commit, ruff, mypy, bandit, detect-secrets, vulture, GitHub Actions.

---

## Scope

Create only the runnable skeleton and the pre-commit / CI gate. Do not implement baseball domain logic in this task.

## Files

- Create: `pyproject.toml` (root, uv workspace + pre-commit dev dep)
- Create: `.gitignore`
- Create: `.pre-commit-config.yaml`
- Create: `.secrets.baseline`
- Create: `.github/workflows/pre-commit.yaml`
- Create: `apps/api/pyproject.toml`
- Create: `apps/api/app/main.py`
- Create: `apps/api/app/__init__.py`
- Create: `apps/api/tests/__init__.py`
- Create: `apps/api/tests/test_health.py`
- Create: `apps/web/package.json`
- Create: `apps/web/app/page.tsx`
- Create: `apps/web/app/layout.tsx`
- Create: `apps/web/app/globals.css`
- Create: `apps/web/tsconfig.json`
- Create: `apps/web/next.config.ts`
- Create: `apps/web/tailwind.config.ts`
- Create: `apps/web/postcss.config.mjs`
- Create: `apps/web/.eslintrc.json` (or `eslint.config.mjs` for flat config)
- Modify: `README.md`

## Steps

### Backend and frontend scaffold

- [ ] Create root `pyproject.toml` declaring `requires-python = ">=3.13"`, a `[tool.uv.workspace]` table listing `apps/api`, and `pre-commit` as a dev dependency. Mark the root package as non-publishable.
- [ ] Create a root `.gitignore` covering `.venv/`, `__pycache__/`, `*.pyc`, `node_modules/`, `.next/`, `apps/web/out/`, `.env*`, `*.db`, `.DS_Store`, and `uv.lock` is NOT ignored.
- [ ] Create `apps/api/pyproject.toml` with `requires-python = ">=3.13"`, FastAPI and pytest dependencies, and `[tool.ruff]`, `[tool.mypy]`, `[tool.bandit]` configuration sections.
- [ ] Add `GET /health` returning `{"status": "ok"}` in `apps/api/app/main.py`.
- [ ] Add a pytest health check using FastAPI `TestClient`.
- [ ] Create `apps/web` with a minimal Next.js TypeScript app (App Router).
- [ ] Add a placeholder homepage titled `KBO Lineup Lab`.
- [ ] Add `lint` and `format:check` scripts to `apps/web/package.json` (eslint + prettier).
- [ ] Update `README.md` with backend and frontend run commands plus a `pre-commit` section.
- [ ] Run `uv sync` at the repo root.
- [ ] Run `cd apps/api && uv run pytest`.
- [ ] Run `cd apps/web && npm install && npm run lint`.
- [ ] Commit with `chore(scaffold): initialize api and web apps`.

### Pre-commit and CI gate

- [ ] Create `.pre-commit-config.yaml` with these hook groups (resolve `rev:` to the latest stable tag at implementation time):
  - `pre-commit/pre-commit-hooks`: `trailing-whitespace`, `end-of-file-fixer`, `check-yaml`, `check-json`, `check-toml`, `check-merge-conflict`, `check-case-conflict`, `check-added-large-files` (exclude `uv\.lock`), `mixed-line-ending`.
  - `astral-sh/ruff-pre-commit`: `ruff` (with `--fix --exit-non-zero-on-fix`) and `ruff-format`, scoped to `files: ^apps/api/.*\.py$`.
  - `PyCQA/bandit`: `args: ["-c", "apps/api/pyproject.toml", "-r", "apps/api/app", "--severity-level", "medium"]`, scoped to `files: ^apps/api/app/.*\.py$`.
  - `Yelp/detect-secrets`: `detect-secrets` with `--baseline .secrets.baseline`.
  - `pre-commit/mirrors-mypy`: scoped to `files: ^apps/api/.*\.py$`, `args: [--config-file=apps/api/pyproject.toml, --strict, --ignore-missing-imports]`, `additional_dependencies: []` (extend later as type stubs become necessary).
  - `jendrikseipp/vulture`: `args: ["apps/api/app/", "--min-confidence", "80"]`.
  - `python-jsonschema/check-jsonschema`: `check-github-workflows` and `check-dependabot`.
  - `repo: local` hooks for the frontend: `eslint` and `prettier`, each running `bash -c 'cd apps/web && npx --no-install <tool>'` with `language: system`, `pass_filenames: false`, and `files: ^apps/web/.*\.(js|jsx|ts|tsx|json|css|md)$` (Prettier) / `^apps/web/.*\.(js|jsx|ts|tsx)$` (ESLint).
- [ ] Generate `.secrets.baseline` with `uv run detect-secrets scan > .secrets.baseline`.
- [ ] Create `.github/workflows/pre-commit.yaml`: trigger on `pull_request` to `main`, set up Python 3.13, set up Node 20, install uv, run `uv sync`, run `npm install --prefix apps/web`, then `uv run pre-commit run --all-files`.
- [ ] Run `uv run pre-commit install` at the repo root to install the git hook.
- [ ] Run `uv run pre-commit run --all-files` and fix any reported issues until the run passes cleanly.
- [ ] Commit with `chore: add pre-commit and CI gate`.

## Done When

- `uv sync` at the root installs `pre-commit` and the api workspace member.
- Backend health test passes.
- Frontend app compiles or lints.
- `uv run pre-commit run --all-files` exits 0.
- README has local development commands and a pre-commit section.
- `.github/workflows/pre-commit.yaml` exists and is valid (`check-github-workflows` passes).
