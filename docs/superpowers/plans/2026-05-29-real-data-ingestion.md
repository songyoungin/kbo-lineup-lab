# Real Data Ingestion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the placeholder, unverified-URL collectors/normalizers with verified Naver `api-gw` (primary) and KBO Official `/ws/` (backup) endpoints so the pipeline ingests real LG Twins data.

**Architecture:** Keep the existing `collector → raw_store → normalizer → snapshot` flow. Collectors fetch raw JSON and persist it verbatim via `save_raw_payload`; normalizers parse the real source shapes into domain rows. Shared `HttpClient` gains POST + per-host rate limiting + per-call headers; a new `game_id` module handles the `YYYYMMDD{away}{home}{seq}` format and KBO↔Naver conversion.

**Tech Stack:** Python 3.13, FastAPI, SQLAlchemy 2.x, httpx, pydantic v2, pytest, uv. Run all commands from `apps/api` with `uv run`.

**Source reference:** [`docs/data-sources/endpoint-verification-2026-05-29.md`](../../data-sources/endpoint-verification-2026-05-29.md). **Design:** [`docs/superpowers/specs/2026-05-29-real-data-ingestion-design.md`](../specs/2026-05-29-real-data-ingestion-design.md).

---

## File Structure

| File | Responsibility | Task |
|------|----------------|------|
| `app/ingestion/http_client.py` | add `post()`, per-host rate limiter, per-call `headers` | 1 |
| `app/ingestion/game_id.py` (new) | parse/format game_id, KBO↔Naver conversion, team codes | 2 |
| `app/ingestion/collectors/schedule.py` | fetch Naver schedule (KBO POST backup) | 3 |
| `app/ingestion/normalizers/schedule.py` | parse Naver `result.games[]` → Game rows | 3 |
| `app/ingestion/collectors/lineup.py` | fetch Naver `/preview` | 4 |
| `app/ingestion/normalizers/lineup.py` | parse `fullLineUp` → lineup snapshot; handedness → Player | 4 |
| `app/ingestion/collectors/player_stats.py` | fetch Naver season stats from `/preview` | 5 |
| `app/ingestion/normalizers/player_stats.py` | parse `currentSeasonStats` → stat snapshot | 5 |
| `app/ingestion/collectors/box_score.py` | fetch Naver `/record` | 6 |
| `app/ingestion/normalizers/box_score.py` | parse `battersBoxscore`/`pitchersBoxscore` → box rows | 6 |
| `app/jobs/daily_pipeline.py` | wire schedule→lineup→stats→box per LG game | 7 |
| `tests/ingestion/conftest.py` (new) | shared `mock_http` + `session` fixtures + captured JSON | 1 |
| `tests/fixtures/sources/naver/*.json` (new) | captured real responses for normalizer tests | 3–6 |

**Capture step (do once, before Task 3).** The verification run already hit these live. Re-capture into repo fixtures so normalizer tests are deterministic and offline:

```bash
cd apps/api && mkdir -p tests/fixtures/sources/naver
UA='Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1'
REF='https://m.sports.naver.com/'
curl -s -H "User-Agent: $UA" -H "Referer: $REF" \
  'https://api-gw.sports.naver.com/schedule/games?fields=basic&upperCategoryId=kbaseball&categoryId=kbo&fromDate=2025-05-14&toDate=2025-05-14' \
  > tests/fixtures/sources/naver/schedule_20250514.json
GID=20250514WOLG02025
for sub in preview record; do
  curl -s -H "User-Agent: $UA" -H "Referer: $REF" \
    "https://api-gw.sports.naver.com/schedule/games/$GID/$sub" \
    > "tests/fixtures/sources/naver/${sub}_${GID}.json"
done
```

Commit these fixtures with Task 3. If an endpoint has changed since 2026-05-29, the tests below pin the exact field names this plan relies on — update the parser to match the new shape, not the test expectation.

---

## Task 1: HttpClient — POST, rate limiting, per-call headers

