# LG Twins Lineup Lab MVP Design

## 1. Product Summary

LG Twins Lineup Lab is a KBO analysis dashboard for deep baseball fans.

The MVP evaluates the LG Twins' actual pregame lineup against a data-recommended lineup, then generates a postgame review that explains whether the actual choices worked in the result.

This is not a real-time gamecast product. It does not need pitch-by-pitch, baserunner, or ball-count data. The product value comes from repeatable lineup analysis, historical simulation, and explainable postgame review.

## 2. Target User

The first target user is a deep LG Twins fan who already cares about lineup choices, platoon matchups, recent form, batting order, and postgame lineup debates.

The product should help this user answer:

- Was today's actual lineup close to the data-recommended lineup?
- Which choices lowered or improved the lineup efficiency score?
- Did a questionable choice succeed anyway?
- Who exceeded expectations?
- Who underperformed relative to the pregame expectation?
- If I replay a past game, what would the model have predicted using only data available before that game?

## 3. MVP Scope

The MVP supports one team: LG Twins.

Included:

- LG game schedule ingestion.
- Opponent and starting pitcher context.
- LG player roster and hitter stat ingestion.
- Actual LG lineup ingestion.
- Pregame lineup evaluation.
- Data-recommended LG lineup generation.
- Actual lineup vs recommended lineup comparison.
- Postgame box score ingestion.
- Postgame review generation.
- Ingestion status dashboard.
- Historical replay using cutoff-based snapshots.
- Deterministic re-runs for the same data and model version.

Excluded from MVP:

- All-team lineup analysis.
- Opponent lineup recommendation.
- Real-time pitch-by-pitch updates.
- Live baserunner and ball-count state.
- Detailed defensive metrics.
- Detailed baserunning metrics.
- Injury and rest-plan inference beyond visible roster/status data.
- Paid sports-data API integration.

## 4. Core User Flow

1. The scheduled pipeline collects today's LG game information.
2. Before the game, the pipeline collects or updates LG player stat snapshots.
3. When the actual LG lineup becomes available, the pipeline stores a lineup snapshot.
4. The lineup model runs with an `evaluation_cutoff_at` timestamp.
5. The system compares the actual lineup with the recommended lineup.
6. The user opens the pregame evaluation page.
7. After the game, the pipeline collects the LG hitter box score.
8. The postgame grader compares actual results with pregame expectations.
9. The user opens the postgame review page.
10. A past game can be replayed later with the same cutoff, data snapshot, and model version.

## 5. Screens

### 5.1 Team Home

Purpose: show the current LG game and recent lineup analysis history.

Content:

- Today's game card.
- Opponent, stadium, game time, and opponent starter.
- Pipeline status.
- Pregame evaluation status.
- Postgame review status.
- Recent 5 games with recommendation gap and result verdict.

Example statuses:

- Schedule collected.
- Starter collected.
- Lineup waiting.
- Evaluation complete.
- Box score waiting.
- Postgame review complete.
- Needs review.

### 5.2 Pregame Evaluation

Purpose: show whether the actual LG lineup is efficient according to the model.

Content:

- Actual lineup efficiency score.
- Recommended lineup efficiency score.
- Recommendation gap.
- Verdict.
- Actual lineup table.
- Recommended lineup table.
- Difference highlights by batting order and position.
- Explanation cards for major differences.
- Model limitations.

Example verdicts:

- Nearly optimal.
- Acceptable.
- Questionable.
- Low offensive efficiency.

### 5.3 Lineup Comparison

Purpose: let users inspect actual vs recommended choices.

Columns:

- Batting order.
- Actual player and position.
- Recommended player and position.
- Difference type.
- Main reason.

Difference types:

- Same.
- Player changed.
- Position changed.
- Batting order changed.
- Player and order changed.

### 5.4 Player Comparison

Purpose: explain why the model preferred one player over another.

Content:

- Recent 14-day OPS.
- Recent 30-day OPS.
- Season OPS.
- Season OBP.
- Season SLG.
- OPS by pitcher handedness.
- Plate appearance sample size.
- Position fit.
- Recent start rhythm.
- Model judgment.
- Unmodeled factors.

Unmodeled factors shown in MVP:

- Detailed defense.
- Baserunning value.
- Injury detail.
- Manager rest plan.
- Clubhouse or condition information.

### 5.5 Postgame Review

Purpose: grade whether the actual choices worked after the result is known.

Content:

