# Bench-Hitter Pool — `batterCandidate`

> Verified 2026-05-31 against the committed fixture
> `apps/api/tests/fixtures/sources/naver/preview_20250514WOLG02025.json` (game 2025-05-14).
> No new HTTP endpoint — this is a block of the Naver preview payload already fetched per game.

## What it is

The recommender's candidate hitter pool is sourced from the Naver Sports **preview** payload that the
pipeline already fetches per game — specifically:

```
result.previewData.homeTeamLineUp.batterCandidate   # the LG-side block when LG is home
result.previewData.awayTeamLineUp.batterCandidate   # when LG is away
```

`batterCandidate` is the set of **bench position players** available for that game. It complements
`fullLineUp` (the 9 starters + the starting pitcher) on the same `homeTeamLineUp` / `awayTeamLineUp`
object. There is **no separate or new HTTP request** — the same preview fetch that yields `fullLineUp`
also carries `batterCandidate`.

## Field shape (verified against the fixture)

Each entry is a flat dict. The fixture's first candidate:

```json
{"hitType": "우투우타", "playerName": "박동원", "pos": "2", "playerCode": "79365", "position": "포수", "batsThrows": "우타"}
```

| Field        | Example     | Meaning |
|--------------|-------------|---------|
| `playerCode` | `"79365"`   | Naver player code (== `Player.external_id`). The same code used by the per-player season-stats endpoint `https://api-gw.sports.naver.com/players/kbo/{code}/playerend-record`. |
| `playerName` | `"박동원"`  | Korean name. |
| `pos`        | `"2"`       | **NUMERIC** position token. **This** is what maps to the canonical position via `to_position` (e.g. `"2"` → `C`). |
| `position`   | `"포수"`    | Korean position **word**. **NOT** recognized by `to_position` (it would fall back to `DH`), which is why the normalizer reads `pos`, not `position`, for bench hitters. |
| `hitType`    | `"우투우타"`| Bat/throw descriptor; used to parse handedness. |
| `batsThrows` | `"우타"`    | Batting/throwing handedness. |

### Field-layout contrast with `fullLineUp`

The two arrays put the same information under **different keys** — do not assume they share a schema. In
the same fixture a `fullLineUp` entry looks like:

```json
{"positionName": "선발투수", "backnum": "13", "hitType": "좌완투수", "playerName": "송승기", "birth": "20020410", "weight": "90.0", "playerCode": "51111", "position": "1", "batsThrows": "좌투", "height": "181.0"}
```

So in `fullLineUp` the **numeric** token is in `position` and the Korean word is in `positionName`; in
`batterCandidate` the **numeric** token is in `pos` and the Korean word is in `position`. The normalizer
accounts for this difference.

## How it flows through the code

1. **`normalize_lineup`** (`apps/api/app/ingestion/normalizers/lineup.py`) iterates each
   `batterCandidate` entry and upserts it as a `Player` row, mapping `pos` onto the `position` key that
   `_upsert_player` expects. Bench hitters carry **no batting order**, so **no `ActualLineupSnapshotRow`**
   is created for them (those rows are reserved for the starting nine from `fullLineUp`).
2. **`_collect_roster_player_season_stats`** (`apps/api/app/jobs/daily_pipeline.py`) then fetches season
   stats for **all team hitters** — every `Player` row for the team with `position != 'P'` — via the
   `playerend-record` endpoint. Because the bench hitters are now `Player` rows, the recommender's
   candidate pool includes them, not just the 9 starters.

For the 2025-05-14 fixture this yields **9 starters + 6 bench = 15 distinct available hitters** (zero
overlap). The end-to-end test asserts this; see the verification note below.

## Open question (verify across multiple game dates)

It is **not yet confirmed** whether `batterCandidate` reliably lists *every* active-entry hitter not in
the starting nine, or whether it can omit held-out entry hitters. The single 2025-05-14 fixture lists 6
bench hitters (15 total available, zero overlap with the starters), which is consistent but not
conclusive. Confirm by capturing previews from several dates and checking that
`fullLineUp` hitters + `batterCandidate` equals the team's active-entry hitters for that game.

## Known limitation

`_collect_roster_player_season_stats` iterates **all** `Player` rows for the team (excluding pitchers),
**not** a per-date roster snapshot. Over a long season the `Player` table accumulates every hitter ever
seen — inserted via lineup *or* box-score upserts — so the candidate pool can include players no longer
on the active entry. This is **acceptable for the current MVP**. A future iteration could scope the pool
to the current preview's `batterCandidate` set per game date.

## Live re-verification note

Confirming the wider pool against the live Supabase project requires **re-ingesting a date whose
ingestion run is not yet `completed`** — idempotent runs short-circuit, so an already-`completed` run
will not re-fetch — after this change is merged/deployed. In CI the behavior is verified by the
fixture-backed end-to-end test
`apps/api/tests/ingestion/test_daily_pipeline_naver.py`, which asserts the stat snapshot now holds **15**
hitters (9 starters + 6 bench).

## Re-capturing the preview fixture

```bash
UA='Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 Version/17.0 Mobile/15E148 Safari/604.1'
curl -s --compressed -H "User-Agent: $UA" -H "Referer: https://m.sports.naver.com/" \
  "<naver preview URL for the target game>" \
  > apps/api/tests/fixtures/sources/naver/preview_<gameId>.json
```
