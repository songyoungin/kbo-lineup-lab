# Admin Ingestion Status Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build backend and frontend views for monitoring KBO ingestion pipeline status.

**Architecture:** Admin status should show what data exists, what is waiting, what failed, and what needs manual review.

**Tech Stack:** FastAPI, SQLAlchemy, Next.js, TypeScript, Tailwind, shadcn/ui.

---

## Scope

Expose ingestion status APIs and a simple admin status page. Do not implement manual editing forms yet.

## Files

- Create: `apps/api/app/api/routes/admin.py`
- Create: `apps/api/app/schemas/admin.py`
- Create: `apps/api/app/services/ingestion_status.py`
- Create: `apps/api/tests/test_ingestion_status_api.py`
- Create: `apps/web/app/admin/ingestion/page.tsx`
- Create: `apps/web/components/admin/ingestion-status-table.tsx`
- Modify: `apps/api/app/main.py`
- Modify: `apps/web/lib/api.ts`
- Modify: `apps/web/lib/types.ts`

## Steps

- [ ] Add `GET /api/admin/ingestion-runs`.
- [ ] Add `GET /api/admin/games/{game_id}/ingestion-status`.
- [ ] Return schedule, roster, stats, lineup, evaluation, box score, and postgame statuses.
- [ ] Include raw payload ids, snapshot ids, run ids, errors, and `needs_review` flags.
- [ ] Build an admin ingestion status page.
- [ ] Use clear statuses: `waiting`, `collected`, `normalized`, `complete`, `failed`, `needs_review`.
- [ ] Add API tests for complete and failed pipelines.
- [ ] Run `cd apps/api && uv run pytest`.
- [ ] Run `cd apps/web && npm run lint`.
- [ ] Commit with `feat(admin): add ingestion status dashboard`.

## Done When

- A developer can see whether KBO data is missing, collected, normalized, or failed.
- The admin page supports operating the automatic pipeline without daily manual entry.
