# Phase 1 — Pitcher Matchup + Clutch Enrichment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Activate the dormant `matchup` (vs-LHP/RHP, 20%) scoring component with real KBO-official splits, add opposing-starter quality as a deterministic score-calibration multiplier + LLM/display context, and surface RISP — building a reusable KBO-official ingestion path.

**Architecture:** New KBO-official collector fetches server-rendered HTML by `playerId` (== our `Player.external_id`) and stores it raw; pure parsers extract pitcher-type splits / RISP / pitcher ERA-WHIP-SO-TBF; a normalizer merges hitter splits + RISP into each `PlayerStatSnapshotRow.stats_json` (keys the model already reads); the evaluator applies a clamped opponent-quality multiplier equally to recommended and actual lineup totals (no reorder) and threads the pitcher line into the LLM prompt and pregame view.

**Tech Stack:** Python 3.13, FastAPI, SQLAlchemy, Alembic, Pydantic, BeautifulSoup4 (new), pytest, uv. Next.js 16 (one small panel addition). Run API commands from `apps/api`.

**Spec:** `docs/superpowers/specs/2026-06-01-phase1-pitcher-clutch-design.md`. Resolved decisions: opponent starter code = preview `playerInfo.pCode`; pitcher columns = ERA, WHIP, SO, TBF (K% = SO/TBF); multiplier = `clamp((ERA/4.5 + WHIP/1.45)/2, 0.9, 1.1)`.

---

## File Structure

