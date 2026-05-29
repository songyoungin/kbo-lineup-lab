# KBO Endpoint Verification (2026-05-29)

> Live confirmation run of the candidate sources in [`kbo-source-matrix.md`](./kbo-source-matrix.md).
> Method: direct `curl` for JSON/POST APIs + Playwright for JS-rendered sites, against the
> 2025 season (a completed game: Kiwoom @ LG, 2025-05-14, KBO `G_ID=20250514WOLG0`).
> Status legend: ✅ verified live (HTTP 200 + expected shape) · ⚠️ partial / needs more work · ❌ blocked.

## TL;DR — what changed vs the matrix

1. **Naver `api-gw.sports.naver.com` is verified and is the richest single source.** One API family
   yields schedule, **lineup + handedness**, starters, opponent/recent/zone splits, and full box score —
   no login required, clean JSON. This is a stronger primary than the matrix assumed.
2. **KBO Official `/ws/*.asmx` JSON endpoints work** (with a `Referer` header) but `/ws/` is **`Disallow`ed by
   robots.txt** — verified, but not robots-clean for production scraping.
3. **STATIZ moved domains**: `statiz.sporki.com` → **`statiz.co.kr`** (old domain has no DNS). The matrix
   URLs are dead. Worse, **player detail (splits / wRC+) is behind a login wall** (`"로그인 후 이용 가능합니다"`),
   so anonymous scraping of advanced metrics is not possible.
4. **No verified source exposes wRC+/wOBA anonymously.** Naver gives OBP/AVG/ERA/WHIP-level metrics; sabermetrics
   (wRC+, wOBA, WAR) live behind STATIZ's login. The V1 scoring model must not assume wRC+ from a free source.

## Verified endpoints

### Naver Sports — `api-gw.sports.naver.com` ✅ (recommended primary)

Headers used: mobile `User-Agent` + `Referer: https://m.sports.naver.com/`. No auth.

| Purpose | Endpoint | Returns |
|---------|----------|---------|
| Schedule | `GET /schedule/games?fields=basic&upperCategoryId=kbaseball&categoryId=kbo&fromDate=YYYY-MM-DD&toDate=YYYY-MM-DD` | `result.games[]`: gameId, gameDateTime, home/awayTeamCode+Name+Score, statusCode (`RESULT`/`BEFORE`/...), winner, cancel/suspended |
| Lineup + handedness + starters | `GET /schedule/games/{gameId}/preview` | `previewData`: `homeTeamLineUp.fullLineUp[]` (`batorder`, `position`, `batsThrows` "좌타", `playerCode`), `homeStarter`/`awayStarter`, `playerInfo.hitType` "우투좌타", `currentSeasonStatsOnOpponents`, `recentFiveGamesStats`, `hotColdZone`, standings |
| Box score | `GET /schedule/games/{gameId}/record` | `recordData`: `battersBoxscore{home,away,*Total}`, `pitchersBoxscore{home,away}`, `scoreBoard{rheb,inn}`, `pitchingResult[]` (W/L/S), `etcRecords[]` |
| Play-by-play | `GET /schedule/games/{gameId}/relay` | full relay/PBP (~80 KB) |
| (Pre-game lineup) | `GET /schedule/games/{gameId}/lineup` | `lineUpData` — was `null` for a finished game; likely populated pre-game only. Use `preview.fullLineUp` instead. |

**Naver gameId = KBO `G_ID` + season year.** Example: KBO `20250514WOLG0` → Naver `20250514WOLG02025`.

### KBO Official — `koreabaseball.com/ws/*.asmx` ✅ (authoritative backup, robots-restricted)

POST, `Content-Type: application/x-www-form-urlencoded`, **`Referer` header required**, JSON body.

| Purpose | Endpoint | Payload | Returns |
|---------|----------|---------|---------|
| Schedule | `POST /ws/Main.asmx/GetKboGameList` | `leId=1&srId=0&date=YYYYMMDD` | `game[]` with `G_ID`, `G_DT`, `AWAY_ID`/`HOME_ID`, start pitchers (`T_PIT_*`/`B_PIT_*`), score, `GAME_STATE_SC`; success `code=100` |
| Game result / scoreboard | `POST /ws/Schedule.asmx/GetScoreBoardScroll` | `leId=1&srId=0&seasonId=2025&gameId={G_ID}` | crowd, team records, scores, start/end time, inning scoreboard (`table1`, HTML-in-JSON) |
| Player stats | `Record/Player/HitterBasic/Basic1.aspx` etc. | ASP.NET WebForms: GET page → POST `__VIEWSTATE`+`__EVENTVALIDATION` | basic counting stats only — **no handedness, no splits, no wRC+/OPS+** |