**Files:**
- Modify: `app/ingestion/http_client.py`
- Create: `tests/ingestion/conftest.py`
- Test: `tests/ingestion/test_http_client_post.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/ingestion/test_http_client_post.py
"""Verifies HttpClient.post (form-encoded), per-call headers, and the per-host
rate limiter. No real network — httpx.MockTransport only."""
from __future__ import annotations

import httpx

from app.ingestion.http_client import HttpClient


def _client(handler) -> HttpClient:
    transport = httpx.MockTransport(handler)
    inner = httpx.Client(transport=transport)
    return HttpClient(client=inner, retry_backoff=(0.0,))


def test_post_sends_form_body_and_returns_result():
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["body"] = request.content.decode()
        seen["referer"] = request.headers.get("Referer")
        return httpx.Response(200, json={"code": 100, "game": []})

    http = _client(handler)
    result = http.post(
        "https://www.koreabaseball.com/ws/Main.asmx/GetKboGameList",
        data={"leId": "1", "srId": "0", "date": "20250514"},
        headers={"Referer": "https://www.koreabaseball.com/Schedule/Schedule.aspx"},
    )
    assert seen["method"] == "POST"
    assert "leId=1" in seen["body"] and "date=20250514" in seen["body"]
    assert seen["referer"] == "https://www.koreabaseball.com/Schedule/Schedule.aspx"
    assert result.status_code == 200
    assert '"code": 100' in result.body or '"code":100' in result.body


def test_fetch_passes_per_call_headers():
    seen: dict[str, str | None] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["referer"] = request.headers.get("Referer")
        return httpx.Response(200, text="ok")

    http = _client(handler)
    http.fetch("https://api-gw.sports.naver.com/x", headers={"Referer": "https://m.sports.naver.com/"})
    assert seen["referer"] == "https://m.sports.naver.com/"


def test_rate_limiter_enforces_min_interval(monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr("app.ingestion.http_client.time.sleep", lambda s: sleeps.append(s))
    clock = {"t": 1000.0}
    monkeypatch.setattr("app.ingestion.http_client.time.monotonic", lambda: clock["t"])

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="ok")

    http = HttpClient(client=httpx.Client(transport=httpx.MockTransport(handler)),
                      retry_backoff=(0.0,), min_interval=5.0)
    http.fetch("https://api-gw.sports.naver.com/a")   # first call: no wait
    http.fetch("https://api-gw.sports.naver.com/b")   # immediate second: must wait ~5s
    assert any(s >= 4.9 for s in sleeps), f"expected a ~5s throttle sleep, got {sleeps}"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/ingestion/test_http_client_post.py -v`
Expected: FAIL — `HttpClient.post` does not exist / `fetch()` takes no `headers` / no `min_interval` kwarg.

- [ ] **Step 3: Implement POST, per-call headers, rate limiter**

In `app/ingestion/http_client.py`: add `min_interval` to `__init__`, track last-request time per host, add a `_throttle(url)` helper, thread an optional `headers` arg through `fetch`, and add `post`. Replace the request-issuing core so both verbs share retry/size/throttle logic.

```python
# add to imports
from urllib.parse import urlsplit

# add to __init__ params (after retry_backoff):
#     min_interval: float = 0.0,
# and in the body:
        self._min_interval = min_interval
        self._last_request_at: dict[str, float] = {}

    def _throttle(self, url: str) -> None:
        """Sleep so successive requests to the same host honor min_interval."""
        if self._min_interval <= 0:
            return
        host = urlsplit(url).netloc
        last = self._last_request_at.get(host)
        now = time.monotonic()
        if last is not None:
            wait = self._min_interval - (now - last)
            if wait > 0:
                time.sleep(wait)
        self._last_request_at[host] = time.monotonic()

    def _request(self, method: str, url: str, *, data: dict[str, str] | None,
                 headers: dict[str, str] | None) -> FetchResult:
        last_error: Exception | None = None
        for attempt in range(self._max_retries):
            try:
                self._throttle(url)
                if method == "POST":
                    response = self._client.post(url, data=data, headers=headers)
                else:
                    response = self._client.get(url, headers=headers)
                response.raise_for_status()
                if len(response.content) > self._max_bytes:
                    raise FetchError(
                        f"Response too large: {len(response.content)} > {self._max_bytes}"
                    )
                return FetchResult(
                    url=url,
                    status_code=response.status_code,
                    content_type=response.headers.get("content-type", "application/octet-stream"),
                    body=response.text,
                    fetched_at=datetime.now(UTC),
                )
            except FetchError:
                raise
            except (httpx.TimeoutException, httpx.NetworkError, httpx.HTTPStatusError) as exc:
                last_error = exc
                if attempt + 1 < self._max_retries:
                    backoff = (
                        self._retry_backoff[attempt]
                        if attempt < len(self._retry_backoff)
                        else self._retry_backoff[-1]
                    )
                    time.sleep(backoff)
        raise FetchError(f"Failed after {self._max_retries} attempts: {last_error}") from last_error

    def fetch(self, url: str, *, headers: dict[str, str] | None = None) -> FetchResult:
        """GET *url* with retries; optional per-call headers (e.g. Referer)."""
        return self._request("GET", url, data=None, headers=headers)

    def post(self, url: str, *, data: dict[str, str],
             headers: dict[str, str] | None = None) -> FetchResult:
        """POST form-encoded *data* to *url* with retries and optional headers."""
        return self._request("POST", url, data=data, headers=headers)
```