- Create `apps/api/app/ingestion/kbo_parse.py` — pure HTML→dict parsers (no I/O): `parse_pitcher_type_splits`, `parse_hitter_risp`, `parse_pitcher_basic`.
- Create `apps/api/app/ingestion/collectors/kbo_official.py` — fetch + raw-store KBO pages (`hitter Situation`, `hitter Basic`, `pitcher Basic`).
- Create `apps/api/app/ingestion/normalizers/kbo_splits.py` — merge parsed hitter splits + RISP into stat snapshot rows.
- Create `apps/api/app/lineup_model/pitcher_quality.py` — pure `matchup_difficulty_multiplier(era, whip)`.
- Modify `apps/api/app/models/game.py` — add `opponent_starter_id`.
- Modify `apps/api/app/ingestion/normalizers/lineup.py` — capture `pCode`.
- Modify `apps/api/app/services/lineup_evaluator.py` — apply multiplier to recommended+actual totals, persist pitcher line.
- Modify `apps/api/app/lineup_model/batting_order/prompt.py` — add pitcher-quality context line.
- Modify `apps/api/app/schemas/pregame.py` + `apps/api/app/services/pregame_views.py` — `risp_avg` + opponent pitcher block.
- Modify `apps/web/components/pregame/player-comparison-panel.tsx` + `apps/web/lib/types.ts` — RISP row.
- Modify `apps/api/app/jobs/daily_pipeline.py` — collect KBO pages per roster player + run the KBO normalizer.
- Migration under `apps/api/alembic/versions/` (or the project's migration dir) for `opponent_starter_id`.

Test fixtures to capture (Task 2): `apps/api/tests/fixtures/sources/kbo/hitter_situation_66108.html`, `hitter_basic_66108.html`, `pitcher_basic_55322.html`.

---

## Task 1: Add BeautifulSoup dependency

**Files:** `apps/api/pyproject.toml`, `apps/api/uv.lock`

- [ ] **Step 1: Add the dependency**

Run: `cd apps/api && uv add beautifulsoup4`
Expected: `pyproject.toml` gains `beautifulsoup4`; `uv.lock` updates.

- [ ] **Step 2: Verify import**

Run: `cd apps/api && uv run python -c "import bs4; print(bs4.__version__)"`
Expected: prints a version (e.g. `4.x`).

- [ ] **Step 3: Commit**

```bash
git add apps/api/pyproject.toml apps/api/uv.lock
git commit -m "chore(api): add beautifulsoup4 for KBO HTML parsing"
```

---

## Task 2: Capture real KBO HTML fixtures

**Files:** Create `apps/api/tests/fixtures/sources/kbo/hitter_situation_66108.html`, `hitter_basic_66108.html`, `pitcher_basic_55322.html`

- [ ] **Step 1: Download the three pages verbatim**

Run (from repo root):
```bash
UA="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/130.0 Safari/537.36"
mkdir -p apps/api/tests/fixtures/sources/kbo
curl -s -A "$UA" "https://www.koreabaseball.com/Record/Player/HitterDetail/Situation.aspx?playerId=66108" -o apps/api/tests/fixtures/sources/kbo/hitter_situation_66108.html
curl -s -A "$UA" "https://www.koreabaseball.com/Record/Player/HitterDetail/Basic.aspx?playerId=66108" -o apps/api/tests/fixtures/sources/kbo/hitter_basic_66108.html
curl -s -A "$UA" "https://www.koreabaseball.com/Record/Player/PitcherDetail/Basic.aspx?playerId=55322" -o apps/api/tests/fixtures/sources/kbo/pitcher_basic_55322.html
```

- [ ] **Step 2: Sanity-check the fixtures contain the target tables**

Run:
```bash
grep -c "투수유형별" apps/api/tests/fixtures/sources/kbo/hitter_situation_66108.html
grep -c "좌투수" apps/api/tests/fixtures/sources/kbo/hitter_situation_66108.html
grep -c "WHIP" apps/api/tests/fixtures/sources/kbo/pitcher_basic_55322.html
```
Expected: each ≥ 1. If a page changed and a marker is missing, STOP — the parser tasks depend on these.

- [ ] **Step 3: Commit**

```bash
git add apps/api/tests/fixtures/sources/kbo/
git commit -m "test(kbo): capture KBO official HTML fixtures (hitter splits/basic, pitcher basic)"
```

---

## Task 3: Pure parser — hitter pitcher-type (L/R) splits

**Files:** Create `apps/api/app/ingestion/kbo_parse.py`; Test `apps/api/tests/ingestion/test_kbo_parse.py`

- [ ] **Step 1: Write the failing test**

```python
# apps/api/tests/ingestion/test_kbo_parse.py
from pathlib import Path

from app.ingestion.kbo_parse import parse_pitcher_type_splits

_FIX = Path(__file__).parent.parent / "fixtures" / "sources" / "kbo"


def test_parse_pitcher_type_splits_holds_lhp_rhp() -> None:
    """Extract vs-LHP/RHP counting lines from the 투수유형별 table; fold 언더투수 into RHP."""
    html = (_FIX / "hitter_situation_66108.html").read_text(encoding="utf-8")
    out = parse_pitcher_type_splits(html)
    # 홍창기 verified 2026: 좌투수 AB54 H15 2B2 HR0 BB10 HBP2 ; 우투수 AB109 H23 2B3 3B1 HR0 BB29 HBP3
    assert out["lhp"]["ab"] == 54
    assert out["lhp"]["h"] == 15
    assert out["lhp"]["bb"] == 10
    assert out["lhp"]["hbp"] == 2
    assert out["rhp"]["ab"] >= 109  # >= because 언더투수 is folded into RHP
    assert out["rhp"]["bb"] >= 29


def test_parse_pitcher_type_splits_missing_table_returns_none() -> None:
    assert parse_pitcher_type_splits("<html><body>no table</body></html>") is None
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd apps/api && uv run pytest tests/ingestion/test_kbo_parse.py -k pitcher_type -v`
Expected: FAIL — module/function not found.

- [ ] **Step 3: Implement**

```python
# apps/api/app/ingestion/kbo_parse.py
"""Pure parsers for KBO official server-rendered HTML record pages.

No I/O. Each parser locates a labelled table and returns plain dicts, or None
when the expected structure is absent (caller records a needs-review reason).
"""

from __future__ import annotations

from bs4 import BeautifulSoup

# Column order of the 투수유형별 table rows (after the leading label cell):
# AVG, AB, H, 2B, 3B, HR, RBI, BB, HBP, SO, GDP
_SPLIT_COLS = ("avg", "ab", "h", "2b", "3b", "hr", "rbi", "bb", "hbp", "so", "gdp")


def _to_int(text: str) -> int:
    text = (text or "").strip().replace(",", "")
    return int(text) if text.lstrip("-").isdigit() else 0


def _row_cells_by_label(soup: BeautifulSoup, label: str) -> list[str] | None:
    """Return the text of all <td> in the first row whose first cell == label."""
    for tr in soup.find_all("tr"):
        cells = tr.find_all(["td", "th"])
        if cells and cells[0].get_text(strip=True) == label:
            return [c.get_text(strip=True) for c in cells]
    return None


def parse_pitcher_type_splits(html: str) -> dict[str, dict[str, int]] | None:
    """Return {'lhp': {...counts}, 'rhp': {...counts}} from 투수유형별.

    Rows are labelled 좌투수 / 우투수 / 언더투수; 언더투수 is folded into rhp.
    Returns None if neither 좌투수 nor 우투수 rows are present.
    """
    if "투수유형별" not in html:
        return None
    soup = BeautifulSoup(html, "html.parser")

    def counts(label: str) -> dict[str, int] | None:
        cells = _row_cells_by_label(soup, label)
        if cells is None:
            return None
        vals = cells[1 : 1 + len(_SPLIT_COLS)]
        return {col: _to_int(v) for col, v in zip(_SPLIT_COLS, vals, strict=False)}

    lhp = counts("좌투수")
    rhp = counts("우투수")
    if lhp is None and rhp is None:
        return None
    lhp = lhp or {c: 0 for c in _SPLIT_COLS}
    rhp = rhp or {c: 0 for c in _SPLIT_COLS}
    under = counts("언더투수")
    if under is not None:
        for c in _SPLIT_COLS:
            if c != "avg":
                rhp[c] += under[c]
    return {"lhp": lhp, "rhp": rhp}
```

- [ ] **Step 4: Run to verify pass**

Run: `cd apps/api && uv run pytest tests/ingestion/test_kbo_parse.py -k pitcher_type -v`
Expected: PASS. If the column count/order differs from `_SPLIT_COLS`, adjust `_SPLIT_COLS` to match the captured fixture's header row (verify by reading the fixture's 투수유형별 header).

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/ingestion/kbo_parse.py apps/api/tests/ingestion/test_kbo_parse.py
git commit -m "feat(ingestion): parse KBO hitter L/R pitcher-type splits"
```

---

## Task 4: Pure parsers — RISP and pitcher basics

**Files:** `apps/api/app/ingestion/kbo_parse.py`; `apps/api/tests/ingestion/test_kbo_parse.py`

- [ ] **Step 1: Write failing tests**

```python
# append to apps/api/tests/ingestion/test_kbo_parse.py
from app.ingestion.kbo_parse import parse_hitter_risp, parse_pitcher_basic


