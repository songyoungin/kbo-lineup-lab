# Real Data Ingestion — Design (2026-05-29)

## Goal

Replace the placeholder, unverified-URL collectors with verified, currently-working endpoints so the
pipeline ingests **real LG Twins data** instead of only the `lg_2026_sample.json` fixture. Scope is the
single-team, daily-batch MVP.

## Decisions (confirmed)

1. **Primary source = Naver `api-gw.sports.naver.com`; backup = KBO Official `/ws/*.asmx`.** Naver provides
   schedule, lineup, handedness, starters, and box score in one no-auth JSON API family (verified
   2026-05-29 — see [`docs/data-sources/endpoint-verification-2026-05-29.md`](../../data-sources/endpoint-verification-2026-05-29.md)).
2. **Fan-scale, polite collection.** Low frequency (≥5 s between requests), daily batch, caching, honest
   `User-Agent`, correct `Referer`. We accept that KBO `/ws/` is `robots.txt`-disallowed and treat KBO as a
   *backup* used sparingly; Naver (primary) is fan-scale tolerated. Politeness is enforced in `HttpClient`.
3. **Incremental delivery:** schedule → roster → lineup → player stats → box score, one collector at a time,
   each with tests, before wiring the daily pipeline end-to-end.
4. **wRC+/LHP-RHP splits are out of scope for V1.** Naver exposes OBP/AVG/SLG/OPS/ERA/WHIP-level metrics;
   sabermetrics live behind STATIZ's login wall. The scoring model must not require wRC+.

## Current state

- `apps/api/app/ingestion/`: `collectors/` (schedule, roster, lineup, player_stats, box_score),
  `normalizers/` (same five), `raw_store.save_raw_payload` (idempotent), `http_client.HttpClient`,
  `types.PayloadCategory`. Pipeline: **collector fetches raw → `raw_store` persists → normalizer →
  domain snapshots**. This separation is good and is preserved.
- Every collector's URL template is marked `VERIFY before live use` and is wrong/stale (e.g. STATIZ's
  `statiz.sporki.com` is dead). `HttpClient.fetch(url)` is **GET-only** — no POST, no `Referer`, no
  rate-limiting.

## Architecture

Keep the existing **collector → raw_store → normalizer → snapshot** flow. Changes are localized:

### 1. `HttpClient` enhancements (shared, do first)

- Add `post(url, *, data, headers)` for KBO `/ws/*.asmx` (form-encoded, returns JSON body) alongside `fetch`.
- Add per-host **rate limiting** (min interval, default 5 s) and a **default `Referer`/`User-Agent`** policy
  injectable per call. Keep existing retry/backoff/size-cap.
- Existing `fetch` signature stays; new behavior is additive.

### 2. `game_id` utilities + team codes (shared)

- `app/ingestion/game_id.py`: parse/format `YYYYMMDD{away}{home}{seq}`; convert KBO `G_ID` ↔ Naver
  `gameId` (Naver = KBO id + season year, e.g. `20250514WOLG0` ↔ `20250514WOLG02025`).
- Team-code constants (LG, WO, HT, SS, KT, LT, NC, OB, HH, SK/SSG, KIA…) in one module; LG already in
  `collectors/_constants.py`.

### 3. Collectors — swap to verified endpoints (incremental)

Each collector still returns raw payloads to `raw_store`; only the URL/method/params change.

| # | Collector | Primary (Naver) | Backup (KBO) |
|---|-----------|-----------------|--------------|
| 1 | schedule | `GET /schedule/games?fields=basic&upperCategoryId=kbaseball&categoryId=kbo&fromDate=&toDate=` | `POST /ws/Main.asmx/GetKboGameList` |
| 2 | roster + handedness | from `preview.playerInfo.hitType` / `fullLineUp.batsThrows` (per game, accumulated) | — |
| 3 | lineup | `GET /schedule/games/{gameId}/preview` → `previewData.{home,away}TeamLineUp.fullLineUp` | KBO GameCenter (dynamic; deferred) |
| 4 | player stats (basic) | `preview.currentSeasonStats*` / `recentFiveGamesStats` | KBO stats `.aspx` (VIEWSTATE) |
| 5 | box score | `GET /schedule/games/{gameId}/record` → `recordData.{batters,pitchers}Boxscore` | `POST /ws/Schedule.asmx/GetScoreBoardScroll` |

Roster handedness is sourced opportunistically from `preview` (KBO Official has no handedness). A dedicated
roster endpoint is not required for V1 — players are discovered via lineups/box scores.

### 4. Normalizers — map real response shapes

Rewrite each normalizer's parser against the **captured real responses** (saved as test fixtures), keeping
the existing `normalize_*` signatures and output domain rows. Existing snapshot uniqueness/idempotency
(content-hash) is unchanged.

### 5. Daily pipeline wiring

After collectors 1–5 are individually verified, connect them in `app/jobs/daily_pipeline.py` for a given
date: schedule → (per LG game) lineup + box score + stats, persisting snapshots. The existing
`ingest-daily` CLI command drives it.

## Politeness / compliance policy

- `HttpClient`: ≥5 s/host min interval, honest UA (`kbo-lineup-lab/0.1 +github…`), correct `Referer`
  (`m.sports.naver.com` for Naver; `koreabaseball.com` for KBO).
- Cache: schedule 24 h; finished-game lineup/box score cached permanently (immutable once `RESULT`).
- KBO `/ws/` (robots-disallowed) used only as backup, low volume. Naver is primary.
- A short `docs/data-sources/collection-policy.md` records these rules.

## Testing

- **Normalizers:** unit tests against captured real JSON fixtures (one per source/category), asserting
  domain rows. This is the bulk of the value and is deterministic.
- **Collectors:** test URL/param/method construction and raw-store persistence with a mocked `HttpClient`
  (no live calls in tests).
- **`HttpClient`:** unit tests for POST, rate-limiter interval, header policy.
- **game_id utils:** round-trip parse/format/convert tests.
- No live network in CI.

## Out of scope (V1)

wRC+/wOBA/WAR; LHP-RHP splits; STATIZ (login wall); multi-team; real-time/live polling; KBO GameCenter
dynamic-lineup capture (Naver `preview` covers lineup).

## Incremental delivery

Each step is its own plan task with tests, merged before the next:

1. `HttpClient` POST + rate-limit + header policy
2. `game_id` utils + team codes
3. schedule collector + normalizer (Naver primary, KBO backup)
4. lineup collector + normalizer (+ handedness into roster)
5. player-stats collector + normalizer (basic metrics)
6. box-score collector + normalizer
7. daily pipeline wiring + `ingest-daily` end-to-end against a real date

## Open items (non-blocking)

- Naver `robots.txt`/ToS posture (confirm; policy already conservative).
- Naver pre-game `preview.lineUpData` shape (only finished-game `null` observed) — confirm during step 4.
- wRC+ source if the model later needs it (STATIZ login or paid).
