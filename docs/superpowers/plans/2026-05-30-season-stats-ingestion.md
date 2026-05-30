# Season Stats Ingestion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ingest season batting stats (OBP/SLG/OPS) for every player in an announced LG lineup so the lineup recommender runs on real data instead of the 2-player preview stats.

**Architecture:** Add a per-player season-stat collector that fetches each lineup player's season record from a verified source, a pure mapper that converts the source's raw fields into the evaluator's `stats_json` schema (numeric `OPS`/`OBP`/`SLG` + handedness + canonical position), and a rewritten player-stats normalizer that writes one `PlayerStatSnapshotRow` per lineup player into a single `StatSnapshot`. The daily pipeline calls the collector for each of the 9 lineup players after the lineup snapshot is created. Keep the existing `collector → raw_store → normalizer → snapshot` flow and idempotency (raw store dedup + content-hash snapshots).

**Tech Stack:** Python 3.13, FastAPI, SQLAlchemy 2.x, httpx, pydantic v2, pytest, uv. Run all commands from `apps/api` with `uv run`. English commits/docstrings/comments (project README convention). Pre-commit hooks (ruff, ruff-format, mypy, bandit, vulture) must pass.

---

## Why this is needed (context)

The recommender (`app/lineup_model/recommendation.py::recommend_lineup`) takes a `player_pool: list[HitterStats]` and fills 9 slots by position eligibility. The pool is built from the `PlayerStatSnapshotRow`s attached to the evaluation run's `stat_snapshot_id`. Today the real-data stats normalizer (`app/ingestion/normalizers/player_stats.py`) writes only **2** rows (the preview's `homeTopPlayer` hitter + `homeStarter` pitcher), so the pool can't fill 9 slots → `ValueError: Cannot fill position C: no eligible player remaining in pool`. We need one stat row per lineup player.

The evaluator (`app/services/lineup_evaluator.py::build_hitter_stats`) reads these `stats_json` keys (verified in code):

| key | type | absent default | notes |
|-----|------|----------------|-------|
| `OPS` | number | `0.0` | **string raises TypeError** — must be float |
| `OBP` | number | `0.0` | same |
| `SLG` | number | `0.0` | same |
| `handedness` | `"L"`/`"R"`/`"S"` | `Handedness.RIGHT` | from `Handedness` enum |
| `primary_position` | Position value (`"CF"`,`"1B"`,…) | falls back to `player_position`, then `"DH"` | `Position` enum; unknown → `DH` |
| `secondary_positions` | list of Position values | `()` | optional |
| `recent_positions` | list of Position values | `()` | optional |
| `recent_14d_ops`,`recent_30d_ops` | number or absent | `None` | optional |
| `vs_rhp_ops`,`vs_lhp_ops` | number or absent | `None` | optional |
| `vs_rhp_pa`,`vs_lhp_pa` | number | `0` | optional |
| `starts_last_5_games` | number | `0` | optional |

**Minimum to make the recommender work:** one row per lineup player with numeric `OPS`/`OBP`/`SLG`, plus `handedness` and `primary_position` (the latter two are already on the upserted `Player` rows from the lineup normalizer, so the mapper can also fall back to those). The optional split/recent fields are out of scope for V1 (leave absent → safe defaults).

**Naver preview shape (verified, fixture `tests/fixtures/sources/naver/preview_20250514WOLG02025.json`):** `fullLineUp` batter entries carry `playerCode`, `position` (numeric code), `batsThrows`, `hitType` — but **no season stats**. Only `homeTopPlayer.currentSeasonStats` / `homeStarter.currentSeasonStats` exist, e.g. `{"ab":103,"hit":29,"hra":"0.282","rbi":14,"hr":0,"obp":0.348}` — note `hra` is a string, `obp` is a number, and **there is no `slg`/`ops`**. So even the 2 players we get need OPS/SLG derived, and the other 7 need a per-player source.

---

## File Structure

