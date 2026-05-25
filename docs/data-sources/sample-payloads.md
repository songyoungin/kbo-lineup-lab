# KBO Data Sample Payloads

> Sample fixture files live in `apps/api/fixtures/source_samples/`.
> Add real captured files there over time so parsers can be developed and tested against representative shapes.
> All sample files listed here are marked `(TBD)` until a collector run captures them.

---

## Schedule sample

**Source:** KBO Official (Korean)
**URL (example):**
```
https://www.koreabaseball.com/Schedule/Schedule.aspx?seriesId=0&seasonId=2026&gameMonth=04
```
**Expected format:** Server-rendered HTML containing a `<table>` of games for the requested month.
**Sample file:** `apps/api/fixtures/source_samples/schedule_kbo_2026_04.html` (TBD — add after first manual capture)

Shape skeleton (parser will extract from HTML table rows):

```json
{
  "game_id": "20260415LGHT0",
  "game_date": "2026-04-15",
  "home_team_code": "LG",
  "away_team_code": "HT",
  "venue": "잠실야구장",
  "start_time": "18:30",
  "status": "final",
  "home_score": 5,
  "away_score": 3,
  "postponed": false
}
```

**Notes:**
- `game_id` follows the pattern `YYYYMMDD{home_code}{away_code}0`; confirm for 2026 season.
- Month is a required query param; iterate months to build a full-season schedule.
- `status` may be `scheduled`, `in_progress`, `final`, or `postponed`.

---

## Roster sample

**Source:** KBO Official (Korean)
**URL (example):**
```
https://www.koreabaseball.com/Player/HitterList.aspx?teamCode=LG
https://www.koreabaseball.com/Player/PitcherList.aspx?teamCode=LG
```
**Expected format:** Server-rendered HTML table listing players for the selected team.
**Sample file:** `apps/api/fixtures/source_samples/roster_lg_hitters_2026.html` (TBD)
**Sample file:** `apps/api/fixtures/source_samples/roster_lg_pitchers_2026.html` (TBD)

Shape skeleton:

```json
{
  "external_id": "78901",
  "name": "홍길동",
  "name_romanized": "Hong Gil-dong",
  "uniform_number": "23",
  "position": "SS",
  "bats": "R",
  "throws": "R",
  "birthdate": "1998-03-12",
  "team_code": "LG"
}
```

**Notes:**
- `external_id` is KBO's internal player code; appears in URL and table.
- Hitters and pitchers are on separate pages; two-pass fetch required.
- `bats`/`throws` may require the Korean-language site (English site sometimes omits handedness).

---

## Player stats sample

**Source:** STATIZ
**URL (example):**
```
https://statiz.sporki.com/team/?team=LG&year=2026
```
**Expected format:** Server-rendered HTML with multiple stat tables (standard, advanced, splits).
**Sample file:** `apps/api/fixtures/source_samples/stats_lg_batting_2026.html` (TBD)
**Sample file:** `apps/api/fixtures/source_samples/stats_lg_pitching_2026.html` (TBD)

Shape skeleton (batting):

```json
{
  "player_external_id": "78901",
  "name": "홍길동",
  "season_pa": 312,
  "season_avg": 0.285,
  "season_obp": 0.361,
  "season_slg": 0.447,
  "season_ops": 0.808,
  "season_wrc_plus": 115,
  "recent_14d_ops": 0.875,
  "recent_30d_ops": 0.822,
  "vs_lhp_pa": 87,
  "vs_lhp_avg": 0.261,
  "vs_lhp_ops": 0.731,
  "vs_rhp_pa": 225,
  "vs_rhp_avg": 0.295,
  "vs_rhp_ops": 0.845
}
```

**Notes:**
- STATIZ splits pages require separate navigation (e.g., `?m=batter_split`); URL shape must be verified per page type.
- Recent n-day stats may require the player detail page (`/player/?m=...`) rather than the team page.
- Verify LHP/RHP splits are accessible without a paid login before implementing.

---

## Lineup sample

**Source:** Naver Sports
**URL (example):**
```
https://m.sports.naver.com/game/{game_id}/lineup
```
where `{game_id}` is Naver's game identifier (may differ from KBO's `game_id`; cross-reference required).

**Expected format:** JSON response from Naver's internal API, or JSON embedded in HTML.
**Sample file:** `apps/api/fixtures/source_samples/lineup_lg_20260415.json` (TBD)

Shape skeleton:

```json
{
  "game_id": "20260415LGHT0",
  "naver_game_id": "2026041503",
  "announced_at": "2026-04-15T15:30:00+09:00",
  "home_team_code": "LG",
  "lineup": [
    {
      "batting_order": 1,
      "player_external_id": "78901",
      "name": "홍길동",
      "position": "SS",
      "bats": "R"
    },
    {
      "batting_order": 2,
      "player_external_id": "78902",
      "name": "김철수",
      "position": "2B",
      "bats": "L"
    }
  ],
  "starting_pitcher": {
    "player_external_id": "78950",
    "name": "이영수",
    "throws": "L"
  }
}
```

**Notes:**
- Naver's `naver_game_id` and KBO's `game_id` may use different formats; a cross-reference map is needed.
- `announced_at` is critical for lineup freshness logic — record it precisely.
- Lineup endpoints are commonly discovered by inspecting the Naver Sports mobile site network tab.
- URL shape `m.sports.naver.com/game/{id}/lineup` is well-known but exact API path must be verified.

---

## Box score sample

**Source:** KBO Official (Korean)
**URL (example):**
```
https://www.koreabaseball.com/Schedule/Boxscore.aspx?gameId=20260415LGHT0
```
**Expected format:** Server-rendered HTML with batting and pitching result tables per team.
**Sample file:** `apps/api/fixtures/source_samples/boxscore_lg_20260415.html` (TBD)

Shape skeleton (batting per player):

```json
{
  "game_id": "20260415LGHT0",
  "team_code": "LG",
  "player_external_id": "78901",
  "name": "홍길동",
  "batting_order": 1,
  "position": "SS",
  "ab": 4,
  "r": 1,
  "h": 2,
  "rbi": 1,
  "bb": 1,
  "k": 1,
  "double": 1,
  "triple": 0,
  "hr": 0,
  "sb": 0,
  "gidp": 0,
  "avg_after": 0.288
}
```

Shape skeleton (pitching per pitcher):

```json
{
  "game_id": "20260415LGHT0",
  "team_code": "LG",
  "player_external_id": "78950",
  "name": "이영수",
  "result": "W",
  "ip": 7.0,
  "bf": 28,
  "h": 5,
  "r": 2,
  "er": 2,
  "bb": 2,
  "k": 8,
  "hr": 1,
  "era_after": 3.12
}
```

**Notes:**
- Box score page is only available after the game reaches `final` status.
- Parse both home and away tables; store both teams for completeness.
- HTML tables typically include a summary row (team totals) that must be excluded from per-player rows.

---

## Adding real samples

When a collector run captures real data, save files into `apps/api/fixtures/source_samples/` following this naming convention:

```
{data_type}_{team}_{YYYYMMDD}[_{source}].{ext}
```

Examples:
- `schedule_kbo_20260401_kbo-official.html`
- `roster_lg_20260401_kbo-official.html`
- `stats_lg_20260415_statiz.html`
- `lineup_lg_20260415_naver.json`
- `boxscore_lg_20260415_kbo-official.html`

Commit sample files with `fixture(data): add {description} sample` and reference the source URL in the commit body.
