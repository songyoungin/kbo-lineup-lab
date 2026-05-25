# KBO Data Source Matrix (LG Twins MVP)

> Generated 2026-05-25 as part of [Plan 11](../superpowers/plans/11-kbo-data-source-research.md).
> All URLs and endpoints are candidates — collector implementers must verify each before relying on them.
> URLs marked "must verify" are known to exist in this shape but exact paths require a live confirmation run.

## Required data types

- **Schedule** — LG game schedule (date, opponent, venue, start time, game status)
- **Roster** — active LG players (external id, name, position, handedness, uniform number)
- **Player stats** — season + recent + splits (OPS, OBP, SLG, PA vs LHP/RHP, wRC+)
- **Lineup** — announced starting lineup per game (batting order, position, player id)
- **Box score** — completed-game per-player stats (AB, H, 2B, 3B, HR, BB, K, RBI, SB, ...)

---

## Source matrix

| Source | LG coverage | Schedule | Roster | Stats | Lineup | Box score | URL shape | Format | Update timing | Risk | Parser complexity |
|---|---|---|---|---|---|---|---|---|---|---|---|
| KBO Official (Korean) | ✅ | ✅ | ✅ | ⚠️ basic | ⚠️ post-announce | ✅ | `https://www.koreabaseball.com/Schedule/Schedule.aspx` | HTML (server-rendered) | Real-time to daily | medium | Table parse + AJAX |
| KBO Official (English) | ✅ | ✅ | ✅ | ⚠️ basic | ⚠️ post-announce | ✅ | `https://eng.koreabaseball.com/Schedule/Schedule.aspx` | HTML (server-rendered) | Daily | medium | Table parse |
| STATIZ | ✅ | ❌ | ⚠️ partial | ✅ deep | ❌ | ⚠️ game log | `https://statiz.sporki.com/team/?team=LG&year=2026` | HTML (server-rendered) | Post-game / daily | medium | Table parse |
| Naver Sports | ✅ | ✅ | ⚠️ partial | ⚠️ basic | ✅ | ✅ | `https://m.sports.naver.com/kbo/schedule` | JSON-in-HTML + JSON API | Near real-time | medium | JSON API + DOM |
| Daum Sports | ✅ | ✅ | ⚠️ partial | ⚠️ basic | ✅ | ✅ | `https://sports.daum.net/schedule/kbo` | HTML + JSON API | Near real-time | medium | JSON API + DOM |
| MyKBOstats | ✅ | ❌ | ⚠️ partial | ✅ advanced | ❌ | ⚠️ game log | `https://mykbostats.com/teams/LG-Twins` | HTML (server-rendered) | Post-game / daily | low-medium | Table parse |
| Naver Sports API (mobile) | ✅ | ✅ | ⚠️ partial | ⚠️ basic | ✅ | ✅ | `https://api-gw.sports.naver.com/schedule/games` (must verify) | JSON | Near real-time | medium | JSON API |
| KBO community datasets (GitHub/Kaggle) | ✅ | ⚠️ historical | ⚠️ historical | ✅ historical | ❌ | ⚠️ historical | varies by dataset | CSV / JSON | Static historical | low | CSV parse |
| Sportalkorea / Yonhap / OSEN (news) | ✅ | ❌ | ❌ | ❌ | ⚠️ early leak | ❌ | varies | HTML | Pre-announce (irregular) | medium | Unstructured HTML |
| Sports Reference (Baseball) | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | N/A — KBO not covered | N/A | N/A | N/A | N/A |

**Legend:** ✅ supported, ⚠️ partial / unconfirmed, ❌ not supported

---

## Source detail notes

### KBO Official (Korean + English)

- **Korean site** (`www.koreabaseball.com`) is more complete and updated faster than the English mirror.
- **English site** (`eng.koreabaseball.com`) lags by hours but uses the same backend; useful for English-named fields.
- Schedule endpoint accepts `seriesId` (0 = regular season) and `seasonId` (e.g. 2026).
- Roster list is split by position: `PitcherList.aspx?teamCode=LG`, `HitterList.aspx?teamCode=LG`.
- Box score: `Boxscore.aspx?gameId={game_id}` where `game_id` is the KBO-format ID (e.g. `20260415LGKT0`).
- Lineup becomes available on the site after official announcement (varies; often 1-2 h before first pitch).
- Some data (live scores, lineup) is loaded via XHR — inspect network tab to find JSON endpoints.
- Stats coverage: basic counting stats only; no wRC+, no splits.
- **Usage risk:** `medium` — no public API agreement; HTML scraping of a government-affiliated sports org. Rate-limit politely (1 req/5 s, no concurrent bursts).

