# Per-Player Season Stats — Endpoint Verification

> Verified 2026-05-30. Source: Naver `api-gw.sports.naver.com`. Discovered **without a browser** by
> downloading the player-end JS bundle and reading the URL-building code; confirmed live with curl.

## Endpoint

```
GET https://api-gw.sports.naver.com/players/{categoryId}/{playerId}/playerend-record
```

- `categoryId` = `kbo`; `playerId` = Naver player code (== KBO `playerCode`, e.g. `62415`).
- Headers required: a normal `User-Agent` + `Referer: https://m.sports.naver.com/`. No auth.
- Politeness: reuse the existing `HttpClient(min_interval=5.0)` per-host throttle.
- Verified live: `GET …/players/kbo/62415/playerend-record` → **HTTP 200**, ~17 KB JSON.

### How it was discovered (no Chrome)

The player page (`m.sports.naver.com/player/index?...`) is a React SPA, so the HTML has no API URL, and
Chrome can't reach Naver under this environment's safety restrictions anyway. Instead the JS bundle
(`…/player-end/<build>/static/js/main.<hash>.js`, linked from the page) was downloaded with curl and its
minified code read directly:

```js
// key:"player-end.player.record", get:function(e){ var t=e.categoryId, n=e.playerId;
//   axiosGet("https://api-gw.sports.naver.com" + "/players/" + t + "/" + n + "/playerend-record") }
```

→ `/players/{categoryId}/{playerId}/playerend-record`. Pure code analysis, no DevTools.

## Response shape (verified against captured fixtures)

Top level: `{ "code", "success", "result" }`. `result` keys: `categoryId`, `playerId`, `year`,
`playerType`, `playerDescription` (e.g. `"박해민, 외야수, 우투좌타"`), `basicRecord`, `record`,
`currentSeasonYear`, `seasonRank`, … .

**Important:** `result.basicRecord` and `result.record` are **JSON-encoded strings** — parse each with a
second `json.loads`.

### ⭐ The authoritative season table: `json.loads(result.record)["season"]`

`record` parses to `{ "day_limit", "day_start", "game", "season" }`. **`season` is a list of per-year
batting rows** (one per season the player has played, plus a `gyear:"통산"` career row). Each row is a flat
dict with **all the rate stats present as native numbers** (no derivation needed):

Verified keys on every row:
`pcode, gyear, team, tid, gamenum, ab, run, hit, h2, h3, hr, tb, rbi, sb, cs, sh, sf, bb, hp, kk, gd, err,
hra (AVG, string), obp (number), slg (number), ops (number), isop, babip, woba, wrcPlus, war, wpa, paFlag`.

Example rows for 박해민 (62415):

| gyear | team | ab | hit | h2 | h3 | hr | obp | slg | ops | wrcPlus |
|-------|------|----|----|----|----|----|------|------|------|---------|
| 통산  | 14   | 6131 | 1736 | 244 | 72 | 61 | 0.352 | 0.376 | 0.728 | 0.0 |
| 2026  | LG   | 158 | 40 | 6 | 0 | 1 | 0.319 | 0.31 | 0.629 | 63.9 |
| **2025** | **LG** | **442** | **122** | **18** | **2** | **3** | **0.379** | **0.346** | **0.725** | **107.4** |
| 2024  | LG   | 482 | 127 | 16 | 6 | 6 | 0.336 | 0.359 | 0.695 | 86.9 |

So **OBP, SLG, OPS are all available directly per season** — pick the row by `gyear`. `hra` (AVG) is a
string; `obp`/`slg`/`ops` are numbers. **No SLG derivation needed.**

### Secondary summary: `json.loads(result.basicRecord)["basic"]`

`basicRecord` parses to `{ "basic", "rank" }`. `basic` is the **current-season** summary only, e.g.
`{"hra":"0.253","obp":"0.319","ops":"0.629","slg":<absent>,"year":"2026","wrc_plus":"63.9","war":"0.48",...}`
— rate stats are **strings**, SLG is absent, and it always reflects the *current* season. Prefer the
`record.season` table over `basicRecord.basic`; `basic` is a fallback only.

## Season selection (decided)

The verified game is **2025-05-14**, so the normalizer (Task 4) picks the **`record.season` row where
`gyear == "2025"`**. For a current-season "today's game" pipeline, pick `gyear == str(target_date.year)`.
If the exact year row is absent, fall back to the `통산` (career) row, then to `basicRecord.basic`. Record
the chosen `gyear` in each `stats_json` row for auditing.

## Net for the mapper (Task 2)

Given one `record.season` row, the mapper emits the evaluator `stats_json`:
- **OBP** = `row["obp"]` (number → float)
- **SLG** = `row["slg"]` (number → float)
- **OPS** = `row["ops"]` (number → float)
- `handedness` from `Player.bats`; `primary_position` from `Player.position` (set by the lineup normalizer).

The mapper still coerces any string rate to float and can derive `SLG = total_bases/AB` + `OPS = OBP+SLG`
from counts (`ab,hit,h2,h3,hr`) as a safety net, but for this source the rates are present directly.

wRC+/wOBA/WAR are present too but **out of scope for V1** (the recommender doesn't consume them yet); the
mapper does not emit them.

## Captured fixtures (committed)

- `apps/api/tests/fixtures/sources/naver/player_season_62415.json` — 박해민 (외야수, 우투좌타)
- `apps/api/tests/fixtures/sources/naver/player_season_69102.json` — 문보경 (내야수, 우투좌타)

Distinct players (verified). Re-capture:

```bash
UA='Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 Version/17.0 Mobile/15E148 Safari/604.1'
curl -s --compressed -H "User-Agent: $UA" -H "Referer: https://m.sports.naver.com/" \
  "https://api-gw.sports.naver.com/players/kbo/62415/playerend-record" \
  > apps/api/tests/fixtures/sources/naver/player_season_62415.json
```

## Notes

- Same `api-gw` family already used for schedule/preview/record — same politeness/headers apply.
- This endpoint actually **does** expose wRC+/wOBA/WAR (contrary to the 2026-05-29 finding that no free
  source did) — useful for a future model version, not V1.