def test_parse_hitter_risp_returns_float() -> None:
    html = (_FIX / "hitter_basic_66108.html").read_text(encoding="utf-8")
    risp = parse_hitter_risp(html)
    assert risp is not None and 0.0 <= risp <= 1.0


def test_parse_pitcher_basic_extracts_era_whip_so_tbf() -> None:
    html = (_FIX / "pitcher_basic_55322.html").read_text(encoding="utf-8")
    out = parse_pitcher_basic(html)
    assert out is not None
    assert out["era"] > 0
    assert out["whip"] > 0
    assert out["so"] >= 0
    assert out["tbf"] > 0


def test_parse_pitcher_basic_missing_returns_none() -> None:
    assert parse_pitcher_basic("<html></html>") is None
```

- [ ] **Step 2: Run to verify fail**

Run: `cd apps/api && uv run pytest tests/ingestion/test_kbo_parse.py -k "risp or pitcher_basic" -v`
Expected: FAIL — functions not found.

- [ ] **Step 3: Implement (append to `kbo_parse.py`)**

```python
def _to_float(text: str) -> float | None:
    text = (text or "").strip().replace(",", "")
    try:
        return float(text)
    except ValueError:
        return None


def parse_hitter_risp(html: str) -> float | None:
    """Return the season RISP (득점권타율) batting average, else None.

    The HitterDetail/Basic season table includes a RISP column. We read the
    season row's RISP cell by header position.
    """
    soup = BeautifulSoup(html, "html.parser")
    return _value_by_header(soup, "RISP", as_float=True)


def parse_pitcher_basic(html: str) -> dict[str, float] | None:
    """Return {'era','whip','so','tbf'} from PitcherDetail/Basic season row, else None."""
    soup = BeautifulSoup(html, "html.parser")
    era = _value_by_header(soup, "ERA", as_float=True)
    whip = _value_by_header(soup, "WHIP", as_float=True)
    so = _value_by_header(soup, "SO", as_float=True)
    tbf = _value_by_header(soup, "TBF", as_float=True)
    if era is None or whip is None or tbf in (None, 0):
        return None
    return {"era": era, "whip": whip, "so": so or 0.0, "tbf": tbf}


def _value_by_header(soup: BeautifulSoup, header: str, *, as_float: bool):
    """Find the first table whose header row contains `header`, return the
    matching cell from the first data row. Returns float|None when as_float."""
    for table in soup.find_all("table"):
        headers = [th.get_text(strip=True) for th in table.find_all("th")]
        if header not in headers:
            continue
        idx = headers.index(header)
        body_rows = table.find_all("tr")
        for tr in body_rows:
            tds = tr.find_all("td")
            if len(tds) > idx:
                return _to_float(tds[idx].get_text(strip=True)) if as_float else tds[idx].get_text(strip=True)
    return None
```

> NOTE: KBO record tables sometimes split columns across two stacked `<table>`s (frozen first columns + scrollable rest); if `_value_by_header` misses a header that you can see in the fixture, the header lives in the second table — `_value_by_header` already iterates all `<table>`s, but confirm the data row alignment by reading the fixture and adjust `idx` handling (e.g. account for a leading rank/name column) if a test value is off.

- [ ] **Step 4: Run to verify pass**

Run: `cd apps/api && uv run pytest tests/ingestion/test_kbo_parse.py -v`
Expected: PASS (all parser tests). Adjust header alignment against the fixtures if any numeric assertion fails.

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/ingestion/kbo_parse.py apps/api/tests/ingestion/test_kbo_parse.py
git commit -m "feat(ingestion): parse KBO RISP and pitcher ERA/WHIP/SO/TBF"
```

---

## Task 5: Split-metric computation (PA/OBP/SLG/OPS) + K%

**Files:** `apps/api/app/ingestion/kbo_parse.py`; `apps/api/tests/ingestion/test_kbo_parse.py`

- [ ] **Step 1: Write failing test**

```python
# append to test_kbo_parse.py
from app.ingestion.kbo_parse import split_rate_stats


def test_split_rate_stats_computes_ops_pa() -> None:
    # 좌투수 line: AB54 H15 2B2 3B0 HR0 BB10 HBP2
    line = {"ab": 54, "h": 15, "2b": 2, "3b": 0, "hr": 0, "bb": 10, "hbp": 2}
    s = split_rate_stats(line)
    assert s["pa"] == 66  # 54 + 10 + 2
    # OBP = (15+10+2)/(54+10+2) = 27/66 ; SLG = (13*1 + 2*2)/54 = 17/54 ; singles=15-2=13
    assert abs(s["obp"] - 27 / 66) < 1e-9
    assert abs(s["slg"] - 17 / 54) < 1e-9
    assert abs(s["ops"] - (27 / 66 + 17 / 54)) < 1e-9


def test_split_rate_stats_zero_pa_is_none() -> None:
    assert split_rate_stats({"ab": 0, "h": 0, "2b": 0, "3b": 0, "hr": 0, "bb": 0, "hbp": 0}) is None
```