Delete the old `fetch` body (now replaced by `_request`). Keep `close`/`__enter__`/`__exit__`.

- [ ] **Step 4: Create the shared test conftest**

```python
# tests/ingestion/conftest.py
"""Shared fixtures for ingestion tests: in-memory session, mock HttpClient
builder, and a loader for captured source JSON fixtures."""
from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from pathlib import Path

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

import app.models  # noqa: F401 — registers models with Base.metadata
from app.db.base import Base
from app.ingestion.http_client import HttpClient

FIXTURE_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "sources"


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as s:
        yield s
    engine.dispose()


@pytest.fixture
def mock_http() -> Callable[[Callable[[httpx.Request], httpx.Response]], HttpClient]:
    def build(handler: Callable[[httpx.Request], httpx.Response]) -> HttpClient:
        transport = httpx.MockTransport(handler)
        return HttpClient(client=httpx.Client(transport=transport), retry_backoff=(0.0,))
    return build


@pytest.fixture
def load_source() -> Callable[[str], str]:
    def load(relpath: str) -> str:
        return (FIXTURE_DIR / relpath).read_text(encoding="utf-8")
    return load
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/ingestion/test_http_client_post.py -v`
Expected: PASS (3 passed). Also run the existing suite to confirm no regression:
Run: `uv run pytest tests/test_schedule_roster_collectors.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add app/ingestion/http_client.py tests/ingestion/
git commit -m "feat(ingestion): add HttpClient POST, rate limiting, per-call headers"
```

---

## Task 2: game_id utilities and team codes

**Files:**
- Create: `app/ingestion/game_id.py`
- Test: `tests/ingestion/test_game_id.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/ingestion/test_game_id.py
"""Verifies game_id parsing/formatting and KBO<->Naver conversion.
KBO G_ID = YYYYMMDD{away}{home}{seq}; Naver appends the season year."""
from __future__ import annotations

from datetime import date

import pytest

from app.ingestion.game_id import GameId, kbo_to_naver, naver_to_kbo, parse_kbo_game_id


def test_parse_kbo_game_id():
    g = parse_kbo_game_id("20250514WOLG0")
    assert g == GameId(date=date(2025, 5, 14), away="WO", home="LG", seq="0")


def test_parse_rejects_bad_length():
    with pytest.raises(ValueError):
        parse_kbo_game_id("2025")


def test_kbo_to_naver_appends_season():
    assert kbo_to_naver("20250514WOLG0") == "20250514WOLG02025"


def test_naver_to_kbo_strips_season():
    assert naver_to_kbo("20250514WOLG02025") == "20250514WOLG0"


def test_roundtrip():
    assert naver_to_kbo(kbo_to_naver("20250514WOLG0")) == "20250514WOLG0"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/ingestion/test_game_id.py -v`
Expected: FAIL — module `app.ingestion.game_id` not found.

- [ ] **Step 3: Implement game_id module**

```python
# app/ingestion/game_id.py
"""KBO game_id parsing/formatting and KBO<->Naver conversion.

KBO G_ID format: YYYYMMDD + awayTeamCode(2) + homeTeamCode(2) + sequence(1),
e.g. "20250514WOLG0" = 2025-05-14, Kiwoom(WO) @ LG, game 0. Naver's gameId is
the same string with the 4-digit season year appended ("...WOLG02025").
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Final

__all__ = ["TEAM_CODES", "GameId", "parse_kbo_game_id", "kbo_to_naver", "naver_to_kbo"]

# Franchise-tied two-letter codes used in KBO game_ids.
TEAM_CODES: Final[dict[str, str]] = {
    "LG": "LG Twins", "OB": "Doosan Bears", "WO": "Kiwoom Heroes",
    "SK": "SSG Landers", "HT": "KIA Tigers", "SS": "Samsung Lions",
    "LT": "Lotte Giants", "HH": "Hanwha Eagles", "NC": "NC Dinos", "KT": "KT Wiz",
}

_KBO_LEN: Final = 13  # 8 date + 2 away + 2 home + 1 seq


@dataclass(frozen=True)
class GameId:
    """Decoded KBO game id."""
    date: date
    away: str
    home: str
    seq: str


def parse_kbo_game_id(game_id: str) -> GameId:
    """Decode a KBO G_ID string into its parts.

    Raises:
        ValueError: If the id is not 13 characters or the date is unparseable.
    """
    if len(game_id) != _KBO_LEN:
        raise ValueError(f"KBO game_id must be {_KBO_LEN} chars: {game_id!r}")
    y, m, d = int(game_id[0:4]), int(game_id[4:6]), int(game_id[6:8])
    return GameId(date=date(y, m, d), away=game_id[8:10], home=game_id[10:12], seq=game_id[12])


def kbo_to_naver(kbo_game_id: str) -> str:
    """Append the season year to a KBO game_id to get the Naver gameId."""
    g = parse_kbo_game_id(kbo_game_id)
    return f"{kbo_game_id}{g.date.year}"


def naver_to_kbo(naver_game_id: str) -> str:
    """Strip the trailing 4-digit season year from a Naver gameId."""
    if len(naver_game_id) != _KBO_LEN + 4:
        raise ValueError(f"Naver gameId must be {_KBO_LEN + 4} chars: {naver_game_id!r}")
    return naver_game_id[:_KBO_LEN]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/ingestion/test_game_id.py -v`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
