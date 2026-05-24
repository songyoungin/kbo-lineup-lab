# Project Scaffold Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Create the initial Python backend and formal frontend monorepo scaffold.

**Architecture:** Use a two-app repository: `apps/api` for FastAPI and `apps/web` for Next.js. Keep shared documentation at the repository root and avoid domain logic in the frontend.

**Tech Stack:** Python, FastAPI, SQLAlchemy, Alembic, pytest, Next.js, TypeScript, Tailwind, shadcn/ui.

---

## Scope

Create only the runnable skeleton. Do not implement baseball domain logic in this task.

## Files

- Create: `apps/api/pyproject.toml`
- Create: `apps/api/app/main.py`
- Create: `apps/api/app/__init__.py`
- Create: `apps/api/tests/test_health.py`
- Create: `apps/web/package.json`
- Create: `apps/web/app/page.tsx`
- Create: `apps/web/app/layout.tsx`
- Create: `apps/web/app/globals.css`
- Create: `apps/web/tsconfig.json`
- Create: `apps/web/next.config.ts`
- Create: `apps/web/tailwind.config.ts`
- Create: `apps/web/postcss.config.mjs`
- Modify: `README.md`

## Steps

- [ ] Create `apps/api` with FastAPI dependencies and pytest config.
- [ ] Add `GET /health` returning `{"status": "ok"}`.
- [ ] Add a pytest health check using FastAPI `TestClient`.
- [ ] Create `apps/web` with a minimal Next.js TypeScript app.
- [ ] Add a placeholder homepage titled `KBO Lineup Lab`.
- [ ] Update `README.md` with backend and frontend run commands.
- [ ] Run `cd apps/api && uv run pytest`.
- [ ] Run `cd apps/web && npm run lint` after installing dependencies.
- [ ] Commit with `chore(scaffold): initialize api and web apps`.

## Done When

- Backend health test passes.
- Frontend app compiles or lints.
- README has local development commands.