⚠️ `robots.txt` = `User-agent: * / Disallow: /Common/ /Help/ /Member/ /ws/`. The `/ws/` prefix is exactly the
data API used above. Also note: as of ~2026-05-20 KBO began rejecting non-`koreabaseball.com` `Referer`.

### game_id format ✅ confirmed

```
YYYYMMDD + awayTeamCode(2) + homeTeamCode(2) + sequence(1)
20250514WOLG0  =  2025-05-14, Kiwoom(WO) @ LG, game 0
```
Team codes (franchise-tied, not sponsor): LG=LG, WO=Kiwoom, HT=KIA, SK/SSG, HH=Hanwha, SS=Samsung, KT=KT,
LT=Lotte, NC, OB=Doosan, SM=Sangmu(Futures). KBO internal field name: `G_ID`. Naver appends the season year.

### STATIZ — `statiz.co.kr` ⚠️ (moved + login wall)

- Old `statiz.sporki.com` is **dead (no DNS)**. New domain `https://statiz.co.kr` (HTTP 200).
- Paths: `/player/?m=playerinfo&p_no={id}`, `/team/?m=team&t_code={code}&year=2026`, `/stats/?m=team`. 2026 data present.
- **Player detail triggers a login dialog** (`"로그인 후 이용 가능합니다"`) → splits/wRC+ not anonymously scrapable.
- Open question remaining: whether the public `/stats/?m=team` list pages expose wRC+/splits without login.

### Paid providers

- Sportradar has a "Global Baseball" API family but **lists no explicit KBO coverage**; defer to its Coverage Matrix. Cost unknown.

## Open-source reference (reusable)

- **`kbo-data-portal/collector` (a.k.a. `leewr9/collector`)** — maintained; matches the verified KBO `/ws/` endpoints,
  game_id parsing, and ASP.NET stats scraping. Use as a reference implementation.
- Cross-checked active 2026 repos: `hwinside/kbo-everyday` (documents the endpoints), `hwiVeloper/hwiki`, `seoyunjang/ballverse`.
- `yagongso/KBO_crawler` — **unmaintained (2021), legacy `.nhn`** — do not reuse.

## Revised recommendation (single-team, daily-batch MVP)

| Data type | Primary | Backup | Notes |
|-----------|---------|--------|-------|
| Schedule | Naver `/schedule/games` ✅ | KBO `GetKboGameList` ✅ | Naver cleaner JSON; KBO authoritative |
| Roster + handedness | Naver `preview.playerInfo.hitType` ✅ | — | KBO Official has no handedness |
| Lineup | Naver `preview.fullLineUp` ✅ | KBO GameCenter (dynamic, needs capture) | batorder/position/batsThrows in one call |
| Player stats (basic) | Naver `preview.currentSeasonStats*` ✅ | KBO stats pages | OBP/AVG/ERA/WHIP available |
| Player stats (wRC+/splits) | **none free** ❌ | STATIZ (login) / paid | V1 model should not require wRC+ |
| Box score | Naver `/record` ✅ | KBO `GetScoreBoardScroll` ✅ | Naver has per-player box; KBO authoritative |

**Net:** Naver `api-gw` can be the primary for all five MVP data types; KBO Official is the authoritative backup
for schedule/box score. STATIZ is deferred (login wall). wRC+ is out of scope for a free V1.

## Still needs a live confirmation run

1. Naver `robots.txt` / ToS posture (not yet checked).
2. Whether STATIZ public `/stats/` list pages expose wRC+/splits without login.
3. Naver `preview` pre-game `lineUpData` shape (only the finished-game `null` was observed).
4. KBO 1st-team per-player box score page (lower priority — Naver `/record` covers it).
5. Sportradar KBO coverage + pricing (if a licensed source is ever needed).