### STATIZ (`statiz.sporki.com`)

- The most comprehensive publicly available KBO sabermetric stats site in Korean.
- Provides wRC+, OPS+, FIP, WAR, and LHP/RHP splits per player.
- Team batting page: `https://statiz.sporki.com/team/?team=LG&year=2026` (must verify exact path for 2026 season).
- Player page: `https://statiz.sporki.com/player/?m=playerinfo&pCode={player_code}`.
- No schedule or lineup data; game log available per player but not structured as lineup.
- Data updated post-game, usually within a few hours of game end.
- `robots.txt` exists — verify before scraping; historically public data is crawlable but terms unclear.
- **Usage risk:** `medium` — scraping a third-party analytics site. Mitigation: cache aggressively, low frequency (≤1 req/10 s), contact site maintainers if volume grows.

**Plan 14 implementation note (2026-05-25):** Implemented in `apps/api/app/ingestion/collectors/player_stats.py`.
URL templates marked `VERIFY before live use`. Handedness-split availability is encoded at compile time via
`SOURCE_SUPPORTS_HANDEDNESS_SPLITS`; when `False` the collector records a marker payload
(`content_type="application/x-source-metadata+json"`, `"supported": false`) instead of fetching,
so the normalizer (Plan 17) can explicitly skip split scoring rather than fabricating zero-PA splits.

**Plan 15 implementation note (2026-05-25):** Implemented in `apps/api/app/ingestion/collectors/lineup.py`.
URL template marked `VERIFY before live use`. `LineupCollectionResult.status` is `waiting` until the source
response contains a non-empty lineup array, then `collected`. `announced_at` is parsed when available;
deduplication is handled by Plan 12's raw store via the (source_name, source_url, payload_hash) UNIQUE
constraint, so repeated polls of an unchanged lineup don't accumulate rows.

**Plan 16 implementation note (2026-05-25):** Implemented in `apps/api/app/ingestion/collectors/box_score.py`.
URL template marked `VERIFY before live use`. `BoxScoreCollectionResult.status` is `waiting` until the source
response indicates a final game (`gameStatus=FINAL` in JSON or `FINAL`/`경기종료` substring fallback for HTML).
`FinalScore` parsing is optional — collector returns whatever the source exposes, including partial scores
for in-progress games.

### Naver Sports (`m.sports.naver.com/kbo`)

- Largest Korean sports portal; covers KBO schedule, lineups, live scores, and box scores.
- Mobile site (`m.sports.naver.com`) and desktop site both exist; mobile API responses are cleaner JSON.
- Lineup endpoint pattern: `https://m.sports.naver.com/game/{game_id}/lineup` (must verify game_id format).
- Schedule API: `https://api-gw.sports.naver.com/schedule/games?category=kbo&date=20260415` (URL shape known, exact params must verify).
- Box score JSON: available after game ends via similar game-id-based endpoint.
- Lineup announcements often appear on Naver before KBO Official updates.
- **Usage risk:** `medium` — Naver's ToS prohibit automated scraping at scale; rate-limiting and User-Agent checks are enforced. Mitigation: polite headers, caching, reasonable intervals (1 req/5-10 s), fallback to Daum Sports.

### Daum Sports (`sports.daum.net/kbo`)

- Kakao-owned sports portal; similar data coverage to Naver (schedule, lineup, live, box score).
- Desktop and mobile versions; mobile app uses REST API endpoints returning JSON.
- Useful as a fallback to Naver Sports when Naver is rate-limiting.
- **Usage risk:** `medium` — same profile as Naver; polite scraping recommended.

### MyKBOstats (`mykbostats.com`)

- English-language KBO stats; advanced metrics (OPS+, wOBA, wRC+).
- LG Twins roster and season stats available: `https://mykbostats.com/teams/LG-Twins/2026` (must verify year path).
- No lineup or schedule data.
- Smaller site, likely more tolerant; risk `low-medium`.
- **Usage risk:** `low-medium` — small independent site; courteous crawling important to avoid service disruption. Mitigation: cache results (daily), contact maintainers if needed.

### KBO Community Datasets (GitHub / Kaggle)

- Example: GitHub search `kbo-data` yields several repos with historical CSVs.
- Kaggle has at least one KBO historical dataset (confirm license before use).
- Useful for backtesting and historical analysis; not suitable for real-time production use.
- License varies per dataset (MIT, CC, or unlicensed) — verify before including in product.
- **Usage risk:** `low` (if license permits).

### News sites (Sportalkorea, Yonhap, OSEN)