| File | Responsibility | Task |
|------|----------------|------|
| `docs/data-sources/player-season-stats-verification.md` (new) | record the verified per-player season-stat endpoint + exact field names + a captured sample | 1 |
| `tests/fixtures/sources/naver/player_season_<code>.json` (new, ×2) | captured real per-player responses for normalizer tests | 1 |
| `app/ingestion/season_stats_map.py` (new) | pure mapper: raw source season dict → evaluator `stats_json` (numeric OPS/OBP/SLG, handedness, primary_position); derive SLG/OPS when only counts present | 2 |
| `app/ingestion/collectors/season_stats.py` (new) | `build_player_season_url`, `collect_player_season_stats(player_code)` → store raw payload | 3 |
| `app/ingestion/normalizers/player_stats.py` (rewrite) | build one `PlayerStatSnapshotRow` per lineup player from the captured season payloads, matched by `playerCode` | 4 |
| `app/jobs/daily_pipeline.py` (modify) | after lineup normalize, fetch season stats for each lineup player, then normalize into one `StatSnapshot` | 5 |
| `tests/ingestion/test_season_stats_map.py` (new) | unit-test the mapper | 2 |
| `tests/ingestion/test_season_stats_collector.py` (new) | collector URL + raw store | 3 |
| `tests/ingestion/test_player_stats_naver.py` (rewrite) | normalizer builds 9 rows with numeric OPS/OBP/SLG | 4 |
| `tests/ingestion/test_daily_pipeline_naver.py` (modify) | E2E: stat snapshot has 9 rows; recommender fills 9 slots | 5 |