git add app/ingestion/game_id.py tests/ingestion/test_game_id.py
git commit -m "feat(ingestion): add game_id parsing and KBO/Naver conversion"
```

---

## Task 3: Schedule collector + normalizer (Naver primary)

**Files:**
- Modify: `app/ingestion/collectors/schedule.py`
- Modify: `app/ingestion/normalizers/schedule.py`
- Create: `tests/fixtures/sources/naver/schedule_20250514.json` (from the capture step)
- Test: `tests/ingestion/test_schedule_naver.py`

Naver schedule shape (verified): `result.games[]` with `gameId`, `gameDate` (`YYYY-MM-DD`),
`gameDateTime`, `homeTeamCode`, `awayTeamCode`, `homeTeamName`, `awayTeamName`, `statusCode`
(`RESULT`/`BEFORE`/...), `cancel`. The Game domain row needs `external_id` (use the KBO id =
`naver_to_kbo(gameId)`), `home_team_id`, `away_team_id`, `game_date`, `venue`.

- [ ] **Step 1: Write the failing test**

```python
# tests/ingestion/test_schedule_naver.py
"""Schedule collector hits the Naver schedule endpoint and stores raw JSON;
the normalizer parses result.games[] into Game rows (LG games only)."""
from __future__ import annotations

import json
from datetime import date

import httpx

from app.ingestion.collectors.schedule import build_naver_schedule_url, collect_lg_schedule
from app.ingestion.normalizers.schedule import normalize_schedule
from app.ingestion.raw_store import save_raw_payload  # noqa: F401  (used indirectly)
from app.models.game import Game
from app.models.snapshot import IngestionRun
from app.models.team import Team


def _seed_teams(session) -> None:
    for code, name in [("LG", "LG"), ("WO", "Kiwoom")]:
        session.add(Team(code=code, name=name))
    session.flush()


def test_build_naver_schedule_url_has_kbo_category_and_dates():
    url = build_naver_schedule_url(date_from=date(2025, 5, 14), date_to=date(2025, 5, 14))
    assert "api-gw.sports.naver.com/schedule/games" in url
    assert "categoryId=kbo" in url and "fromDate=2025-05-14" in url and "toDate=2025-05-14" in url


def test_collect_schedule_stores_naver_payload(session, mock_http, load_source):
    body = load_source("naver/schedule_20250514.json")

    def handler(request: httpx.Request) -> httpx.Response:
        assert "Referer" in request.headers
        return httpx.Response(200, text=body, headers={"content-type": "application/json"})

    run = IngestionRun(source="test:sched", status="running")
    session.add(run); session.flush()
    payload, created = collect_lg_schedule(
        session=session, ingestion_run=run,
        date_from=date(2025, 5, 14), date_to=date(2025, 5, 14), http=mock_http(handler),
    )
    assert created is True
    assert payload.source_name == "naver_sports"
    assert "result" in json.loads(payload.raw_body)


def test_normalize_schedule_creates_lg_game(session, load_source):
    _seed_teams(session)
    run = IngestionRun(source="test:sched", status="running")
    session.add(run); session.flush()
    from app.ingestion.raw_store import save_raw_payload
    from app.schemas.ingestion import RawPayloadCreate
    from app.ingestion.types import PayloadCategory
    from datetime import UTC, datetime
    payload, _ = save_raw_payload(session, RawPayloadCreate(
        ingestion_run_id=run.id, category=PayloadCategory.SCHEDULE, source_name="naver_sports",
        source_url="https://api-gw.sports.naver.com/schedule/games?x", fetched_at=datetime.now(UTC),
        content_type="application/json", raw_body=load_source("naver/schedule_20250514.json"),
    ))
    result = normalize_schedule(session, payload)
    game = session.query(Game).filter(Game.external_id == "20250514WOLG0").one()
    assert game.game_date == date(2025, 5, 14)
    assert result.games_created >= 1
