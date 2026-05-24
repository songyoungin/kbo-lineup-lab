# KBO Data Source Research Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Identify and document usable KBO data sources for LG Twins schedules, rosters, player stats, lineups, and box scores.

**Architecture:** Treat KBO data as a first-class requirement. Source research must produce a source matrix and fixture samples before any scraper-specific implementation.

**Tech Stack:** Python, Markdown, HTTP inspection tools, pytest fixtures.

---

## Scope

Research real KBO data sources. Do not implement collectors in this task.

## Files

- Create: `docs/data-sources/kbo-source-matrix.md`
- Create: `docs/data-sources/sample-payloads.md`
- Create: `apps/api/fixtures/source_samples/.gitkeep`
- Modify: `README.md`

## Steps

- [ ] List candidate sources for KBO schedule, roster, player stats, lineup, and box score data.
- [ ] For each source, document data coverage, update timing, URL shape, response format, usage risk, and parser complexity.
- [ ] Mark whether the source contains LG Twins data specifically.
- [ ] Capture at least one sample payload reference for each required data type.
- [ ] Document legal/operational risk as `low`, `medium`, or `high`.
- [ ] Update `README.md` to state that production data must be KBO/LG data, not generic baseball data.
- [ ] Commit with `docs(data): document KBO source candidates`.

## Done When

- The project has a written KBO source matrix.
- Each required data type has at least one candidate source.
- The next collector tasks can choose a source without guessing.