- [ ] **Step 2: Run to verify fail**

Run: `cd apps/api && uv run pytest tests/ingestion/test_kbo_parse.py -k split_rate -v`
Expected: FAIL.

- [ ] **Step 3: Implement (append to `kbo_parse.py`)**

```python
def split_rate_stats(line: dict[str, int]) -> dict[str, float | int] | None:
    """Compute PA/OBP/SLG/OPS from a counting line (no SF available in this table).

    OBP = (H + BB + HBP) / (AB + BB + HBP) ; SLG = TB / AB ;
    TB = singles + 2*2B + 3*3B + 4*HR ; singles = max(0, H - 2B - 3B - HR).
    Returns None when there are no plate appearances.
    """
    ab, h = line["ab"], line["h"]
    bb, hbp = line["bb"], line["hbp"]
    pa = ab + bb + hbp
    if pa == 0 or ab == 0:
        return None
    singles = max(0, h - line["2b"] - line["3b"] - line["hr"])
    tb = singles + 2 * line["2b"] + 3 * line["3b"] + 4 * line["hr"]
    obp = (h + bb + hbp) / pa
    slg = tb / ab
    return {"pa": pa, "obp": obp, "slg": slg, "ops": obp + slg}
```

- [ ] **Step 4: Run to verify pass**

Run: `cd apps/api && uv run pytest tests/ingestion/test_kbo_parse.py -k split_rate -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/ingestion/kbo_parse.py apps/api/tests/ingestion/test_kbo_parse.py
git commit -m "feat(ingestion): compute vs-hand OPS/OBP/SLG/PA from KBO split lines"
```

---

## Task 6: KBO-official collector (fetch + raw store)

**Files:** Create `apps/api/app/ingestion/collectors/kbo_official.py`; Test `apps/api/tests/ingestion/test_kbo_official_collector.py`

Study `apps/api/app/ingestion/collectors/season_stats.py` for the exact `collect_*` shape (HttpClient.fetch, `RawPayloadCreate`, `save_raw_payload`, return `(payload, created)`).

- [ ] **Step 1: Write the failing test (inject a mock HttpClient)**

```python
# apps/api/tests/ingestion/test_kbo_official_collector.py
from app.ingestion.collectors.kbo_official import (
    KBO_SOURCE_NAME,
    build_kbo_hitter_situation_url,
    collect_kbo_hitter_situation,
)


def test_build_kbo_hitter_situation_url() -> None:
    url = build_kbo_hitter_situation_url(player_code="66108")
    assert url.endswith("/HitterDetail/Situation.aspx?playerId=66108")
    assert "koreabaseball.com" in url


def test_collect_kbo_hitter_situation_stores_raw(session, ingestion_run, fake_http):
    """fake_http returns canned HTML; collector stores it as a kbo_official PLAYER_STATS payload."""
    fake_http.set_response("<html>투수유형별</html>")
    payload, created = collect_kbo_hitter_situation(
        session=session, ingestion_run=ingestion_run, player_code="66108", http=fake_http
    )
    assert created is True
    assert payload.source_name == KBO_SOURCE_NAME
    assert "투수유형별" in payload.raw_body
```