```

- [ ] **Step 2: Run the capture step** (top of this plan) to create `schedule_20250514.json`, then run the test.

Run: `uv run pytest tests/ingestion/test_schedule_naver.py -v`
Expected: FAIL — `build_naver_schedule_url` missing; normalizer expects old `{"games":[...]}` shape.

- [ ] **Step 3: Rewrite the schedule collector**

```python
# app/ingestion/collectors/schedule.py — replace URL builder + collector body
from __future__ import annotations

from datetime import date
from typing import Final

from sqlalchemy.orm import Session

from app.ingestion.collectors._constants import LG_TEAM_CODE  # noqa: F401 (kept for callers)
from app.ingestion.http_client import HttpClient
from app.ingestion.raw_store import save_raw_payload
from app.ingestion.types import PayloadCategory
from app.models.snapshot import IngestionRun, RawIngestionPayload
from app.schemas.ingestion import RawPayloadCreate

__all__ = ["build_naver_schedule_url", "collect_lg_schedule"]

NAVER_SCHEDULE_URL: Final = (
    "https://api-gw.sports.naver.com/schedule/games"
    "?fields=basic&upperCategoryId=kbaseball&categoryId=kbo&fromDate={frm}&toDate={to}"
)
NAVER_REFERER: Final = "https://m.sports.naver.com/"


def build_naver_schedule_url(*, date_from: date, date_to: date) -> str:
    """Naver KBO schedule URL for an inclusive date range (full league, not LG-only)."""
    return NAVER_SCHEDULE_URL.format(frm=date_from.isoformat(), to=date_to.isoformat())


def collect_lg_schedule(
    *, session: Session, ingestion_run: IngestionRun,
    date_from: date, date_to: date, http: HttpClient,
) -> tuple[RawIngestionPayload, bool]:
    """Fetch the KBO schedule for [date_from, date_to] from Naver and store raw JSON.

    The payload holds all KBO games for the range; the normalizer filters to LG.

    Raises:
        ValueError: If date_from is later than date_to.
        FetchError: If the request fails after retries.
    """
    if date_from > date_to:
        raise ValueError(f"date_from ({date_from}) must not be later than date_to ({date_to})")
    url = build_naver_schedule_url(date_from=date_from, date_to=date_to)
    result = http.fetch(url, headers={"Referer": NAVER_REFERER})
    payload = RawPayloadCreate(
        ingestion_run_id=ingestion_run.id, category=PayloadCategory.SCHEDULE,
        source_name="naver_sports", source_url=result.url, fetched_at=result.fetched_at,
        content_type=result.content_type, raw_body=result.body,
    )
    return save_raw_payload(session, payload)
```

- [ ] **Step 4: Rewrite the schedule normalizer to parse Naver shape**

Replace the parse loop in `app/ingestion/normalizers/schedule.py`. Keep `ScheduleNormalizeResult` and the `needs_review_reasons` pattern; change only what it reads and filter to LG games.

```python
# replace the body of normalize_schedule after the JSON-load guard:
    games_list = body.get("result", {}).get("games")
    if not isinstance(games_list, list):
        raise ValueError("schedule payload missing result.games list")

    games_created = 0
    games_existing = 0
    needs_review_reasons: list[str] = []

    for entry in games_list:
        home_code = entry.get("homeTeamCode")
        away_code = entry.get("awayTeamCode")
        if "LG" not in (home_code, away_code):
            continue  # single-team MVP: only LG games
        naver_id = entry.get("gameId")
        game_date_str = entry.get("gameDate")
        if not naver_id or not game_date_str:
            needs_review_reasons.append(f"game entry missing gameId/gameDate: {entry!r}")
            continue
        from app.ingestion.game_id import naver_to_kbo
        try:
            external_id = naver_to_kbo(naver_id)
        except ValueError:
            needs_review_reasons.append(f"unparseable Naver gameId={naver_id!r}")
            continue

        existing = session.execute(
            select(Game).where(Game.external_id == external_id)
        ).scalar_one_or_none()
        if existing is not None:
            games_existing += 1
            continue

        home_team = session.execute(select(Team).where(Team.code == home_code)).scalar_one_or_none()
        away_team = session.execute(select(Team).where(Team.code == away_code)).scalar_one_or_none()
        if home_team is None or away_team is None:
            needs_review_reasons.append(
                f"game {external_id!r}: unknown team code(s) home={home_code!r} away={away_code!r}"
            )
            continue
        try:
            parsed_date = date.fromisoformat(game_date_str)
        except ValueError:
            needs_review_reasons.append(f"game {external_id!r}: bad gameDate={game_date_str!r}")
            continue

        session.add(Game(
            external_id=external_id, home_team_id=home_team.id, away_team_id=away_team.id,
            game_date=parsed_date, venue=entry.get("stadium"),
        ))
        session.flush()
        games_created += 1

    return ScheduleNormalizeResult(
        games_created=games_created, games_existing=games_existing,
        needs_review_reasons=tuple(needs_review_reasons),
    )
