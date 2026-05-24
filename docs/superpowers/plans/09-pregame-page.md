# Pregame Page Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the pregame evaluation and lineup comparison UI.

**Architecture:** Server-render or fetch page data through the frontend API client. Keep comparison table and explanation cards as reusable components.

**Tech Stack:** Next.js, TypeScript, Tailwind, shadcn/ui.

---

## Scope

Implement pregame screens only. Postgame review is a separate task.

## Files

- Create: `apps/web/app/games/[gameId]/pregame/page.tsx`
- Create: `apps/web/components/pregame/lineup-score-summary.tsx`
- Create: `apps/web/components/pregame/lineup-comparison-table.tsx`
- Create: `apps/web/components/pregame/difference-card.tsx`
- Create: `apps/web/components/pregame/player-comparison-panel.tsx`
- Modify: `apps/web/lib/api.ts`
- Modify: `apps/web/lib/types.ts`

## Steps

- [ ] Add API client functions for pregame, lineup comparison, and player comparison.
- [ ] Build score summary cards for actual score, recommended score, and gap.
- [ ] Build lineup comparison table with difference badges.
- [ ] Build explanation cards for major differences.
- [ ] Build player comparison panel showing recent, season, split, position, and rhythm values.
- [ ] Add loading and error states.
- [ ] Verify mobile and desktop layouts do not overlap.
- [ ] Run `cd apps/web && npm run lint`.
- [ ] Commit with `feat(web): add pregame evaluation page`.

## Done When

- A user can inspect why the actual LG lineup scored above or below the recommendation.
- The page clearly exposes model limitations.
