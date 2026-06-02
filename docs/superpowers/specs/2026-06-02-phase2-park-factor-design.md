# Phase 2 — Park Factor (구장별 run-environment) Design

**Status:** ❌ SUPERSEDED / BLOCKED (2026-06-02). The data premise does not hold: KBO official `HitterDetail` provides **no per-hitter 구장별 (stadium) split** — verified against 3 captured fixtures and 2 live players (Situation.aspx split tabs are 투수유형별/타순별/주자상황별/이닝별/아웃카운트별/볼카운트별/경기별/일자별; no 구장별, no home/away, no per-game stadium column). Other sources are already ruled out (Statiz DNS-unreachable; Naver has no pre-aggregated splits; our DB holds only LG games so league park factors can't be computed). A park model would therefore rest on un-sourced estimated coefficients, which fails the project's accuracy bar. **Phase 2 was re-scoped to optimal defensive position assignment — see `2026-06-02-phase2-optimal-position-assignment-design.md`.** This document is retained for the data-availability finding.

---

**Status (original):** Approved design (2026-06-02).

**Goal:** Add a per-hitter **park factor** to the deterministic hitter score so the recommended lineup reflects how each hitter performs at the upcoming game's ballpark. This is the second phase of the 3-phase analysis-quality program (Phase 1 = pitcher matchup + clutch, MERGED PR #47; Phase 3 = run-expectancy simulation, later).

**Approach (decided):** A new **per-slot scoring factor**, structurally identical to the Phase-1 handedness `matchup` factor — it changes player selection, batting order, and `output_hash` (intended). It does NOT restructure the weighted-average aggregation (that is Phase 3's, deliberately deferred as the determinism-sensitive change).

**Tech stack:** Python 3.13, SQLAlchemy, BeautifulSoup, pytest, uv. Run from `apps/api`.

---

## Context: the three integration tiers

The codebase has three established ways a KBO-official signal can feed the model. Phase 2 deliberately chooses **Tier 1**.

| Tier | Mechanism | Affects `output_hash`? | Example | Seam |
|---|---|---|---|---|
| 1. Per-slot score | Weighted factor in the per-player deterministic score | **Yes** (selection/order/total) | `matchup` (vs-LHP/RHP, 20%) | `player_score.py` `matchup_score()` |
| 2. Headline multiplier | Clamped [0.9,1.1] mult on `key_insights` headline totals only | No | opponent-starter ERA/WHIP | `lineup_evaluator.py:642-651` |
| 3. Display/LLM-only | Surfaced in pregame view + LLM prompt | No | RISP (득점권타율) | `pregame_views.py`, prompt |

`output_hash` is `_lineup_output_hash(recommended)` = `{slots:[{batting_order, player_id, position}], total_score}` (`lineup_evaluator.py:289-310`). `total_score` is the **unmultiplied** deterministic lineup total, so any Tier-1 factor shifts `output_hash`. This is expected and remains deterministic (a pure function of the snapshot data). Phase 1's `matchup` already does exactly this.

---

## Decisions (locked during brainstorming)

1. **Integration tier:** Tier 1 — per-slot scoring factor (mirrors `matchup`).
2. **Park data unit:** per-stadium OPS from the KBO `Situation.aspx` **구장별** table, with PA-confidence blending toward season OPS (small away-park samples self-attenuate; LG's home park Jamsil has the most PA so it weights heavily).
3. **Weight:** `park = 0.10`, carved from the offense components — `season_offense 0.35 → 0.28`, `recent_form 0.30 → 0.27`. `matchup 0.20`, `position_fit 0.10`, `start_rhythm 0.05` unchanged. New sum = 1.00.
4. **Venue resolution:** derive the upcoming ballpark from `Game.home_team_id` via a static `TEAM_CODE → KBO park-label` map (KBO home parks are fixed). No dependency on `Game.venue` (currently `None` in the DB).

---

## Architecture & components

### A. Parser — `app/ingestion/kbo_parse.py`
Add `parse_hitter_park_splits(situation_html: str) -> dict[str, dict[str, float | int]]` returning `{stadium_label: {"ops": float, "pa": int}}` for each ballpark row in the **구장별** table. Mirrors the existing `parse_hitter_lr_splits` (투수유형별) structure: locate the 구장별 section, read the labelled rows, parse the OPS and PA cells.

**Primary risk / first planning step:** confirm the **구장별 table is server-rendered on the same `Situation.aspx` payload** Phase 1 already collects. KBO `HitterDetail/Situation.aspx` is known to carry several split tables (투수유형별, 구장별, 요일별, …). If the 구장별 table is instead loaded via a postback/AJAX tab and is NOT in the stored HTML, a new collector + URL is required and the parser plan must be revised. **Verify against a real captured payload before writing the parser.**

### B. Storage — `app/ingestion/normalizers/kbo_splits.py`
Extend the existing situation/basic enrichment so the merged `stats_json` gains `park_splits: {label: [ops, pa]}` (same JSON-merge mechanism that adds `vs_lhp_ops`/`risp_avg`; no DB migration). Absent table → key omitted (graceful).

### C. Stats type — `app/lineup_model/types.py`
Add a `park_splits` field to `HitterStats` (e.g. `Mapping[str, tuple[float, int]]`, default empty) so the scorer can read per-stadium OPS/PA. Populate it where `HitterStats` is built from `stats_json` (alongside `vs_lhp_ops` etc.).

### D. Venue resolution — new static map + evaluator seam
- New constant (e.g. in a small `app/lineup_model/parks.py` or alongside team codes) `TEAM_CODE_TO_PARK_LABEL: dict[str, str]` covering all 10 KBO teams, where the value matches the KBO 구장별 row label exactly.
- In `lineup_evaluator.py`, add `_resolve_venue_label(session, run) -> str | None` next to `_resolve_opp_handedness` (`:320`): `game.home_team_id` → team code → park label. Returns `None` when unmappable (→ scorer falls back to season).

### E. Scoring — `app/lineup_model/player_score.py`
- Add `park_score(stats: HitterStats, venue_label: str | None) -> tuple[float, ScoringReason]` using the **same PA-threshold confidence blend** as `matchup_score` (`:134-184`): when the hitter's PA at `venue_label` is ≥ threshold, weight the park OPS more; below it, blend toward / fall back to season OPS; `venue_label is None` or label absent → pure season fallback (neutral).
- Constants: `_W_PARK = 0.10`; change `_W_SEASON = 0.28`, `_W_RECENT = 0.27`. Update the module docstring weight table.
- Wire `park_score` into the per-player composite wherever the five factors are currently summed, and thread `venue_label` from the evaluator to the scorer (parallel to how `opp_handedness` is threaded to `matchup_score`).

### F. Display / LLM (small, optional-but-included)
Surface venue + the hitter's park OPS in `pregame_views.py` and the batting-order LLM prompt, in the same shape as the existing matchup/RISP context. Display-only; no scoring effect beyond E.

---

## Data flow

```
daily pipeline (existing) → collect Situation.aspx HTML (already happens)
  → normalize: parse_hitter_park_splits → stats_json.park_splits   [B]
pregame eval:
  HitterStats.park_splits ← stats_json                              [C]
  venue_label ← _resolve_venue_label(game.home_team_id)             [D]
  per-player score += _W_PARK * park_score(stats, venue_label)      [E]
  → recommended lineup (selection/order)/total_score/output_hash shift (deterministic)
  → pregame view + LLM prompt show venue/park OPS                   [F]
```

## Determinism & safety
- **Deterministic:** `park_score` is a pure function of `stats_json` + the static venue map. Same snapshot → same lineup → same `output_hash`. The *value* of the recommended-lineup `output_hash` changes from the pre-Phase-2 baseline (expected, as with `matchup`).
- **Graceful degradation (never breaks the pipeline):** missing 구장별 table, an unmapped venue, or `PA == 0` all resolve to the season-OPS fallback inside `park_score`. KBO-official fetch/parse failures remain best-effort (the daily pipeline already swallows them).
- **LLM layer unaffected:** the LLM batting-order layer stays additive and OFF by default; the canary's `output_hash` is already best-effort/non-deterministic when LLM is ON.

## Testing
- `parse_hitter_park_splits` against a real captured 구장별 fixture (and a no-table fixture → `{}`).
- `park_score`: above-threshold (park-weighted), below-threshold (blended), `venue_label=None`/absent label/`PA=0` (season fallback). A reason/explanation assertion.
- Weight regression: the six factor weights sum to exactly 1.0.
- `_resolve_venue_label`: home-team → label for a couple of teams; unmapped → None.
- DB-path integration: a hitter with a strong park OPS at the game venue ranks higher than with the table absent (proves park actually moves the recommendation).
- Graceful degradation: pipeline/eval completes with no 구장별 data (falls back, no error).

## Out of scope (explicit)
- Replacing the weighted-average aggregation with run-expectancy simulation → **Phase 3**.
- League-computed park-factor coefficients (runs-at-park vs league) → not needed; per-hitter park OPS is the chosen signal.
- Backfilling/repairing `Game.venue` from Naver → venue is derived from `home_team_id` instead.
- Greedy→optimal position assignment; postgame `performance_score` contextualization → separate follow-ups.

## Key files
- Modify: `app/ingestion/kbo_parse.py`, `app/ingestion/normalizers/kbo_splits.py`, `app/lineup_model/types.py`, `app/lineup_model/player_score.py`, `app/services/lineup_evaluator.py`, `app/services/pregame_views.py`, the batting-order prompt builder.
- Add: `TEAM_CODE_TO_PARK_LABEL` constant (e.g. `app/lineup_model/parks.py`).
- Tests: `tests/` for parser, scorer, weights, venue map, integration, degradation.