> Reuse the existing fixtures/fakes used by `tests/ingestion/test_season_stats_collector.py` for `session`, `ingestion_run`, and a fake/mock `HttpClient` (match that file's arrange pattern exactly; add a conftest fixture only if one does not already exist).

- [ ] **Step 2: Run to verify fail**

Run: `cd apps/api && uv run pytest tests/ingestion/test_kbo_official_collector.py -v`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement**

```python
# apps/api/app/ingestion/collectors/kbo_official.py
"""Collectors for KBO official (koreabaseball.com) server-rendered record pages.

Player ids are KBO playerIds, which equal our Player.external_id. Pages are
GET + server-rendered HTML; stored raw for replay through the shared raw store.
"""

from __future__ import annotations

from typing import Final

from sqlalchemy.orm import Session

from app.ingestion.http_client import HttpClient
from app.ingestion.raw_store import save_raw_payload
from app.ingestion.types import PayloadCategory
from app.models.snapshot import IngestionRun, RawIngestionPayload
from app.schemas.ingestion import RawPayloadCreate

__all__ = [
    "KBO_SOURCE_NAME",
    "KBO_USER_AGENT",
    "build_kbo_hitter_situation_url",
    "build_kbo_hitter_basic_url",
    "build_kbo_pitcher_basic_url",
    "collect_kbo_hitter_situation",
    "collect_kbo_hitter_basic",
    "collect_kbo_pitcher_basic",
]

KBO_SOURCE_NAME: Final = "kbo_official"
KBO_USER_AGENT: Final = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"
)
_BASE: Final = "https://www.koreabaseball.com/Record/Player"


def build_kbo_hitter_situation_url(*, player_code: str) -> str:
    return f"{_BASE}/HitterDetail/Situation.aspx?playerId={player_code}"


def build_kbo_hitter_basic_url(*, player_code: str) -> str:
    return f"{_BASE}/HitterDetail/Basic.aspx?playerId={player_code}"


def build_kbo_pitcher_basic_url(*, player_code: str) -> str:
    return f"{_BASE}/PitcherDetail/Basic.aspx?playerId={player_code}"


def _collect(
    session: Session, ingestion_run: IngestionRun, url: str, http: HttpClient
) -> tuple[RawIngestionPayload, bool]:
    result = http.fetch(url, headers={"User-Agent": KBO_USER_AGENT})
    payload = RawPayloadCreate(
        ingestion_run_id=ingestion_run.id,
        category=PayloadCategory.PLAYER_STATS,
        source_name=KBO_SOURCE_NAME,
        source_url=result.url,
        fetched_at=result.fetched_at,
        content_type=result.content_type,
        raw_body=result.body,
    )
    return save_raw_payload(session, payload)


def collect_kbo_hitter_situation(
    *, session: Session, ingestion_run: IngestionRun, player_code: str, http: HttpClient
) -> tuple[RawIngestionPayload, bool]:
    return _collect(session, ingestion_run, build_kbo_hitter_situation_url(player_code=player_code), http)


def collect_kbo_hitter_basic(
    *, session: Session, ingestion_run: IngestionRun, player_code: str, http: HttpClient
) -> tuple[RawIngestionPayload, bool]:
    return _collect(session, ingestion_run, build_kbo_hitter_basic_url(player_code=player_code), http)


def collect_kbo_pitcher_basic(
    *, session: Session, ingestion_run: IngestionRun, player_code: str, http: HttpClient
) -> tuple[RawIngestionPayload, bool]:
    return _collect(session, ingestion_run, build_kbo_pitcher_basic_url(player_code=player_code), http)
```

> Verify `HttpClient.fetch` accepts a `headers=` kwarg (the Naver collectors pass `headers={"Referer": ...}`), and that `RawPayloadCreate` field names match `season_stats.py`. Adjust to the real signatures.

- [ ] **Step 4: Run to verify pass**

Run: `cd apps/api && uv run pytest tests/ingestion/test_kbo_official_collector.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/ingestion/collectors/kbo_official.py apps/api/tests/ingestion/test_kbo_official_collector.py
git commit -m "feat(ingestion): KBO official collector (hitter situation/basic, pitcher basic)"
```

---

## Task 7: Normalizer — merge hitter splits + RISP into stats_json

**Files:** Create `apps/api/app/ingestion/normalizers/kbo_splits.py`; Test `apps/api/tests/ingestion/test_kbo_splits_normalizer.py`

This reads the run's `kbo_official` PLAYER_STATS payloads, parses each, and updates the matching `PlayerStatSnapshotRow.stats_json` (reassigning the dict so SQLAlchemy detects the change — same pattern as `lineup_evaluator._persist_start_rhythm`). It maps the KBO `playerId` from `source_url` back to a `Player` via `external_id`.

- [ ] **Step 1: Write the failing test**

```python
# apps/api/tests/ingestion/test_kbo_splits_normalizer.py
from app.ingestion.normalizers.kbo_splits import merge_hitter_splits_into_stats

def test_merge_hitter_splits_sets_vs_hand_and_risp() -> None:
    stats_json = {"OPS": 0.8, "OBP": 0.35, "SLG": 0.45}
    situation_html = open(
        "tests/fixtures/sources/kbo/hitter_situation_66108.html", encoding="utf-8"
    ).read()
    basic_html = open(
        "tests/fixtures/sources/kbo/hitter_basic_66108.html", encoding="utf-8"
    ).read()
    merged = merge_hitter_splits_into_stats(stats_json, situation_html, basic_html)
    assert merged["vs_lhp_pa"] == 66
    assert merged["vs_rhp_pa"] >= 141
    assert merged["vs_lhp_ops"] > 0
    assert "risp_avg" in merged
    assert merged["OPS"] == 0.8  # preserves existing keys


def test_merge_hitter_splits_no_data_is_noop() -> None:
    stats_json = {"OPS": 0.8}
    merged = merge_hitter_splits_into_stats(stats_json, "<html></html>", "<html></html>")
    assert "vs_lhp_ops" not in merged
    assert merged == {"OPS": 0.8}
```

- [ ] **Step 2: Run to verify fail**

Run: `cd apps/api && uv run pytest tests/ingestion/test_kbo_splits_normalizer.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement the pure merge helper + the DB normalizer**

```python
# apps/api/app/ingestion/normalizers/kbo_splits.py
"""Merge KBO official L/R splits + RISP into player stat snapshot rows."""

from __future__ import annotations

from app.ingestion.kbo_parse import (
    parse_hitter_risp,
    parse_pitcher_type_splits,
    split_rate_stats,
)


def merge_hitter_splits_into_stats(
    stats_json: dict, situation_html: str, basic_html: str
) -> dict:
    """Return a new stats_json with vs_lhp/rhp_ops·pa and risp_avg added when
    derivable. No-op (returns an equal dict) when the source HTML lacks them.
    Existing keys are preserved."""
    out = dict(stats_json)
    splits = parse_pitcher_type_splits(situation_html)
    if splits is not None:
        lhp = split_rate_stats(splits["lhp"])
        rhp = split_rate_stats(splits["rhp"])
        if lhp is not None:
            out["vs_lhp_ops"] = lhp["ops"]
            out["vs_lhp_pa"] = lhp["pa"]
        if rhp is not None:
            out["vs_rhp_ops"] = rhp["ops"]
            out["vs_rhp_pa"] = rhp["pa"]
    risp = parse_hitter_risp(basic_html)
    if risp is not None:
        out["risp_avg"] = risp
    return out
```

Add a DB-facing `normalize_kbo_hitter_splits(session, *, ingestion_run_id, snapshot_id)` that: selects `kbo_official` PLAYER_STATS payloads for the run; groups them per player by the `playerId` in `source_url` (regex `playerId=(\d+)`) and by page kind (`Situation` vs `HitterDetail/Basic` via the URL); resolves the `Player` by `external_id`; for each player with both pages, updates that player's `PlayerStatSnapshotRow.stats_json` (where `snapshot_id == snapshot_id`) via `row.stats_json = merge_hitter_splits_into_stats(...)`; records `needs_review` reasons for players whose pages are missing or unparseable. Mirror the structure and idempotency notes of `normalize_player_stats`.

- [ ] **Step 4: Run to verify pass**

Run: `cd apps/api && uv run pytest tests/ingestion/test_kbo_splits_normalizer.py -v`
Expected: PASS. Add a DB-backed test for `normalize_kbo_hitter_splits` mirroring the seeding pattern in `tests/ingestion/test_player_stats_naver.py` (seed a snapshot row + two raw kbo payloads → assert the row's stats_json gains `vs_lhp_ops`).

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/ingestion/normalizers/kbo_splits.py apps/api/tests/ingestion/test_kbo_splits_normalizer.py
git commit -m "feat(ingestion): merge KBO L/R splits + RISP into stat snapshots"
```

---

## Task 8: Capture opponent_starter_id

**Files:** `apps/api/app/models/game.py`; new Alembic migration; `apps/api/app/ingestion/normalizers/lineup.py`; Test `apps/api/tests/ingestion/test_lineup_naver.py`

- [ ] **Step 1: Write the failing test**

```python
# append to apps/api/tests/ingestion/test_lineup_naver.py
def test_opponent_starter_id_captured_from_preview(...):
    """After normalize_lineup, Game.opponent_starter_id == the opposing starter's pCode."""
    # arrange a preview where the opponent starter playerInfo.pCode == "55322"
    # (reuse this file's existing normalize_lineup setup)
    ...
    assert game.opponent_starter_id == "55322"
```

> Fill the arrange block by copying the existing opponent-starter test in this file (there is already coverage for `opponent_starter_throws`); set `playerInfo.pCode` in the fixture preview and assert the new column.

- [ ] **Step 2: Run to verify fail**

Run: `cd apps/api && uv run pytest tests/ingestion/test_lineup_naver.py -k opponent_starter_id -v`
Expected: FAIL — `Game` has no `opponent_starter_id`.

- [ ] **Step 3: Add the column + migration + capture**

In `apps/api/app/models/game.py`, after `opponent_starter_name`:
```python
    opponent_starter_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
```
Create an Alembic migration (use the project's command — check `apps/api` for `alembic.ini`; run `uv run alembic revision -m "add opponent_starter_id to games"` then add `op.add_column("games", sa.Column("opponent_starter_id", sa.String(length=32), nullable=True))` and the `drop_column` downgrade).
In `apps/api/app/ingestion/normalizers/lineup.py::_apply_opponent_starter`, extract the code:
```python
    info = starter.get("playerInfo")
    if isinstance(info, dict):
        pcode = info.get("pCode")
        if pcode is not None:
            game.opponent_starter_id = str(pcode)
```

- [ ] **Step 4: Run to verify pass**

Run: `cd apps/api && uv run pytest tests/ingestion/test_lineup_naver.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/models/game.py apps/api/app/ingestion/normalizers/lineup.py apps/api/alembic/ apps/api/tests/ingestion/test_lineup_naver.py
git commit -m "feat(ingestion): capture opponent starter id (pCode) from preview"
```

---

## Task 9: Opponent-quality multiplier (pure)

**Files:** Create `apps/api/app/lineup_model/pitcher_quality.py`; Test `apps/api/tests/test_pitcher_quality.py`

- [ ] **Step 1: Write the failing test**

```python
# apps/api/tests/test_pitcher_quality.py
from app.lineup_model.pitcher_quality import matchup_difficulty_multiplier


def test_ace_suppresses_toward_floor() -> None:
    # ERA 2.5, WHIP 1.10 → (2.5/4.5 + 1.10/1.45)/2 ≈ 0.66 → clamp 0.9
    assert matchup_difficulty_multiplier(era=2.5, whip=1.10) == 0.9


def test_weak_pitcher_lifts_toward_ceiling() -> None:
    # ERA 6.0, WHIP 1.70 → (1.33 + 1.17)/2 = 1.25 → clamp 1.1
    assert matchup_difficulty_multiplier(era=6.0, whip=1.70) == 1.1


def test_average_pitcher_is_near_one() -> None:
    m = matchup_difficulty_multiplier(era=4.5, whip=1.45)
    assert abs(m - 1.0) < 1e-9


def test_missing_inputs_default_to_one() -> None:
    assert matchup_difficulty_multiplier(era=None, whip=None) == 1.0
```

- [ ] **Step 2: Run to verify fail**

Run: `cd apps/api && uv run pytest tests/test_pitcher_quality.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement**

```python
# apps/api/app/lineup_model/pitcher_quality.py
"""Opponent-starter quality → lineup score-calibration multiplier (pure).

Applied EQUALLY to recommended and actual lineup totals, so it calibrates the
score magnitude for matchup difficulty without changing player selection or
batting order. League baselines are approximate KBO run-environment constants;
revisit per season.
"""

from __future__ import annotations

# Approximate KBO league baselines (revisit per season).
_LEAGUE_ERA = 4.5
_LEAGUE_WHIP = 1.45
_MULT_MIN = 0.9
_MULT_MAX = 1.1


def matchup_difficulty_multiplier(*, era: float | None, whip: float | None) -> float:
    """Lower ERA/WHIP (tougher starter) → multiplier toward 0.9; weaker → 1.1.

    Returns 1.0 when either input is missing (graceful no-op).
    """
    if era is None or whip is None or era <= 0 or whip <= 0:
        return 1.0
    raw = (era / _LEAGUE_ERA + whip / _LEAGUE_WHIP) / 2
    return min(_MULT_MAX, max(_MULT_MIN, raw))
```

- [ ] **Step 4: Run to verify pass**

Run: `cd apps/api && uv run pytest tests/test_pitcher_quality.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/lineup_model/pitcher_quality.py apps/api/tests/test_pitcher_quality.py
git commit -m "feat(lineup): opponent-starter quality calibration multiplier"
```

---

## Task 10: Apply multiplier in the evaluator + persist pitcher line

**Files:** `apps/api/app/services/lineup_evaluator.py`; Test `apps/api/tests/test_lineup_evaluator_history.py` (or a new `test_pitcher_quality_eval.py`)

- [ ] **Step 1: Write the failing test**

Seed an eval run whose `Game.opponent_starter_id` resolves to a kbo_official pitcher payload (or inject parsed era/whip), and assert: (a) both `recommended_total_score` and `actual_total_score` in `key_insights_json` are scaled by the same multiplier vs. the no-pitcher baseline; (b) the relative ordering of recommended slots is unchanged; (c) `key_insights_json["opponent_pitcher"]` carries era/whip/k_pct/multiplier.

```python
def test_pitcher_multiplier_scales_both_totals_equally(...):
    base_rec, base_act = _scores_without_pitcher(...)
    rec, act = _scores_with_pitcher(era=2.5, whip=1.10, ...)  # mult 0.9
    assert abs(rec - base_rec * 0.9) < 1e-6
    assert abs(act - base_act * 0.9) < 1e-6
```

- [ ] **Step 2: Run to verify fail** — `uv run pytest -k pitcher_multiplier -v` → FAIL.

- [ ] **Step 3: Implement**

In `evaluate_lineup_for_run`: after computing `recommended` and `actual_total_score`, resolve the opponent pitcher quality: load the run's `kbo_official` pitcher payload for `game.opponent_starter_id` (parse with `parse_pitcher_basic`), compute `mult = matchup_difficulty_multiplier(era=..., whip=...)`, multiply both `recommended.total_score`-derived value stored in `key_insights` and `actual_total_score` by `mult`, and add `key_insights["opponent_pitcher"] = {"era":..., "whip":..., "k_pct": so/tbf, "multiplier": mult}`. Keep the stored per-slot scores unmultiplied (ranking unaffected); apply the multiplier only to the two headline totals used for comparison. When no pitcher data, `mult` is 1.0 (no change).

- [ ] **Step 4: Run to verify pass** — `uv run pytest tests/test_pitcher_quality_eval.py -v` → PASS; plus `uv run pytest tests/test_recommendation.py tests/test_pregame_api.py -v` no regression.

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/services/lineup_evaluator.py apps/api/tests/test_pitcher_quality_eval.py
git commit -m "feat(lineup): calibrate lineup totals by opponent-starter quality"
```

---

## Task 11: Pitcher-quality LLM context line

**Files:** `apps/api/app/lineup_model/batting_order/prompt.py`; Test `apps/api/tests/test_batting_order.py`

- [ ] **Step 1: Write the failing test** — assert `build_user_prompt(..., opp_pitcher={"era":3.18,"whip":1.59,"k_pct":0.21})` includes an ERA/WHIP line; and that omitting `opp_pitcher` leaves the prompt unchanged (backward compatible).

- [ ] **Step 2: Run to verify fail.**

- [ ] **Step 3: Implement** — add an optional `opp_pitcher: dict | None = None` param to `build_user_prompt`; when present, prepend a Korean line e.g. `f"상대 선발 투수: ERA {era:.2f}, WHIP {whip:.2f}, K% {k_pct:.0%} (참고용 매치업 난이도)"`. Thread it from the orderer/evaluator call site (only when LLM provider active).

- [ ] **Step 4: Run to verify pass.**

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/lineup_model/batting_order/prompt.py apps/api/tests/test_batting_order.py
git commit -m "feat(lineup): add opponent-starter quality to the batting-order prompt"
```

---

## Task 12: Surface RISP + opponent pitcher in pregame view & web

**Files:** `apps/api/app/schemas/pregame.py`, `apps/api/app/services/pregame_views.py`, `apps/web/lib/types.ts`, `apps/web/components/pregame/player-comparison-panel.tsx`; Tests `apps/api/tests/test_pregame_api.py`, web lint/build

- [ ] **Step 1: Write the failing API test** — `players/compare` response includes `risp_avg` for each side; `pregame` response includes an `opponent_pitcher` block when present.

- [ ] **Step 2: Run to verify fail.**

- [ ] **Step 3: Implement**
  - `PlayerComparisonStats`: add `risp_avg: float | None`. `pregame_views._build_comparison_stats`: `risp_avg=_opt_f("risp_avg")`.
  - `PregameResponse`: add optional `opponent_pitcher` (era/whip/k_pct/multiplier) read from `LineupEvaluationSummary.key_insights_json["opponent_pitcher"]`.
  - Web: add `rispAvg`/`oppPitcher` to `lib/types.ts`; add a `최근 득점권 타율 (RISP)` row to `player-comparison-panel.tsx` mirroring the existing `최근 14일 OPS` row; render an opponent-pitcher line in the pregame header.

- [ ] **Step 4: Verify** — `uv run pytest tests/test_pregame_api.py -v`; web checks via the `web-ui-reviewer` path (`npm run lint`, `npm run build` in `apps/web`).

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/schemas/pregame.py apps/api/app/services/pregame_views.py apps/web/lib/types.ts apps/web/components/pregame/player-comparison-panel.tsx apps/api/tests/test_pregame_api.py
git commit -m "feat(pregame): surface RISP and opponent-starter quality"
```

---

## Task 13: Pipeline wiring

**Files:** `apps/api/app/jobs/daily_pipeline.py`; Test `apps/api/tests/test_pipeline_jobs.py`

- [ ] **Step 1: Write the failing test** — with mocked HttpClient returning the KBO fixtures, running the daily pipeline for a game results in a stat snapshot row carrying `vs_lhp_ops`/`risp_avg`, and the eval `key_insights_json` carrying `opponent_pitcher`.

- [ ] **Step 2: Run to verify fail.**

- [ ] **Step 3: Implement** — in `daily_pipeline`: in/after `_collect_roster_player_season_stats`, for each rostered hitter also call `collect_kbo_hitter_situation` + `collect_kbo_hitter_basic`; collect `collect_kbo_pitcher_basic(opponent_starter_id)` once; after `normalize_player_stats`, call `normalize_kbo_hitter_splits(...)`. Guard all KBO fetches in try/except → log + needs-review on failure so a KBO outage never fails the pipeline.

- [ ] **Step 4: Run to verify pass** — `uv run pytest tests/test_pipeline_jobs.py -v`.

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/jobs/daily_pipeline.py apps/api/tests/test_pipeline_jobs.py
git commit -m "feat(pipeline): collect KBO splits/pitcher and normalize into evaluation"
```

---

## Task 14: End-to-end verification & harness

- [ ] **Step 1:** `cd apps/api && uv run pytest` → all pass.
- [ ] **Step 2:** `pre-commit run --all-files` → clean (incl. new dep, mypy on new modules).
- [ ] **Step 3:** Web: `cd apps/web && npm run lint && npm run build` → clean.
- [ ] **Step 4:** Update `CLAUDE.md` architecture-invariant wording if it still implies matchup is unused; document the KBO-official source. Commit.
- [ ] **Step 5:** Live check against Supabase (delete+re-ingest 2026-05-31 as in prior phases, LLM on): confirm `players/compare` returns populated `vs_*_ops`, `risp_avg`, and the pregame `opponent_pitcher` block; spot-check that a strong-platoon hitter's placement shifts vs. season-only.
- [ ] **Step 6:** `/harness-audit` (structural + semantic) green; record marker before PR.

---

## Self-Review notes

- **Spec coverage:** platoon→Tasks 3,5,7; RISP→Tasks 4,7,12; pitcher quality→Tasks 4,8,9,10,11,12; shared infra→Tasks 1,2,6,7; wiring→13; verify/harness→14. All spec sections covered.
- **Type consistency:** stats_json keys `vs_lhp_ops/vs_lhp_pa/vs_rhp_ops/vs_rhp_pa/risp_avg` consistent across parser→normalizer→evaluator→schema. `matchup_difficulty_multiplier(*, era, whip)` signature consistent in Tasks 9/10. `opponent_starter_id` consistent in Task 8/10/13.
- **Open items pushed to execution (need a quick verify against captured fixtures, not placeholders):** exact column index alignment in `_value_by_header`/`_SPLIT_COLS` (verify against the Task 2 fixtures — KBO stacks frozen+scroll tables); `HttpClient.fetch(headers=...)` and `RawPayloadCreate` field names (mirror `season_stats.py`); Alembic command/dir specifics.
- **Graceful degradation:** every KBO dependency is optional — missing/failed → fields omitted, multiplier 1.0, needs-review reason; no pipeline failure and byte-identical behavior without KBO data.