- Final score.
- Pregame actual lineup score.
- Pregame recommendation gap.
- Result verdict.
- Overperformers.
- Underperformers.
- Review of choices that differed from the recommendation.
- Natural-language summary.

Example summaries:

- The actual lineup was weaker than the recommendation, but the selected players exceeded expectations.
- The model disliked the choice before the game, and the result also underperformed.
- The actual choice differed from the model and succeeded.
- The actual lineup was close to optimal and performed within expectation.

### 5.6 Ingestion Status

Purpose: make the data pipeline observable.

Content:

- Schedule ingestion status.
- Starter ingestion status.
- Player stat ingestion status.
- Actual lineup ingestion status.
- Stat snapshot id.
- Lineup snapshot id.
- Evaluation run id.
- Box score ingestion status.
- Postgame review status.
- Errors and warnings.
- Manual correction status.

The admin screen is for exception handling, not daily manual data entry.

## 6. Data Pipeline

The MVP uses batch ingestion, not real-time ingestion.

Recommended schedule:

- Morning: collect today's LG game, opponent, stadium, game time, and expected starter context.
- Before game: update player stat snapshots.
- After lineup announcement: collect actual LG lineup and run pregame evaluation.
- After final result: collect box score and run postgame review.
- Overnight: recalculate rolling 7/14/30-day stat snapshots and recent dashboard aggregates.

Pipeline stages:

```text
Scheduler
-> Collectors
-> Raw Data Store
-> Parser / Normalizer
-> Validated Domain Tables
-> Snapshot Builder
-> Lineup Model
-> Pregame Evaluation
-> Postgame Grader
-> Dashboard API
```

Collector examples:

- `ScheduleCollector`
- `PlayerStatsCollector`
- `LineupCollector`
- `BoxScoreCollector`

Source-specific implementations should be replaceable:

- `KboScheduleCollector`
- `NaverLineupCollector`
- `KboOfficialStatsCollector`
- `StatizPlayerStatsCollector`

The exact source choice is intentionally left replaceable because public KBO data pages can change.

## 7. Snapshot And Idempotency Requirements

Historical replay is a core requirement.

The system must never evaluate a past game using current player stats unless the user explicitly requests a current-data experiment.

Default rule:

```text
Pregame evaluations must use only data available at or before evaluation_cutoff_at.
```

For a past game, the model must use:

- The game id.
- The LG team id.
- The evaluation cutoff timestamp.
- A stat snapshot with `snapshot_at <= evaluation_cutoff_at`.
- An actual lineup snapshot with `announced_at <= evaluation_cutoff_at`.
- A model version.
- A stored input manifest.

Idempotency key:

```text
game_id + team_id + evaluation_cutoff_at + stat_snapshot_id + lineup_snapshot_id + model_version_id
```

If the same key is run again, the system should return the existing run or produce the same output hash.

Store:

- `input_manifest_json`
- `input_hash`
- `output_hash`
- `model_version_id`
- `model_config_json`
- `code_hash` when available

Replay checks:

- Same input hash and same output hash means the run is reproducible.
- Same input hash and different output hash means model code or nondeterminism changed.
- Different input hash means the source data snapshot changed.

## 8. Lineup Model V1

The MVP uses an explainable weighted scoring model.

Player score:

```text
Player Score =
  season offense 35%
+ recent form 30%
+ opponent starter handedness matchup 20%
+ position fit 10%
+ start rhythm 5%
```

Season offense:

```text
season offense =
  OPS 60%
+ OBP 25%
+ SLG 15%
```

Recent form:

```text
recent form =
  recent 14-day OPS 70%
+ recent 30-day OPS 30%
```

Matchup:

- Use vs RHP OPS if the opponent starter is right-handed.
- Use vs LHP OPS if the opponent starter is left-handed.
- Regress small samples toward season OPS.

Initial sample confidence:

- 80+ PA: use split fully.
- 40-79 PA: 70% split, 30% season OPS.
- 20-39 PA: 40% split, 60% season OPS.
- Under 20 PA: use season OPS.

Position fit:

- Primary position: 100.
- Secondary position: 80.
- Recent played position: 65.
- Impossible position: not eligible.

Start rhythm:

- 3-5 starts in the last 5 games: 100.
- 1-2 starts in the last 5 games: 80.
- 0 starts in the last 5 games: 60.

Batting order adjustments:

- 1st: OBP emphasis.
- 2nd: OBP and contact emphasis.
- 3rd: balanced OPS.
- 4th: SLG emphasis.
- 5th: OPS and SLG emphasis.
- 6th-7th: overall offense.
- 8th: position realism.
- 9th: secondary OBP value.