```

Update the docstring's "expected payload" block to describe `result.games[]`. Remove the old `external_id`/`home_team_code` shape.

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/ingestion/test_schedule_naver.py -v`
Expected: PASS (3 passed).

- [ ] **Step 6: Commit**

```bash
git add app/ingestion/collectors/schedule.py app/ingestion/normalizers/schedule.py \
        tests/ingestion/test_schedule_naver.py tests/fixtures/sources/naver/schedule_20250514.json
git commit -m "feat(ingestion): collect+normalize schedule from Naver api-gw"
```

---

## Task 4: Lineup collector + normalizer (+ handedness into Player)

**Files:**
- Modify: `app/ingestion/collectors/lineup.py`
- Modify: `app/ingestion/normalizers/lineup.py`
- Create: `tests/fixtures/sources/naver/preview_20250514WOLG02025.json`
- Test: `tests/ingestion/test_lineup_naver.py`

Naver `/preview` shape (verified): `previewData.{home,away}TeamLineUp.fullLineUp[]` each with
`batorder`, `position`, `batsThrows` ("좌타"/"우타"/"우투좌타" style), `playerCode`, and a `name`.
`previewData.homeStarter`/`awayStarter` carry the starting pitcher. Handedness lives on the lineup
rows and on `playerInfo.hitType`. The lineup normalizer writes an `ActualLineupSnapshot` + rows;
along the way it upserts `Player` rows (external id = `playerCode`, name, bats/throws).

- [ ] **Step 1: Write the failing test** — assert the collector hits `/schedule/games/{naver_id}/preview`, stores JSON; the normalizer creates a lineup snapshot with 9 rows for LG and upserts Players with handedness.

```python
# tests/ingestion/test_lineup_naver.py
"""Lineup collector fetches Naver /preview; normalizer builds an
ActualLineupSnapshot (LG side) and upserts Player handedness."""
from __future__ import annotations

import httpx

from app.ingestion.collectors.lineup import build_naver_preview_url, collect_lg_lineup
from app.ingestion.normalizers.lineup import normalize_lineup
from app.models.player import Player
from app.models.snapshot import ActualLineupSnapshot, IngestionRun


def test_build_preview_url_uses_naver_game_id():
    url = build_naver_preview_url(kbo_game_id="20250514WOLG0")
    assert url.endswith("/schedule/games/20250514WOLG02025/preview")


def test_collect_lineup_stores_payload(session, mock_http, load_source):
    body = load_source("naver/preview_20250514WOLG02025.json")
    handler = lambda req: httpx.Response(200, text=body, headers={"content-type": "application/json"})
    run = IngestionRun(source="test:lineup", status="running"); session.add(run); session.flush()
    payload, created = collect_lg_lineup(
        session=session, ingestion_run=run, kbo_game_id="20250514WOLG0", http=mock_http(handler),
    )
    assert created is True and payload.source_name == "naver_sports"


def test_normalize_lineup_creates_snapshot_and_players(session, load_source):
    # ... seed Game(external_id="20250514WOLG0") + LG/WO Teams, save preview payload,
    # call normalize_lineup, assert a 9-row ActualLineupSnapshot for LG and that
    # Players were upserted with non-empty handedness.
    ...
```

> NOTE for the implementer: fill the third test body using the same seed/save pattern as
> `test_normalize_schedule_creates_lg_game` in Task 3. Assert
> `session.query(ActualLineupSnapshot).count() == 1` and that the row count for the LG side is 9,
> and `session.query(Player).count() >= 9` with handedness populated.

- [ ] **Step 2: Run to verify it fails.** `uv run pytest tests/ingestion/test_lineup_naver.py -v` → FAIL (`build_naver_preview_url` missing; normalizer expects old shape).

- [ ] **Step 3: Rewrite collector** — mirror Task 3's collector, but build the URL from the KBO id via `kbo_to_naver` and category `LINEUP`:

