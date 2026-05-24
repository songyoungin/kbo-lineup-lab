# Project Scaffold Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Create the initial Python backend and formal frontend monorepo scaffold, plus a unified pre-commit and CI gate.

**Architecture:** Use a two-app repository: `apps/api` for FastAPI and `apps/web` for Next.js. The repository root is a `uv` workspace that owns shared developer tooling (pre-commit). Keep shared documentation at the repository root and avoid domain logic in the frontend.

**Tech Stack:** Python 3.13, FastAPI, SQLAlchemy, Alembic, pytest, Next.js, TypeScript, Tailwind, shadcn/ui, uv (workspace), pre-commit, ruff, mypy, bandit, detect-secrets, vulture, GitHub Actions.

---

## Scope

Create only the runnable skeleton and the pre-commit / CI gate. Do not implement baseball domain logic in this task.

## Files

- Create: `pyproject.toml` (root, uv workspace + `pre-commit` and `detect-secrets` dev deps)
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
- Create: `apps/web/postcss.config.mjs`
- Create: `apps/web/eslint.config.mjs` (flat config)
- Modify: `README.md`

Tailwind v4 (the version `create-next-app@latest` ships) is configured via `@import "tailwindcss"` in `apps/web/app/globals.css` and the `@tailwindcss/postcss` plugin in `apps/web/postcss.config.mjs`. No `tailwind.config.ts` file is created.

## Steps

### Backend and frontend scaffold

- [ ] Create root `pyproject.toml` declaring `requires-python = ">=3.13"`, a `[tool.uv.workspace]` table listing `apps/api`, and `pre-commit` plus `detect-secrets` as dev dependencies (both are needed for the gate work in the next section). Mark the root package as non-publishable.
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
  - `PyCQA/bandit`: `args: ["-c", "apps/api/pyproject.toml", "-r", "apps/api/app", "--severity-level", "medium"]`, scoped to `files: ^apps/api/app/.*\.py$`. Set `pass_filenames: false` because the `-r` directory scan is incompatible with per-file invocation.
  - `Yelp/detect-secrets`: `detect-secrets` with `--baseline .secrets.baseline`.
  - `pre-commit/mirrors-mypy`: scoped to `files: ^apps/api/.*\.py$`, `args: [--config-file=apps/api/pyproject.toml, --strict, --ignore-missing-imports]`, `additional_dependencies: ["fastapi>=0.115", "httpx>=0.27"]` so strict mypy can resolve FastAPI decorator types in mypy's isolated venv. Extend this list whenever a new runtime dependency leaks types into `apps/api/app/`.
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

## Implementation Notes

Landed on `feat/project-scaffold` as two commits and merged on 2026-05-25:

- `chore(scaffold): initialize api and web apps` — Task 1
- `chore: add pre-commit and CI gate` — Task 2

Deviations from the original spec, all documented and accepted in review:

- **Tailwind v4 instead of v3.** Captured in the Files list above. The product UI work in Plan 08 will follow the Tailwind v4 CSS-first config style.
- **mypy `additional_dependencies` populated** with `fastapi>=0.115` and `httpx>=0.27` so strict mypy resolves FastAPI decorator types in mypy's isolated venv. Empty list was not viable for the scaffold.
- **bandit `pass_filenames: false`.** Required because the `-r apps/api/app` directory scan is incompatible with pre-commit passing individual files.
- **CI uses `cd apps/web && npm ci`** instead of `npm install --prefix apps/web` for reproducibility against the committed lockfile.
- **`detect-secrets>=1.5.0` in root dev deps**, required by the `uv run detect-secrets scan` baseline generation step.