Batting order weights:

- 1st: 1.10
- 2nd: 1.05
- 3rd: 1.15
- 4th: 1.20
- 5th: 1.10
- 6th: 0.95
- 7th: 0.90
- 8th: 0.80
- 9th: 0.75

Lineup score:

```text
Lineup Score =
  weighted average of batting-order scores
+ position completeness adjustment
+ light handedness-balance adjustment
```

Handedness-balance adjustment should remain weak in V1:

- Five same-side hitters in a row: -1.
- Six or more same-side hitters in a row: -2.
- Otherwise: 0.

## 9. Recommendation Generation

The recommendation generator should:

1. Load eligible LG hitters from the cutoff snapshot.
2. Remove unavailable players when roster/status data is available.
3. Compute player scores.
4. Generate valid defensive configurations.
5. Assign batting order candidates.
6. Score candidate lineups.
7. Save the highest scoring lineup.
8. Save reasons for major differences against the actual lineup.

MVP constraints:

- Catcher must be catcher-eligible.
- Defensive positions must be valid.
- DH is allowed.
- The model must not silently place players at impossible positions.
- The model should expose limitations instead of pretending to know unavailable data.

## 10. Postgame Grader V1

Postgame review compares pregame expectation with actual box score results.

Simple game performance score:

```text
single: +1
double: +2
triple: +3
home run: +4
walk/HBP: +1
run: +1
RBI: +1
strikeout: -0.5
grounded into double play: -1.5
```

Postgame dimensions:

- Selection quality: how close the actual lineup was to the recommendation before the game.
- Result quality: how actual selected players performed after the game.
- Difference review: whether players chosen over model recommendations succeeded or failed.

Recommendation gap labels:

- 0 to -2: nearly optimal.
- -2 to -5: acceptable.
- -5 to -10: questionable.
- below -10: low offensive efficiency.

## 11. Storage Model

Core tables:

- `teams`
- `players`
- `games`
- `raw_ingestion_payloads`
- `ingestion_runs`
- `stat_snapshots`
- `player_stat_snapshot_rows`
- `actual_lineup_snapshots`
- `actual_lineup_snapshot_rows`
- `model_versions`
- `lineup_evaluation_runs`
- `recommended_lineup_rows`
- `lineup_evaluation_summaries`
- `box_score_snapshots`
- `box_score_rows`
- `postgame_review_runs`
- `postgame_review_summaries`

Important relationships:

- Recommended lineup rows belong to a lineup evaluation run.
- Pregame summaries belong to a lineup evaluation run.
- Postgame reviews reference the pregame evaluation run.
- Box score rows belong to a box score snapshot.
- Every model-generated output stores a model version and input manifest.

## 12. API Shape

Initial read endpoints:

- `GET /api/team/lg/home`
- `GET /api/games/{game_id}/pregame`
- `GET /api/games/{game_id}/lineup-comparison`
- `GET /api/games/{game_id}/players/compare`
- `GET /api/games/{game_id}/postgame`
- `GET /api/admin/ingestion-runs`

Initial job endpoints:

- `POST /api/jobs/ingest/schedule`
- `POST /api/jobs/ingest/player-stats`
- `POST /api/jobs/ingest/lineup`
- `POST /api/jobs/evaluate-lineup`
- `POST /api/jobs/ingest/box-score`
- `POST /api/jobs/generate-postgame-review`
- `POST /api/jobs/replay-evaluation`

Job endpoints should be protected before any public deployment.

## 13. Success Criteria

Technical success:

- The system can ingest or load LG data for at least 3-5 games.
- The system can produce pregame evaluations.
- The system can produce postgame reviews.
- Historical replay uses only cutoff-safe data.
- Re-running the same evaluation key returns the same result.
- Ingestion and evaluation runs are inspectable.

User success:

- A deep LG fan can understand why a lineup scored well or poorly.
- The postgame review feels worth discussing or sharing.
- The product gives enough evidence to support disagreement rather than hiding behind an opaque score.

## 14. Open Decisions

These are intentionally left for implementation planning:

- Exact first data sources.
- Web framework and backend stack.
- Database choice.
- Whether the first prototype uses seeded CSV fixtures before live ingestion.
- Whether model V1 should be implemented in SQL, application code, or a separate job module.
- Whether natural-language summaries are rule-based or LLM-assisted.
