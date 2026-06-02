# Phase 1 — Pitcher Matchup + Clutch Enrichment (KBO Official) — Design

## Context

The hitter scoring model in `apps/api/app/lineup_model/` has a `matchup` component (20% weight) that consumes vs-LHP/RHP splits (`vs_lhp_ops/pa`, `vs_rhp_ops/pa`), but those fields are never ingested, so it always falls back to season OPS — the platoon dimension is dormant. Separately, the model knows nothing about the *opposing starter's quality* (only handedness) or *clutch* tendencies.

We verified (2026-06-01) that KBO official (koreabaseball.com) cleanly provides the missing data via simple **GET, server-rendered HTML** pages, and that our `Player.external_id` **is** the KBO `playerId` (no mapping needed). STATIZ is unreachable in our environment (DNS fails); Naver does not pre-aggregate splits. So KBO official is the source.

This is **Phase 1 of a 3-phase program** (user-decomposed):
- **Phase 1 (this spec): Pitcher matchup + clutch.**
- Phase 2: Park / home-away run-environment factors. *(out of scope here)*
- Phase 3: Run-expectancy lineup simulation. *(out of scope here)*

## Goals

1. Activate the dormant **matchup (20%)** component with real vs-LHP/RHP splits → changes which hitters are favored and the batting order against a given starter's hand.
2. Add **opposing-starter quality** (ERA/WHIP/K%) as context: a deterministic score-calibration multiplier (applied equally to recommended and actual scores, so it does **not** reorder/reselect) plus LLM-prompt and pregame-view context.
3. Surface **RISP (득점권타율)** as a display/LLM-context metric (not a scoring weight).
4. Build the reusable **KBO-official ingestion infrastructure** (collector + HTML parser + needs-review fallback) that Phase 2 will reuse.

## Non-Goals

