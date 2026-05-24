# Postgame Page Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the postgame review UI for result grading and player outcome analysis.

**Architecture:** Display postgame review as a compact dashboard: result summary first, then player outcome lists and recommendation-difference reviews.

**Tech Stack:** Next.js, TypeScript, Tailwind, shadcn/ui.

---

## Scope

Implement the postgame page and link it from the team home or pregame page.

## Files

- Create: `apps/web/app/games/[gameId]/postgame/page.tsx`
- Create: `apps/web/components/postgame/result-summary.tsx`
- Create: `apps/web/components/postgame/player-outcome-list.tsx`
- Create: `apps/web/components/postgame/choice-review-card.tsx`
- Modify: `apps/web/lib/api.ts`
- Modify: `apps/web/lib/types.ts`
- Modify: `apps/web/app/page.tsx`

## Steps

- [ ] Add API client function for postgame review.
- [ ] Build result summary with final score, pregame score, recommendation gap, and verdict.
- [ ] Build overperformer and underperformer lists.
- [ ] Build choice review cards for actual-vs-recommended differences.
- [ ] Add navigation links from team home and pregame page.
- [ ] Add loading and error states.
- [ ] Verify mobile and desktop layouts do not overlap.
- [ ] Run `cd apps/web && npm run lint`.
- [ ] Commit with `feat(web): add postgame review page`.

## Done When

- A user can review whether the actual lineup choice worked after the game.
- The page is connected from the main product flow.
