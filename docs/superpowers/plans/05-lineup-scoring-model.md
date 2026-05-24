# Lineup Scoring Model Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement explainable player scoring, lineup scoring, and recommended lineup generation.

**Architecture:** Keep scoring pure and deterministic. Database services should load inputs, then pass plain Python objects into the scoring module.

**Tech Stack:** Python, Pydantic, pytest.

---

## Scope

Implement model V1 from the design document. Use fixture data only. Do not expose API endpoints yet.

## Files

- Create: `apps/api/app/lineup_model/types.py`
- Create: `apps/api/app/lineup_model/player_score.py`
- Create: `apps/api/app/lineup_model/lineup_score.py`
- Create: `apps/api/app/lineup_model/recommendation.py`
- Create: `apps/api/app/services/lineup_evaluator.py`
- Create: `apps/api/tests/test_player_score.py`
- Create: `apps/api/tests/test_lineup_score.py`
- Create: `apps/api/tests/test_recommendation.py`

## Steps

- [ ] Define typed inputs for hitter stats, positions, handedness, lineup slots, and scoring reasons.
- [ ] Implement season offense score: OPS 60%, OBP 25%, SLG 15%.
- [ ] Implement recent form score: recent 14-day OPS 70%, recent 30-day OPS 30%.
- [ ] Implement handedness split regression by plate appearance thresholds.
- [ ] Implement position eligibility scoring and block impossible positions.
- [ ] Implement batting-order weights and slot-specific OBP/SLG emphasis.
- [ ] Implement weak handedness-balance penalty.
- [ ] Implement a deterministic recommendation generator for valid lineups.
- [ ] Persist recommended lineup rows and summary scores for an evaluation run.
- [ ] Add tests for every scoring rule and at least one full recommendation.
- [ ] Run `cd apps/api && uv run pytest`.
- [ ] Commit with `feat(model): add lineup scoring and recommendation`.

## Done When

- The fixture game can produce an actual score, recommended score, score gap, and recommended lineup.
- Tests verify the scoring formula and deterministic output.