- Occasionally publish lineup leaks 2-4 h before official announcement.
- Not a reliable primary source; parsing is unstructured.
- Useful only as an early-signal supplement if official lineup lag becomes a product problem.
- **Usage risk:** `medium` — scraping news content may trigger ToS issues.

### Sports Reference (Baseball-Reference, etc.)

- Does **not** cover KBO. Confirmed no KBO data as of 2026. Exclude from consideration.

---

## Recommended primary sources per data type

| Data type | Primary | Backup | Rationale |
|---|---|---|---|
| Schedule | KBO Official (Korean) | Naver Sports API | KBO is authoritative; Korean site is faster-updated |
| Roster | KBO Official (Korean) | MyKBOstats | KBO is authoritative; MyKBOstats for English name mapping |
| Player stats | STATIZ | MyKBOstats | STATIZ has splits (vs LHP/RHP), wRC+, and OPS+ not on KBO Official |
| Lineup | Naver Sports | Daum Sports | Naver announces lineup earliest; JSON API cleaner than KBO HTML |
| Box score | KBO Official (Korean) | Naver Sports | KBO is authoritative source of record for final stats |

---

## Risk register

### KBO Official — risk: medium

| Dimension | Detail |
|---|---|
| What goes wrong | HTML structure changes break parsers; server returns 403/429 under aggressive polling |
| Terms | No explicit public API agreement; scraping falls into gray area for a fan/analytics product |
| Mitigation | 1 req/5 s per endpoint, polite User-Agent (`kbo-lineup-lab/0.1 +github.com/songyoungin/kbo-lineup-lab`), cache schedule for 24 h, cache box score permanently once final |
| Fallback | Naver Sports for lineup + box score; STATIZ for stats |

### STATIZ — risk: medium

| Dimension | Detail |
|---|---|
| What goes wrong | Page layout changes break table parsing; robots.txt may restrict crawlers |
| Terms | Public stat pages appear crawlable historically; no explicit API ToS documented |
| Mitigation | Verify `robots.txt` before first use; scrape at ≤1 req/10 s; cache daily; consider reaching out to site operator |
| Fallback | MyKBOstats for English stats; KBO Official for basic counting stats |

### Naver Sports — risk: medium

| Dimension | Detail |
|---|---|
| What goes wrong | Rate-limiting (HTTP 429), User-Agent detection, API endpoint changes without notice |
| Terms | Naver ToS prohibit automated collection at scale; fan-scale usage is tolerated in practice |
| Mitigation | Rotate reasonable intervals (1 req/5-10 s), cache lineup once announced, use mobile API (fewer anti-bot protections than desktop) |
| Fallback | Daum Sports for lineup + box score |

### Daum Sports — risk: medium

| Dimension | Detail |
|---|---|
| What goes wrong | Same profile as Naver; Kakao API endpoint changes |
| Mitigation | Same as Naver; treat as backup, not primary |
| Fallback | KBO Official for box score |

### MyKBOstats — risk: low-medium

| Dimension | Detail |
|---|---|
| What goes wrong | Small site may go offline; scraping could affect availability for other users |
| Mitigation | Cache stats daily; contact maintainer if usage grows |
| Fallback | KBO Official for basic stats; STATIZ for splits |

---

## Open questions

1. **robots.txt verification** — must run `GET /robots.txt` on each source before first collection run.
2. **KBO terms-of-service review** — `koreabaseball.com/Policy` should be reviewed for scraping restrictions.
3. **Naver Developer API** — Naver has a developer platform (`developers.naver.com`); confirm if any sports API is available with an API key that reduces risk vs. scraping.
4. **Paid / partner data providers** — investigate if any KBO-licensed data vendors (e.g. Stats Perform, Sportradar) cover KBO at reasonable cost for a side project.
5. **game_id format** — confirm the exact KBO game_id format (e.g. `YYYYMMDD{home}{away}0`) for 2026 season; used across all endpoints.
6. **LHP/RHP split availability** — verify STATIZ exposes splits as HTML-accessible table (not behind a login wall).

---

## Next steps (Plans 12–17)

| Plan | Task | Recommended starting source |
|---|---|---|
| Plan 12 | Raw payload store — capture and replay fixture files | All sources |
| Plan 13 | Schedule + roster collector | KBO Official (Korean) |
| Plan 14 | Player stats collector | STATIZ |
| Plan 15 | Lineup collector | Naver Sports |
| Plan 16 | Box score collector | KBO Official (Korean) |
| Plan 17 | Normalizer — converts each source payload to validated domain rows | All sources |