```python
# app/ingestion/collectors/lineup.py — URL builder + collector core
from typing import Final
from app.ingestion.game_id import kbo_to_naver

NAVER_PREVIEW_URL: Final = "https://api-gw.sports.naver.com/schedule/games/{nid}/preview"
NAVER_REFERER: Final = "https://m.sports.naver.com/"

def build_naver_preview_url(*, kbo_game_id: str) -> str:
    """Naver preview (lineup) URL for a KBO game_id."""
    return NAVER_PREVIEW_URL.format(nid=kbo_to_naver(kbo_game_id))

def collect_lg_lineup(*, session, ingestion_run, kbo_game_id: str, http) -> tuple[RawIngestionPayload, bool]:
    """Fetch Naver preview JSON for the game and store it raw."""
    result = http.fetch(build_naver_preview_url(kbo_game_id=kbo_game_id), headers={"Referer": NAVER_REFERER})
    payload = RawPayloadCreate(
        ingestion_run_id=ingestion_run.id, category=PayloadCategory.LINEUP, source_name="naver_sports",
        source_url=result.url, fetched_at=result.fetched_at, content_type=result.content_type,
        raw_body=result.body,
    )
    return save_raw_payload(session, payload)
```

(Keep the same imports block as the old file: `RawPayloadCreate`, `PayloadCategory`, `save_raw_payload`, `IngestionRun`, `RawIngestionPayload`, `Session`, `HttpClient`.)