- No park factors, no run-expectancy simulation (Phases 2/3).
- Pitcher quality and RISP do **not** become per-hitter scoring weights (statistically: pitcher quality is uniform across the lineup so it can't differentiate hitters; RISP is small-sample and regresses). Decision recorded via brainstorming Q1–Q3.
- No new ID-mapping layer (`Player.external_id` == KBO `playerId`).

## Design

### Roles summary

| Component | KBO source page | Role | Reorders lineup? |
|---|---|---|---|
| vs-LHP/RHP platoon | `HitterDetail/Situation.aspx?playerId=` → 투수유형별 | Fills `vs_lhp/rhp_ops·pa` → activates `matchup` (20%) | ✅ yes (per-hitter) |
| Opponent starter quality | `PitcherDetail/Basic.aspx?playerId=` → ERA/WHIP/K% | Score-calibration multiplier (equal to all) + LLM/display context | ⚪ no (uniform) |
| RISP / clutch | `HitterDetail/Basic.aspx?playerId=` → 득점권타율(RISP) | Display + LLM context | ⚪ no (not scored) |

### Shared KBO-official ingestion infrastructure

- **Collector** `apps/api/app/ingestion/collectors/kbo_official.py`: GET KBO pages by `playerId` with a browser `User-Agent` and a connect/read timeout via the existing `HttpClient`. Store the raw HTML through `save_raw_payload` with `source_name="kbo_official"` and `category=PayloadCategory.PLAYER_STATS`. Idempotency (existing `payload_hash` UNIQUE) and `needs_review_reasons` follow the existing collector/normalizer conventions. Fetch is **once per player per ingestion run** (wire into the existing roster loop in `daily_pipeline.py::_collect_roster_player_season_stats`).
- **Parser** (in a new normalizer module): parse the server-rendered HTML tables. Add `beautifulsoup4` as a dependency (`uv add beautifulsoup4`). On any parse failure (missing table/row, non-numeric cell), skip that player with a `needs_review` reason rather than fabricating values — the raw HTML is stored for replay against a fixed parser.
- **Politeness/robustness:** low request rate (≈1/player/run), browser UA, store raw for replay, needs-review fallback. KBO data is public record.

### Component 1 — vs-LHP/RHP platoon (scoring)

- Collect `Situation.aspx?playerId={external_id}`; parse the **투수유형별** table rows: 좌투수 / 우투수 / 언더투수, each with AB, H, 2B, 3B, HR, BB, HBP.
- Fold **언더투수** PAs into **우투수** (RHP) — KBO submarine/sidearm deliveries are overwhelmingly right-side and samples are tiny.
- Compute per side: `PA = AB + BB + HBP`; `OBP = (H + BB + HBP) / (AB + BB + HBP)` (the 투수유형별 table has no SF column — documented approximation); `SLG = (singles + 2·2B + 3·3B + 4·HR) / AB` with `singles = max(0, H − 2B − 3B − HR)`; `OPS = OBP + SLG`.
- Write `vs_lhp_ops`, `vs_lhp_pa`, `vs_rhp_ops`, `vs_rhp_pa` into the player's `stats_json` (the keys `lineup_evaluator.build_hitter_stats` and `pregame_views` already read). No scoring-code change needed — `player_score.matchup_score` already blends by PA confidence (≥80 full split, 40–79 blend 70/30, 20–39 blend 40/60, <20 season fallback).
- Omit a side's keys when that side has 0 PA (preserves the season-OPS fallback; no regression).

### Component 2 — opponent starter quality (context + calibration)

- **Prerequisite:** capture the opposing starter's id. Today `lineup.normalizer._apply_opponent_starter` stores only `opponent_starter_name` / `opponent_starter_throws`. Add `opponent_starter_id` (new nullable `String` column on `Game` + Alembic migration) and extract the player code from the preview `awayStarter/homeStarter.playerInfo` in the same function. Verify the code field name in `playerInfo` during planning.
- Collect `PitcherDetail/Basic.aspx?playerId={opponent_starter_id}`; parse season ERA, WHIP, and a strikeout-rate stat (K/9 or K%).
- **Calibration:** map opponent quality to a clamped multiplier on the lineup **total** score, mirroring the wRC+ multiplier pattern in `player_score.season_offense` (clamped to a small band, e.g. 0.9–1.1). Apply the **same** multiplier to both the recommended and actual lineup totals (in `lineup_evaluator`), so the comparison stays fair and the score magnitude reflects matchup difficulty, but the relative ranking / batting order is unchanged. League-average baselines for ERA/WHIP needed — resolve source in planning (compute from KBO team-pitching page vs. a documented constant).
- **Context:** include the opponent starter's quality line in the batting-order LLM prompt (`batting_order/prompt.py`) and expose it in the pregame view (`pregame_views` / `PregameResponse`) for rationale.
- Persist the parsed pitcher stats + applied multiplier in `LineupEvaluationSummary.key_insights_json` for display/audit.

### Component 3 — RISP / clutch (display + context)

- From `HitterDetail/Basic.aspx?playerId={external_id}`, parse the season **RISP (득점권타율)** value.
- Write `risp_avg` into `stats_json`; add a `risp_avg: float | None` field to `PlayerComparisonStats` (`schemas/pregame.py`) and a row to the web comparison panel (`apps/web/components/pregame/player-comparison-panel.tsx`) — a small frontend addition. Include RISP in the LLM context.
- Not used in deterministic scoring.

## Determinism & invariant

All additions are deterministic functions of ingested data; the platoon split and the pitcher-quality multiplier are pure given inputs. The recommended-lineup `output_hash` will shift on real data (expected, per the existing best-effort note in `CLAUDE.md`). The deterministic-vs-LLM boundary is preserved (platoon/calibration are deterministic; pitcher-quality context only *informs* the LLM prompt, never gates the deterministic order). Harness docs (`CLAUDE.md`, `lineup-model-reviewer`, `ingestion-helper`) to be re-checked via `/harness-audit` before the PR; update CLAUDE.md if the matchup-dormant wording no longer holds.

## Risks & mitigations

- **HTML parsing brittleness** (KBO markup changes): store raw HTML for replay; needs-review fallback on parse failure; parser unit tests against saved real-HTML fixtures.
- **New dependency** (`beautifulsoup4`): standard, well-maintained; add via `uv add`, commit `uv.lock`.
- **`opponent_starter_id` availability**: verify the preview `playerInfo` carries a player code in planning; if absent, pitcher-quality degrades gracefully (skip multiplier, leave context blank) — must not break the pipeline.
- **OBP without SF** in the 투수유형별 table: documented minor approximation.
- **Pitcher-quality baseline**: needs league averages; pick the source in planning and clamp the multiplier tightly to avoid overcorrection.
- **KBO access from CI/cron**: koreabaseball.com is reachable from this environment; confirm reachability from the actual cron runner during rollout (else degrade gracefully + needs-review).

## Testing strategy

- Parser unit tests using **saved real KBO HTML fixtures** (Situation, HitterDetail/Basic, PitcherDetail/Basic) under `tests/fixtures/sources/kbo/`: correct extraction of 좌투수/우투수(+언더 fold), RISP, ERA/WHIP/K%; malformed HTML → needs-review (no fabricated values).
- Unit tests for the split math (PA/OBP/SLG/OPS incl. the SF-less OBP) and the pitcher-quality multiplier (clamp bounds; equal application to recommended & actual; missing-pitcher → multiplier 1.0).
- Integration: matchup component activates (a hitter with a strong platoon split changes placement vs. season-only); `opponent_starter_id` captured; pregame `players/compare` returns populated `vs_*_ops` and `risp_avg`.
- Regression: with KBO data absent, behavior is byte-identical (all new fields optional, multiplier defaults to 1.0).
- Full `uv run pytest`, `pre-commit run --all-files`, `/harness-audit`.

## Open decisions for the planning step

1. Pitcher-quality league-average baseline source (KBO team-pitching page vs. documented constant) and the exact multiplier formula + clamp band.
2. Exact `playerInfo` field name for the opponent starter code (verify against a real preview payload).
3. Strikeout-rate stat to parse (K/9 vs K%) based on what `PitcherDetail/Basic.aspx` exposes.
