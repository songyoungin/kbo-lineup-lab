# Frontend Shell Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Create the formal Next.js frontend shell for the LG lineup dashboard.

**Architecture:** Keep API access isolated in `lib/api`. Build reusable dashboard components before page-specific screens.

**Tech Stack:** Next.js, TypeScript, Tailwind, shadcn/ui, lucide-react.

---

## Scope

Build layout, navigation, API client, and shared visual components. Do not implement full pregame/postgame pages yet.

## Files

- Create: `apps/web/lib/api.ts`
- Create: `apps/web/lib/types.ts`
- Create: `apps/web/components/app-shell.tsx`
- Create: `apps/web/components/status-pill.tsx`
- Create: `apps/web/components/score-card.tsx`
- Create: `apps/web/components/data-table.tsx`
- Modify: `apps/web/app/layout.tsx`
- Modify: `apps/web/app/page.tsx`
- Modify: `apps/web/app/globals.css`

## Steps

- [ ] Set `<html lang="ko">` in `apps/web/app/layout.tsx`. The scaffold (Plan 01) shipped `lang="en"` as a placeholder; the product UI is Korean-first.
- [ ] Add `NEXT_PUBLIC_API_BASE_URL` support with default `http://localhost:8000`.
- [ ] Define TypeScript types matching pregame and postgame API responses.
- [ ] Build an app shell with compact navigation.
- [ ] Build reusable status pill, score card, and table components.
- [ ] Implement the team home placeholder using fixture-shaped mock data if the API is unavailable.
- [ ] Keep the design dense and dashboard-like, not marketing-like.
- [ ] Run `cd apps/web && npm run lint`.
- [ ] Commit with `feat(web): add dashboard shell`.

## Done When

- Frontend has a stable app layout and shared components.
- The home page is useful even before page-specific data is wired.