- [ ] **Step 4: Rewrite normalizer** — parse `previewData.homeTeamLineUp.fullLineUp` (LG side; pick home vs away by matching the Game's home/away team code to "LG"). For each entry: upsert `Player` (external id = `playerCode`, `name`, handedness from `batsThrows`/`playerInfo.hitType`), then create `ActualLineupSnapshotRow(batting_order=batorder, position=position, player_id=...)` under one `ActualLineupSnapshot(game_id, team_id, announced_at)`. Reuse the snapshot/content-hash idempotency already used by the fixture loader (see `app/services/fixture_loader.py` for the snapshot+rows construction shape). Keep `normalize_lineup`'s existing signature and result dataclass.

- [ ] **Step 5: Run tests** → PASS. Also `uv run pytest tests/test_lineup_collector.py -v` (update or supersede the old placeholder test if it asserts the old URL).

- [ ] **Step 6: Commit** `feat(ingestion): collect+normalize lineup and handedness from Naver preview`.

---

## Task 5: Player stats collector + normalizer (basic metrics)

**Files:**
- Modify: `app/ingestion/collectors/player_stats.py`
- Modify: `app/ingestion/normalizers/player_stats.py`
- Reuse fixture: `preview_20250514WOLG02025.json` (stats live in the same preview payload)
- Test: `tests/ingestion/test_player_stats_naver.py`

Naver exposes per-player season stats in `previewData.{home,away}TopPlayer.currentSeasonStats` and
within `fullLineUp` entries / `*Starter.currentSeasonStats`. Available metrics: AVG/OBP/SLG/OPS (hitters),
ERA/WHIP (pitchers). **No wRC+/wOBA** — do not emit them. The normalizer writes a `StatSnapshot` +
`PlayerStatSnapshotRow` per player from these fields.

- [ ] **Step 1: Write the failing test** — collector builds the preview URL (same as Task 4) under category `PLAYER_STATS`; normalizer creates a `StatSnapshot` with rows whose OBP/SLG are populated and whose wRC+ field is left null/absent. (Seed/save pattern as Task 3.)
- [ ] **Step 2: Run → FAIL.**
- [ ] **Step 3: Collector** — same fetch shape as Task 4 but `category=PayloadCategory.PLAYER_STATS`; keep `collect_lg_hitter_season_stats` name (called by `daily_pipeline`), change its signature to take `kbo_game_id` OR keep season-based and source from a season endpoint — for V1 source from the per-game preview, so rename param to `kbo_game_id`. Update `daily_pipeline` call site in Task 7.
- [ ] **Step 4: Normalizer** — parse the season-stat dicts into `PlayerStatSnapshotRow(player_id, obp, slg, ops, ...)`; map only fields Naver provides; leave advanced metrics null. Match `Player` by `playerCode` (created in Task 4).
- [ ] **Step 5: Run → PASS.**
- [ ] **Step 6: Commit** `feat(ingestion): normalize basic player stats from Naver preview`.

---

## Task 6: Box score collector + normalizer

**Files:**
- Modify: `app/ingestion/collectors/box_score.py`
- Modify: `app/ingestion/normalizers/box_score.py`
- Create: `tests/fixtures/sources/naver/record_20250514WOLG02025.json`
- Test: `tests/ingestion/test_box_score_naver.py`

Naver `/record` shape (verified): `recordData.battersBoxscore.{home,away}` (per-player AB/H/HR/RBI/...),
`recordData.pitchersBoxscore.{home,away}`, `recordData.scoreBoard.rheb` (R/H/E/B), `pitchingResult` (W/L/S).
The box-score normalizer writes a `BoxScoreSnapshot` + `BoxScoreRow` per LG player. Game is "final" when
the schedule `statusCode == "RESULT"` (collector may store regardless; normalizer is the gate).

- [ ] **Step 1: Write the failing test** — collector builds `/schedule/games/{naver_id}/record` (category `BOX_SCORE`), stores JSON; normalizer creates a `BoxScoreSnapshot` with one `BoxScoreRow` per LG batter, fields AB/H/RBI populated. (Seed/save pattern as Task 3; capture the record fixture first.)
- [ ] **Step 2: Run → FAIL.**
- [ ] **Step 3: Collector** — `build_naver_record_url(kbo_game_id)` → `.../record`; `collect_lg_box_score(session, ingestion_run, kbo_game_id, http)` mirrors Task 4 collector with `category=BOX_SCORE`.
- [ ] **Step 4: Normalizer** — choose the LG side (`home` vs `away` by team code), iterate `battersBoxscore[side]`, match `Player` by player code, build `BoxScoreRow(at_bats, hits, runs, rbis, extra_stats_json=...)` under one `BoxScoreSnapshot(game_id, ...)`. Follow the box-score construction in `app/services/fixture_loader.py`.
- [ ] **Step 5: Run → PASS.**
- [ ] **Step 6: Commit** `feat(ingestion): collect+normalize box score from Naver record`.

---

## Task 7: Wire the daily pipeline end-to-end

**Files:**
- Modify: `app/jobs/daily_pipeline.py`
- Test: `tests/ingestion/test_daily_pipeline_naver.py`

- [ ] **Step 1: Write the failing test** — with a mock `HttpClient` routing by URL substring (`/schedule/games?`→schedule fixture, `/preview`→preview fixture, `/record`→record fixture), `run_daily_pipeline(target_date=date(2025,5,14), session_factory=..., http=mock)` returns `status="completed"` and the DB has 1 Game, 1 ActualLineupSnapshot, 1 StatSnapshot, 1 BoxScoreSnapshot for the LG game. Re-running returns the same run with no new rows (idempotent).

```python
# tests/ingestion/test_daily_pipeline_naver.py (handler sketch)
def handler(request):
    u = str(request.url)
    if "/schedule/games?" in u: body = SCHEDULE_JSON
    elif u.endswith("/preview"): body = PREVIEW_JSON
    elif u.endswith("/record"): body = RECORD_JSON
    else: return httpx.Response(404)
    return httpx.Response(200, text=body, headers={"content-type": "application/json"})
```

- [ ] **Step 2: Run → FAIL** (pipeline still calls roster/season-stats collectors with old signatures).
- [ ] **Step 3: Rewrite `run_daily_pipeline`** — after the schedule collect+normalize, query LG `Game`s for `target_date`; for each, call `collect_lg_lineup` → `normalize_lineup`, `collect_lg_hitter_season_stats(kbo_game_id=...)` → `normalize_player_stats`, and (if the schedule says `RESULT`) `collect_lg_box_score` → `normalize_box_score`. Update `DailyPipelineResult` fields to counts (`lineups_created`, `box_scores_created`, etc.). Drop the roster collector call (handedness now comes from lineup). Keep the `get_or_create_ingestion_run` / completed-run short-circuit / crash-retry `started_at` logic unchanged.
- [ ] **Step 4: Run → PASS.** Then full suite: `uv run pytest -q` → all pass.
- [ ] **Step 5: Manual smoke (optional, live)** — `KBO_DATABASE_URL=sqlite:///./kbo_lineup_lab.db uv run python -m app.cli ingest-daily --date 2025-05-14`, then check `/api/admin/ingestion-runs` and `/api/games/{id}/...` via the `running-fixture-demo` flow.
- [ ] **Step 6: Commit** `feat(jobs): wire daily pipeline to Naver collectors end-to-end`.

---

## Notes for the implementer

- **No live network in tests.** Every collector test uses the `mock_http` fixture; every normalizer test
  uses captured JSON via `load_source`. The only live calls are the one-time capture step and the optional
  Task 7 manual smoke.
- **Idempotency is already provided** by `save_raw_payload` (raw) and snapshot content-hash uniqueness
  (domain). Don't reimplement it — construct snapshots the way `app/services/fixture_loader.py` does.
- **Politeness**: construct the production `HttpClient` with `min_interval=5.0` in `daily_pipeline`
  (tests pass their own zero-interval mock).
- **Old placeholder tests** (`test_schedule_roster_collectors.py`, `test_lineup_collector.py`,
  `test_box_score_collector.py`, `test_player_stats_collector.py`) assert the old `koreabaseball.com`
  URLs and HTML payloads. Supersede them with the new `tests/ingestion/*_naver.py` tests; delete the
  superseded assertions rather than leaving both.
- **KBO backup endpoints** (verified `GetKboGameList` / `GetScoreBoardScroll`) are not implemented in this
  plan — they are the documented fallback. Add them only if Naver proves unreliable.