**Single source-shape seam.** Everything except `season_stats_map.py` field names and `season_stats.py`'s URL is independent of which source Task 1 picks. Task 1 fills in the exact endpoint + field names; Tasks 2–5 are written against the mapper's output schema (the evaluator's `stats_json`), which does not change.

---

## Task 1: Discover & verify the per-player season-stat API endpoint — ✅ DONE 2026-05-30

**DONE.** Discovered **without a browser** (Chrome can't reach Naver under safety restrictions) by downloading the player-end JS bundle with curl and reading its URL-building code, then verified live. All runtime extraction is pure `httpx` API (Task 3) — no browser. Full write-up: [`docs/data-sources/player-season-stats-verification.md`](../../data-sources/player-season-stats-verification.md).

**Verified endpoint:**
```
GET https://api-gw.sports.naver.com/players/{categoryId}/{playerId}/playerend-record
```
`categoryId="kbo"`, `playerId` = Naver player code (== KBO `playerCode`). Headers: `User-Agent` + `Referer: https://m.sports.naver.com/`. Confirmed `…/players/kbo/62415/playerend-record` → HTTP 200.

**Response (verified):** top `{code,success,result}`; `result.basicRecord` and `result.record` are **JSON-encoded strings** (parse with a second `json.loads`). **The authoritative table is `json.loads(result.record)["season"]`** — a list of per-year batting rows, each a flat dict with **OBP/SLG/OPS present as native numbers** (no derivation needed) plus `ab,hit,h2,h3,hr,bb,hp,sf,tb,hra,wrcPlus,woba,war,gyear,team,…`. Pick the row by `gyear` (e.g. `"2025"`). Example (62415, gyear 2025): `ab=442, obp=0.379, slg=0.346, ops=0.725`. (`basicRecord.basic` is a current-season-only string-typed summary without SLG — used as a last-resort fallback.)

**Season selection (decided):** the normalizer (Task 4) picks the `record.season` row where `gyear == "2025"` (the verified game's year); for a current-season "today" pipeline, `gyear == str(target_date.year)`. Fallback order: exact year → `통산` (career) row → `basicRecord.basic`. Record the chosen `gyear` in each `stats_json` for auditing.

**Files (created):**
- `docs/data-sources/player-season-stats-verification.md`
- `apps/api/tests/fixtures/sources/naver/player_season_62415.json` (박해민), `player_season_69102.json` (문보경) — verified distinct players, both with a 2025 row.

- [x] **Step 1: Discover the endpoint (done — JS-bundle code analysis, no DevTools)**

The Naver player page is a React SPA, so the URL is built at runtime. The bundle (`…player-end/<build>/static/js/main.<hash>.js`, linked from `m.sports.naver.com/player/index?...`) was fetched with curl and grepped: its code reads `get:function(e){var t=e.categoryId,n=e.playerId; axiosGet("https://api-gw.sports.naver.com"+"/players/"+t+"/"+n+"/playerend-record")}` → endpoint confirmed.

- [x] **Step 2: Capture two fixtures (done)**

```bash
cd apps/api && mkdir -p tests/fixtures/sources/naver
UA='Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 Version/17.0 Mobile/15E148 Safari/604.1'
curl -s --compressed -H "User-Agent: $UA" -H "Referer: https://m.sports.naver.com/" \
  'https://api-gw.sports.naver.com/players/kbo/62415/playerend-record' \
  > tests/fixtures/sources/naver/player_season_62415.json
curl -s --compressed -H "User-Agent: $UA" -H "Referer: https://m.sports.naver.com/" \
  'https://api-gw.sports.naver.com/players/kbo/69102/playerend-record' \
  > tests/fixtures/sources/naver/player_season_69102.json
```

- [x] **Step 3: Document the verified shape (done)** — see the verification doc (exact field names, season-selection rule, mapper guidance).

- [x] **Step 4: Commit (done in this session)**

```bash
git add docs/data-sources/player-season-stats-verification.md docs/superpowers/plans/2026-05-30-season-stats-ingestion.md tests/fixtures/sources/naver/player_season_62415.json tests/fixtures/sources/naver/player_season_69102.json
git commit -m "docs(data): verify Naver per-player season-stat endpoint and capture fixtures"
```

> **Downstream note for Tasks 2–4:** wherever this plan writes raw field names (`hra`, `obp`, `h2`, `h3`, `hr`, `bb`, `hp`, `sf`, `ab`, `hit`), replace them with the verified names from Step 3 if they differ. The mapper's *output* keys (`OPS`/`OBP`/`SLG`/`handedness`/`primary_position`) never change.

---

## Task 2: Season-stat mapper (pure function)

Converts one source season dict into the evaluator's `stats_json`. Pure and fully unit-testable, no I/O. Derives SLG/OPS when the source gives only counts; coerces string rates to float; never emits a string for OPS/OBP/SLG.

**Files:**
- Create: `app/ingestion/season_stats_map.py`
- Test: `tests/ingestion/test_season_stats_map.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/ingestion/test_season_stats_map.py
"""Verifies the season-stat mapper produces the evaluator stats_json schema:
numeric OPS/OBP/SLG, handedness, and a canonical primary_position. Derives
SLG/OPS from counts when the source omits them."""
from __future__ import annotations

from app.ingestion.season_stats_map import map_season_stats


def test_maps_rates_and_coerces_strings_to_float():
    # Source provides OBP as number and AVG ("hra") as string; SLG/OPS absent.
    raw = {"ab": 103, "hit": 29, "h2": 6, "h3": 1, "hr": 0, "bb": 12, "hp": 1, "sf": 1,
           "hra": "0.282", "obp": 0.348}
    out = map_season_stats(raw, bats="L", position="CF")
    # OBP passes through as float
    assert isinstance(out["OBP"], float) and abs(out["OBP"] - 0.348) < 1e-9
    # SLG derived: total_bases = 29 + 6 + 2*1 + 3*0 = 37; 37/103 = 0.359...
    assert isinstance(out["SLG"], float) and abs(out["SLG"] - 37 / 103) < 1e-6
    # OPS = OBP + SLG
    assert isinstance(out["OPS"], float) and abs(out["OPS"] - (0.348 + 37 / 103)) < 1e-6
    assert out["handedness"] == "L"
    assert out["primary_position"] == "CF"


def test_prefers_source_slg_ops_when_present():
    raw = {"ab": 100, "hit": 30, "obp": 0.400, "slg": 0.550, "ops": 0.950}
    out = map_season_stats(raw, bats="R", position="1B")
    assert abs(out["SLG"] - 0.550) < 1e-9
    assert abs(out["OPS"] - 0.950) < 1e-9


def test_zero_ab_does_not_divide_by_zero():
    raw = {"ab": 0, "hit": 0, "obp": 0.0}
    out = map_season_stats(raw, bats=None, position=None)
    assert out["SLG"] == 0.0 and out["OPS"] == out["OBP"]
    assert out["handedness"] == "R"          # None bats -> default R
    assert out["primary_position"] == "DH"   # None position -> DH


def test_string_rates_everywhere_are_coerced():
    raw = {"ab": "50", "hit": "20", "obp": "0.380", "slg": "0.500", "ops": "0.880"}
    out = map_season_stats(raw, bats="S", position="SS")
    assert isinstance(out["OPS"], float) and abs(out["OPS"] - 0.880) < 1e-9
    assert out["handedness"] == "S"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/ingestion/test_season_stats_map.py -v`
Expected: FAIL — module `app.ingestion.season_stats_map` not found.

- [ ] **Step 3: Implement the mapper**

```python
# app/ingestion/season_stats_map.py
"""Map a source season-batting dict into the evaluator stats_json schema.

The evaluator (app/services/lineup_evaluator.py::build_hitter_stats) requires
numeric OPS/OBP/SLG (a string raises TypeError) plus handedness and a canonical
primary_position. Naver exposes OBP and AVG ("hra") but not SLG/OPS, so SLG is
derived from total bases / AB and OPS from OBP + SLG. Field names follow the
source verified in docs/data-sources/player-season-stats-verification.md; adjust
the keys in _extract if a different source is used.
"""

from __future__ import annotations

from typing import Any

__all__ = ["map_season_stats"]


def _num(value: Any, default: float = 0.0) -> float:
    """Coerce a source value (number or numeric string) to float."""
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def map_season_stats(
    raw: dict[str, Any], *, bats: str | None, position: str | None
) -> dict[str, Any]:
    """Return an evaluator stats_json dict from a source season-batting row.

    Args:
        raw: One season batting row from the source (keys per the verification
            doc): ab, hit, h2, h3, hr, bb, hp, sf, hra, obp, optionally slg/ops.
        bats: Player batting handedness ("L"/"R"/"S") or None.
        position: Canonical Position value ("CF","1B",...) or None.

    Returns:
        stats_json with float OPS/OBP/SLG, handedness, primary_position, and the
        raw source row preserved under "_source" for auditing.
    """
    ab = _num(raw.get("ab"))
    obp = _num(raw.get("obp"))

    slg_raw = raw.get("slg")
    if slg_raw is not None:
        slg = _num(slg_raw)
    elif ab > 0:
        singles = _num(raw.get("hit")) - _num(raw.get("h2")) - _num(raw.get("h3")) - _num(raw.get("hr"))
        total_bases = singles + 2 * _num(raw.get("h2")) + 3 * _num(raw.get("h3")) + 4 * _num(raw.get("hr"))
        slg = total_bases / ab
    else:
        slg = 0.0

    ops_raw = raw.get("ops")
    ops = _num(ops_raw) if ops_raw is not None else obp + slg

    return {
        "OPS": ops,
        "OBP": obp,
        "SLG": slg,
        "handedness": bats if bats in ("L", "R", "S") else "R",
        "primary_position": position or "DH",
        "_source": raw,
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/ingestion/test_season_stats_map.py -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add app/ingestion/season_stats_map.py tests/ingestion/test_season_stats_map.py
git commit -m "feat(ingestion): add season-stat mapper to evaluator schema"
```

---

## Task 3: Per-player season-stat collector

Fetches one player's season-stat JSON and stores it as a raw payload (category `PLAYER_STATS`, `source_name="naver_sports"`). Mirrors the existing collector shape (`app/ingestion/collectors/box_score.py` is the reference). Uses the URL verified in Task 1.

**Files:**
- Create: `app/ingestion/collectors/season_stats.py`
- Test: `tests/ingestion/test_season_stats_collector.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/ingestion/test_season_stats_collector.py
"""Season-stat collector builds the verified per-player URL, sends a Referer,
and stores the raw JSON payload (idempotent via the raw store)."""
from __future__ import annotations

import httpx

from app.ingestion.collectors.season_stats import (
    build_player_season_url,
    collect_player_season_stats,
)
from app.models.snapshot import IngestionRun


def test_build_player_season_url_contains_player_code():
    url = build_player_season_url(player_code="62415")
    assert "62415" in url and "api-gw.sports.naver.com" in url


def test_collect_stores_payload(session, mock_http, load_source):
    body = load_source("naver/player_season_62415.json")

    def handler(request: httpx.Request) -> httpx.Response:
        assert "Referer" in request.headers
        return httpx.Response(200, text=body, headers={"content-type": "application/json"})

    run = IngestionRun(source="test:season", status="running")
    session.add(run); session.flush()
    payload, created = collect_player_season_stats(
        session=session, ingestion_run=run, player_code="62415", http=mock_http(handler),
    )
    assert created is True
    assert payload.source_name == "naver_sports"
    assert "62415" in payload.source_url
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/ingestion/test_season_stats_collector.py -v`
Expected: FAIL — module `app.ingestion.collectors.season_stats` not found.

- [ ] **Step 3: Implement the collector**

Use the URL template verified in Task 1. The template below shows the expected shape — **replace `PLAYER_SEASON_URL` with the exact verified template from `docs/data-sources/player-season-stats-verification.md`** (same `{code}` substitution, same query params).

```python
# app/ingestion/collectors/season_stats.py
"""Collector for a single player's season batting stats from Naver api-gw.

Stores the raw JSON via the shared raw store (idempotent on
source_name+source_url+payload_hash). The exact URL is the one verified in
docs/data-sources/player-season-stats-verification.md.
"""

from __future__ import annotations

from typing import Final

from sqlalchemy.orm import Session

from app.ingestion.collectors.lineup import NAVER_REFERER
from app.ingestion.http_client import HttpClient
from app.ingestion.raw_store import save_raw_payload
from app.ingestion.types import PayloadCategory
from app.models.snapshot import IngestionRun, RawIngestionPayload
from app.schemas.ingestion import RawPayloadCreate

__all__ = ["build_player_season_url", "collect_player_season_stats"]

# VERIFIED in Task 1 — replace with the exact template from the verification doc.
PLAYER_SEASON_URL: Final = "https://api-gw.sports.naver.com/sports/player/baseball/{code}/record"
NAVER_SOURCE_NAME: Final = "naver_sports"


def build_player_season_url(*, player_code: str) -> str:
    """Naver per-player season-record URL for a player code."""
    return PLAYER_SEASON_URL.format(code=player_code)


def collect_player_season_stats(
    *, session: Session, ingestion_run: IngestionRun, player_code: str, http: HttpClient
) -> tuple[RawIngestionPayload, bool]:
    """Fetch one player's season-stat JSON from Naver and store it raw.

    Raises:
        FetchError: If the request fails after retries.
    """
    result = http.fetch(
        build_player_season_url(player_code=player_code), headers={"Referer": NAVER_REFERER}
    )
    payload = RawPayloadCreate(
        ingestion_run_id=ingestion_run.id, category=PayloadCategory.PLAYER_STATS,
        source_name=NAVER_SOURCE_NAME, source_url=result.url, fetched_at=result.fetched_at,
        content_type=result.content_type, raw_body=result.body,
    )
    return save_raw_payload(session, payload)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/ingestion/test_season_stats_collector.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add app/ingestion/collectors/season_stats.py tests/ingestion/test_season_stats_collector.py
git commit -m "feat(ingestion): add per-player season-stat collector"
```

---

## Task 4: Rewrite the player-stats normalizer (one row per lineup player)

Replace the preview-based normalizer so it builds one `PlayerStatSnapshotRow` per LG lineup player, using the per-player season payloads + the mapper. Match each Player by `external_id == playerCode`; pull `bats`/`position` from the upserted `Player` (the lineup normalizer already set canonical values). Keep `PlayerStatsNormalizeResult`, the content-hash idempotency, and the `_extract_season_row` helper that reads the verified JSON path.

**Files:**
- Modify: `app/ingestion/normalizers/player_stats.py`
- Modify/rewrite: `tests/ingestion/test_player_stats_naver.py`
- Reuse: `app/ingestion/season_stats_map.py` (Task 2), captured `player_season_*.json` (Task 1)

- [ ] **Step 1: Write the failing test**

```python
# tests/ingestion/test_player_stats_naver.py  (rewrite)
"""normalize_player_stats builds one PlayerStatSnapshotRow per lineup player
from per-player season payloads, with numeric OPS/OBP/SLG, and is idempotent."""
from __future__ import annotations

from datetime import UTC, date, datetime

from app.ingestion.normalizers.player_stats import normalize_player_stats
from app.ingestion.raw_store import save_raw_payload
from app.ingestion.types import PayloadCategory
from app.models.game import Game
from app.models.player import Player
from app.models.snapshot import IngestionRun, PlayerStatSnapshotRow, StatSnapshot
from app.models.team import Team
from app.schemas.ingestion import RawPayloadCreate


def _seed(session) -> tuple[Team, Game]:
    lg = Team(code="LG", name="LG"); wo = Team(code="WO", name="Kiwoom")
    session.add_all([lg, wo]); session.flush()
    game = Game(external_id="20250514WOLG0", home_team_id=lg.id, away_team_id=wo.id,
                game_date=date(2025, 5, 14))
    session.add(game); session.flush()
    # Two lineup players already upserted by the lineup normalizer (canonical pos + bats).
    session.add_all([
        Player(team_id=lg.id, external_id="62415", name="박해민", position="CF", bats="L", throws="R"),
        Player(team_id=lg.id, external_id="69102", name="문보경", position="3B", bats="L", throws="R"),
    ])
    session.flush()
    return lg, game


def _save(session, run, code, load_source) -> None:
    save_raw_payload(session, RawPayloadCreate(
        ingestion_run_id=run.id, category=PayloadCategory.PLAYER_STATS, source_name="naver_sports",
        source_url=f"https://api-gw.sports.naver.com/sports/player/baseball/{code}/record",
        fetched_at=datetime.now(UTC), content_type="application/json",
        raw_body=load_source(f"naver/player_season_{code}.json"),
    ))


def test_builds_one_row_per_player_with_numeric_rates(session, load_source):
    _seed(session)
    run = IngestionRun(source="test:season", status="running"); session.add(run); session.flush()
    _save(session, run, "62415", load_source)
    _save(session, run, "69102", load_source)

    result = normalize_player_stats(session, game_external_id="20250514WOLG0",
                                    ingestion_run_id=run.id)

    assert session.query(StatSnapshot).count() == 1
    rows = session.query(PlayerStatSnapshotRow).all()
    assert len(rows) == 2
    for row in rows:
        assert isinstance(row.stats_json["OPS"], float)
        assert isinstance(row.stats_json["OBP"], float)
        assert isinstance(row.stats_json["SLG"], float)
    assert result.rows_created == 2


def test_idempotent(session, load_source):
    _seed(session)
    run = IngestionRun(source="test:season", status="running"); session.add(run); session.flush()
    _save(session, run, "62415", load_source)
    normalize_player_stats(session, game_external_id="20250514WOLG0", ingestion_run_id=run.id)
    second = normalize_player_stats(session, game_external_id="20250514WOLG0", ingestion_run_id=run.id)
    assert second.rows_created == 0
    assert session.query(StatSnapshot).count() == 1
```

> NOTE for the implementer: the new `normalize_player_stats` signature is
> `normalize_player_stats(session, *, game_external_id, ingestion_run_id)` — it
> reads ALL `PLAYER_STATS` raw payloads for that run, matches each to a Player by
> the `playerCode` embedded in `source_url`, and builds one row per matched player.
> Update the JSON path inside `_extract_season_row` to the field verified in Task 1.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/ingestion/test_player_stats_naver.py -v`
Expected: FAIL — old `normalize_player_stats(session, raw_payload)` signature / preview parsing.

- [ ] **Step 3: Rewrite the normalizer**

```python
# app/ingestion/normalizers/player_stats.py  (rewrite)
"""Normalize per-player season-stat payloads into one StatSnapshot with one
PlayerStatSnapshotRow per LG lineup player.

Each PLAYER_STATS raw payload holds one player's season record (collected by
app/ingestion/collectors/season_stats.py). We match each payload to a Player by
the playerCode embedded in source_url, map its season row into the evaluator
stats_json schema (app/ingestion/season_stats_map.py), and write one snapshot
row per player. The snapshot dedups on content_hash for idempotency.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ingestion.normalizers._shared import compute_content_hash
from app.ingestion.season_stats_map import map_season_stats
from app.ingestion.types import PayloadCategory
from app.models.player import Player
from app.models.snapshot import PlayerStatSnapshotRow, RawIngestionPayload, StatSnapshot

__all__ = ["PlayerStatsNormalizeResult", "normalize_player_stats"]

_PLAYER_CODE_RE: Final = re.compile(r"/player/baseball/([^/]+)/")


@dataclass(frozen=True)
class PlayerStatsNormalizeResult:
    """Result of normalizing per-player season stats.

    Attributes:
        snapshot_id: PK of the created or existing StatSnapshot.
        rows_created: Number of newly inserted PlayerStatSnapshotRow rows.
        rows_skipped: Number of payloads skipped (no player code or no match).
        needs_review_reasons: Reasons requiring manual review.
    """

    snapshot_id: int
    rows_created: int
    rows_skipped: int
    needs_review_reasons: tuple[str, ...]


def _extract_season_row(body: dict[str, Any]) -> dict[str, Any] | None:
    """Return the latest season batting row from a player payload, or None.

    Update the JSON path here to match the source verified in Task 1.
    """
    # Example shape — adjust to the verified path. Assumes a list of season rows
    # under result.recordData.hitterSeason with a year/season field.
    result = (body.get("result") or {}).get("recordData") or {}
    rows = result.get("hitterSeason") or []
    rows = [r for r in rows if isinstance(r, dict)]
    if not rows:
        return None
    # Pick the most recent season (largest year-ish key); fall back to last.
    def _season_key(r: dict[str, Any]) -> str:
        return str(r.get("gyear") or r.get("seasonId") or "")
    return max(rows, key=_season_key)


def normalize_player_stats(
    session: Session, *, game_external_id: str, ingestion_run_id: int
) -> PlayerStatsNormalizeResult:
    """Build one StatSnapshot with a row per lineup player for the run.

    Args:
        session: Active SQLAlchemy session. Caller controls the transaction.
        game_external_id: KBO game id (used for auditing in content hash).
        ingestion_run_id: Run whose PLAYER_STATS payloads to normalize.

    Returns:
        PlayerStatsNormalizeResult.
    """
    payloads = list(
        session.execute(
            select(RawIngestionPayload).where(
                RawIngestionPayload.ingestion_run_id == ingestion_run_id,
                RawIngestionPayload.category == PayloadCategory.PLAYER_STATS.value,
            )
        ).scalars()
    )

    rows_skipped = 0
    needs_review_reasons: list[str] = []
    mapped: list[tuple[int, dict[str, Any]]] = []  # (player_id, stats_json)

    for payload in payloads:
        m = _PLAYER_CODE_RE.search(payload.source_url)
        if m is None:
            rows_skipped += 1
            needs_review_reasons.append(f"no player code in url: {payload.source_url!r}")
            continue
        player_code = m.group(1)
        player = session.execute(
            select(Player).where(Player.external_id == player_code)
        ).scalar_one_or_none()
        if player is None:
            rows_skipped += 1
            needs_review_reasons.append(f"no Player for code {player_code!r}")
            continue
        try:
            body = json.loads(payload.raw_body)
        except json.JSONDecodeError:
            rows_skipped += 1
            needs_review_reasons.append(f"invalid JSON for code {player_code!r}")
            continue
        season = _extract_season_row(body)
        if season is None:
            rows_skipped += 1
            needs_review_reasons.append(f"no season row for code {player_code!r}")
            continue
        stats_json = map_season_stats(season, bats=player.bats, position=player.position)
        mapped.append((player.id, stats_json))

    content_hash = compute_content_hash(
        {"game": game_external_id, "rows": sorted((pid, sj["OPS"]) for pid, sj in mapped)}
    )
    existing = session.execute(
        select(StatSnapshot).where(StatSnapshot.content_hash == content_hash)
    ).scalar_one_or_none()
    if existing is not None:
        return PlayerStatsNormalizeResult(
            snapshot_id=existing.id, rows_created=0, rows_skipped=rows_skipped,
            needs_review_reasons=tuple(needs_review_reasons),
        )

    snapshot = StatSnapshot(
        ingestion_run_id=ingestion_run_id, snapshot_at=datetime.now(UTC),
        content_hash=content_hash,
    )
    session.add(snapshot)
    session.flush()
    for player_id, stats_json in mapped:
        session.add(
            PlayerStatSnapshotRow(snapshot_id=snapshot.id, player_id=player_id, stats_json=stats_json)
        )
    session.flush()
    return PlayerStatsNormalizeResult(
        snapshot_id=snapshot.id, rows_created=len(mapped), rows_skipped=rows_skipped,
        needs_review_reasons=tuple(needs_review_reasons),
    )
```

> Confirm `StatSnapshot` columns (`ingestion_run_id`, `snapshot_at`, `content_hash`) and `PlayerStatSnapshotRow` columns (`snapshot_id`, `player_id`, `stats_json`) against `app/models/snapshot.py` before finalizing; adjust constructor kwargs if they differ.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/ingestion/test_player_stats_naver.py -v`
Expected: PASS (2 passed).
Also update the player_stats section of `tests/test_ingestion_normalizers.py` to the new signature/shape (it currently calls `normalize_player_stats(session, raw)` against the preview). Run: `uv run pytest tests/test_ingestion_normalizers.py -v` → PASS.

- [ ] **Step 5: Commit**

```bash
git add app/ingestion/normalizers/player_stats.py tests/ingestion/test_player_stats_naver.py tests/test_ingestion_normalizers.py
git commit -m "feat(ingestion): normalize per-player season stats into one snapshot"
```

---

## Task 5: Wire the daily pipeline + end-to-end

After the lineup snapshot is created (which upserts the 9 lineup players), fetch each lineup player's season stats and normalize them into one `StatSnapshot`. The recommender then fills 9 slots.

**Files:**
- Modify: `app/jobs/daily_pipeline.py`
- Modify: `tests/ingestion/test_daily_pipeline_naver.py`

- [ ] **Step 1: Write the failing test**

Extend `tests/ingestion/test_daily_pipeline_naver.py` so the mock HttpClient also serves the per-player season fixture for any `/player/baseball/{code}/record` URL, seed LG+WO teams, run `run_daily_pipeline(target_date=date(2025,5,14), ...)`, then assert:

```python
from app.models.snapshot import PlayerStatSnapshotRow, StatSnapshot

# after run:
assert session.query(StatSnapshot).count() == 1
assert session.query(PlayerStatSnapshotRow).count() == 9  # one per lineup batter
```

Handler sketch (add to the existing daily mock):

```python
def handler(request):
    u = str(request.url)
    if "/schedule/games?" in u:        body = SCHEDULE_JSON
    elif u.endswith("/preview"):       body = PREVIEW_JSON
    elif u.endswith("/record") and "/player/" in u:
        # any player season request -> reuse one captured season fixture
        body = PLAYER_SEASON_JSON
    elif u.endswith("/record"):        body = RECORD_JSON
    else: return httpx.Response(404, text="nf")
    return httpx.Response(200, text=body, headers={"content-type": "application/json"})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/ingestion/test_daily_pipeline_naver.py -v`
Expected: FAIL — pipeline does not yet fetch per-player season stats; `PlayerStatSnapshotRow` count is 0 (or old preview-based 2).

- [ ] **Step 3: Modify the pipeline**

In `app/jobs/daily_pipeline.py`, after `normalize_lineup(...)` produces the lineup snapshot for a game:

```python
from sqlalchemy import select
from app.ingestion.collectors.season_stats import collect_player_season_stats
from app.ingestion.normalizers.player_stats import normalize_player_stats
from app.models.snapshot import ActualLineupSnapshotRow
from app.models.player import Player

# ... inside the per-game block, after the lineup snapshot is created:
lineup_player_ids = list(
    session.execute(
        select(ActualLineupSnapshotRow.player_id).where(
            ActualLineupSnapshotRow.snapshot_id == lineup_snapshot_id
        )
    ).scalars()
)
for player_id in lineup_player_ids:
    player = session.get(Player, player_id)
    if player is None:
        continue
    collect_player_season_stats(
        session=session, ingestion_run=run, player_code=player.external_id, http=http_client
    )
stats_result = normalize_player_stats(
    session, game_external_id=game.external_id, ingestion_run_id=run.id
)
if stats_result.rows_created > 0:
    stat_snapshots_created += 1
```

Remove the old `normalize_player_stats(session, lineup_result.raw_payload)` call (the preview-based one). The production `HttpClient(min_interval=5.0)` already throttles the per-player fetches politely. Adjust `lineup_snapshot_id` to the variable the existing code uses for the lineup normalize result (`LineupNormalizeResult.snapshot_id`).

- [ ] **Step 4: Run the full suite**

Run: `uv run pytest -q`
Expected: 0 failed. The E2E test shows 1 `StatSnapshot` + 9 `PlayerStatSnapshotRow`. Fix any test that referenced the old 2-row preview stats behavior.

- [ ] **Step 5: Manual smoke (optional, live)**

```bash
KBO_DATABASE_URL="sqlite:///./kbo_lineup_lab_real.db" uv run python scripts/seed_real.py
```
Then check that `/api/games/1/pregame` returns a recommended lineup with no "Cannot fill position" error and that the recommended OPS values are real (not approximations).

- [ ] **Step 6: Commit**

```bash
git add app/jobs/daily_pipeline.py tests/ingestion/test_daily_pipeline_naver.py
git commit -m "feat(jobs): fetch per-player season stats so the recommender uses real data"
```

---

## Notes for the implementer

- **Task 1 is a gate.** Tasks 2–5 depend on the verified endpoint + field names. If Task 1 picks the KBO Official fallback instead of Naver, only `PLAYER_SEASON_URL` (Task 3), the collector's HTTP method, and `_extract_season_row`'s JSON/HTML path (Task 4) change — the mapper output and pipeline wiring stay identical.
- **Numeric OPS/OBP/SLG is mandatory.** The evaluator raises `TypeError` on string rates. The mapper's `_num` coercion is the single guard — keep it.
- **Idempotency** is already provided by `save_raw_payload` (raw) and `StatSnapshot.content_hash` (domain). The content hash is computed over the mapped (player_id, OPS) pairs so re-runs with identical stats don't create a second snapshot.
- **Politeness:** 9 per-player fetches per game at `min_interval=5.0` ≈ 45s per game. Acceptable for a daily job; the box-score/preview fetches share the same host throttle. If this becomes too slow for backfills, consider caching season payloads by (player_code, date) — out of scope for V1.
- **Out of scope for V1:** LHP/RHP splits, recent-14/30d OPS, wRC+ (no free source — see `docs/data-sources/endpoint-verification-2026-05-29.md`). The mapper leaves these absent → evaluator uses safe defaults.
