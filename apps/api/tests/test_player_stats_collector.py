"""Tests for the LG Twins player stats collectors.

Verifies:
- collect_lg_hitter_recent_stats emits one payload per window (default [14, 30])
- Recent stats URL contains both window size and as-of date
- Recent stats collector accepts custom windows and produces correct count
- Recent stats collector raises ValueError when windows is empty
- collect_lg_hitter_split_stats saves a payload when source supports splits
- collect_lg_hitter_split_stats records a marker payload when splits are unsupported
- Marker payload is idempotent across two runs

Season stats (collect_lg_hitter_season_stats) are now backed by the Naver preview
endpoint.  Coverage for that function lives in
``tests/ingestion/test_player_stats_naver.py``.

No real network connections are made. All HTTP interactions use httpx.MockTransport.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import date

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

import app.models  # noqa: F401 — registers all models with Base.metadata
from app.db.base import Base
from app.ingestion.collectors import player_stats
from app.ingestion.collectors._constants import LG_TEAM_CODE
from app.ingestion.collectors.player_stats import (
    SPLIT_STATS_URL_TEMPLATE,
    collect_lg_hitter_recent_stats,
    collect_lg_hitter_split_stats,
)
from app.ingestion.http_client import HttpClient
from app.models.snapshot import IngestionRun

# ---------------------------------------------------------------------------
# Shared constants
# ---------------------------------------------------------------------------

SEASON = 2026
AS_OF_DATE = date(2026, 4, 15)
CONTENT_TYPE_HTML = "text/html; charset=utf-8"
SAMPLE_RECENT_HTML_14 = "<html><body>LG recent 14d stats</body></html>"
SAMPLE_RECENT_HTML_30 = "<html><body>LG recent 30d stats</body></html>"
SAMPLE_SPLIT_HTML = "<html><body>LG split stats 2026</body></html>"

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def session() -> Iterator[Session]:
    """In-memory SQLite session with the full schema."""
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as s:
        yield s
    engine.dispose()


@pytest.fixture
def ingestion_run(session: Session) -> IngestionRun:
    """A minimal IngestionRun row to use as parent for test payloads."""
    run = IngestionRun(source="test-player-stats", status="running")
    session.add(run)
    session.flush()
    return run


# ---------------------------------------------------------------------------
# HttpClient helper
# ---------------------------------------------------------------------------


def _make_mock_http_client(
    responses: dict[str, tuple[int, str, str]],
    *,
    captured_requests: list[httpx.Request] | None = None,
) -> HttpClient:
    """Build an HttpClient backed by httpx.MockTransport with canned responses.

    Args:
        responses: Mapping of URL string → (status_code, body, content_type).
            URLs not in the map receive a 404 response.
        captured_requests: If provided, each incoming request is appended so
            callers can inspect the URL and headers.

    Returns:
        Configured HttpClient that never opens real network connections.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        url_str = str(request.url)
        if captured_requests is not None:
            captured_requests.append(request)
        if url_str in responses:
            status, body, ctype = responses[url_str]
            return httpx.Response(status, text=body, headers={"content-type": ctype})
        return httpx.Response(404, text="not found")

    transport = httpx.MockTransport(handler)
    inner = httpx.Client(transport=transport)
    return HttpClient(client=inner, retry_backoff=(0.0,))


# ---------------------------------------------------------------------------
# Recent stats tests
# ---------------------------------------------------------------------------


def test_collect_recent_stats_emits_one_payload_per_window(
    session: Session, ingestion_run: IngestionRun
) -> None:
    """Default windows (14, 30) produce exactly two payload rows."""
    from app.ingestion.collectors.player_stats import RECENT_STATS_URL_TEMPLATE

    url_14 = RECENT_STATS_URL_TEMPLATE.format(
        team_code=LG_TEAM_CODE, year=AS_OF_DATE.year, days=14, as_of=AS_OF_DATE.isoformat()
    )
    url_30 = RECENT_STATS_URL_TEMPLATE.format(
        team_code=LG_TEAM_CODE, year=AS_OF_DATE.year, days=30, as_of=AS_OF_DATE.isoformat()
    )
    http = _make_mock_http_client(
        {
            url_14: (200, SAMPLE_RECENT_HTML_14, CONTENT_TYPE_HTML),
            url_30: (200, SAMPLE_RECENT_HTML_30, CONTENT_TYPE_HTML),
        }
    )

    results = collect_lg_hitter_recent_stats(
        session=session,
        ingestion_run=ingestion_run,
        as_of_date=AS_OF_DATE,
        http=http,
    )

    assert len(results) == 2
    assert all(created is True for _, created in results)
    assert all(row.category == "player_stats" for row, _ in results)


def test_collect_recent_stats_url_includes_window_and_asof(
    session: Session, ingestion_run: IngestionRun
) -> None:
    """Each recent stats URL must contain the window size and as-of date."""
    from app.ingestion.collectors.player_stats import RECENT_STATS_URL_TEMPLATE

    url_14 = RECENT_STATS_URL_TEMPLATE.format(
        team_code=LG_TEAM_CODE, year=AS_OF_DATE.year, days=14, as_of=AS_OF_DATE.isoformat()
    )
    url_30 = RECENT_STATS_URL_TEMPLATE.format(
        team_code=LG_TEAM_CODE, year=AS_OF_DATE.year, days=30, as_of=AS_OF_DATE.isoformat()
    )
    captured: list[httpx.Request] = []
    http = _make_mock_http_client(
        {
            url_14: (200, SAMPLE_RECENT_HTML_14, CONTENT_TYPE_HTML),
            url_30: (200, SAMPLE_RECENT_HTML_30, CONTENT_TYPE_HTML),
        },
        captured_requests=captured,
    )

    collect_lg_hitter_recent_stats(
        session=session,
        ingestion_run=ingestion_run,
        as_of_date=AS_OF_DATE,
        http=http,
    )

    assert len(captured) == 2
    urls = [str(r.url) for r in captured]
    # First request: 14-day window
    assert "recent=14" in urls[0]
    assert f"asof={AS_OF_DATE.isoformat()}" in urls[0]
    # Second request: 30-day window
    assert "recent=30" in urls[1]
    assert f"asof={AS_OF_DATE.isoformat()}" in urls[1]


def test_collect_recent_stats_custom_windows(session: Session, ingestion_run: IngestionRun) -> None:
    """Custom windows=[7, 14, 21] must produce exactly three payload rows."""
    from app.ingestion.collectors.player_stats import RECENT_STATS_URL_TEMPLATE

    custom_windows = [7, 14, 21]
    responses: dict[str, tuple[int, str, str]] = {}
    for w in custom_windows:
        url = RECENT_STATS_URL_TEMPLATE.format(
            team_code=LG_TEAM_CODE,
            year=AS_OF_DATE.year,
            days=w,
            as_of=AS_OF_DATE.isoformat(),
        )
        responses[url] = (200, f"<html>recent {w}d</html>", CONTENT_TYPE_HTML)

    http = _make_mock_http_client(responses)

    results = collect_lg_hitter_recent_stats(
        session=session,
        ingestion_run=ingestion_run,
        as_of_date=AS_OF_DATE,
        http=http,
        windows=custom_windows,
    )

    assert len(results) == 3


def test_collect_recent_stats_rejects_empty_windows(
    session: Session, ingestion_run: IngestionRun
) -> None:
    """Passing windows=[] must raise ValueError before any HTTP request is made."""
    http = _make_mock_http_client({})

    with pytest.raises(ValueError, match="windows must not be empty"):
        collect_lg_hitter_recent_stats(
            session=session,
            ingestion_run=ingestion_run,
            as_of_date=AS_OF_DATE,
            http=http,
            windows=[],
        )


# ---------------------------------------------------------------------------
# Split stats tests
# ---------------------------------------------------------------------------


def test_collect_split_stats_saves_payload_when_supported(
    session: Session, ingestion_run: IngestionRun
) -> None:
    """When splits are supported, collect_lg_hitter_split_stats fetches and saves the page."""
    url = SPLIT_STATS_URL_TEMPLATE.format(team_code=LG_TEAM_CODE, year=SEASON)
    http = _make_mock_http_client({url: (200, SAMPLE_SPLIT_HTML, CONTENT_TYPE_HTML)})

    row, created = collect_lg_hitter_split_stats(
        session=session,
        ingestion_run=ingestion_run,
        season=SEASON,
        http=http,
    )

    assert created is True
    assert row.id is not None
    assert row.category == "player_stats"
    assert row.source_name == "statiz"
    assert row.source_url == url
    assert row.raw_body == SAMPLE_SPLIT_HTML
    assert row.content_type == CONTENT_TYPE_HTML


def test_collect_split_stats_records_marker_when_unsupported(
    monkeypatch: pytest.MonkeyPatch,
    session: Session,
    ingestion_run: IngestionRun,
) -> None:
    """When splits are unsupported, a marker payload is stored without an HTTP fetch.

    Mocking condition: SOURCE_SUPPORTS_HANDEDNESS_SPLITS patched to False.
    Verifies: content_type is 'application/x-source-metadata+json'; body JSON
    contains 'supported': false.
    """
    monkeypatch.setattr(player_stats, "SOURCE_SUPPORTS_HANDEDNESS_SPLITS", False)

    # Provide no matching URL — any real fetch would raise an exception.
    http = _make_mock_http_client({})

    row, created = collect_lg_hitter_split_stats(
        session=session,
        ingestion_run=ingestion_run,
        season=SEASON,
        http=http,
    )

    assert created is True
    assert row.content_type == "application/x-source-metadata+json"
    body = json.loads(row.raw_body)
    assert body["supported"] is False
    assert body["subkind"] == "splits"
    assert body["season"] == SEASON
    assert body["team_code"] == LG_TEAM_CODE


def test_marker_payload_is_idempotent_across_runs(
    monkeypatch: pytest.MonkeyPatch,
    session: Session,
    ingestion_run: IngestionRun,
) -> None:
    """Calling the unsupported split path twice yields the same row (idempotent store).

    Mocking condition: SOURCE_SUPPORTS_HANDEDNESS_SPLITS patched to False.
    Verifies: second call returns created=False and the same row id.
    """
    monkeypatch.setattr(player_stats, "SOURCE_SUPPORTS_HANDEDNESS_SPLITS", False)

    http = _make_mock_http_client({})

    row1, created1 = collect_lg_hitter_split_stats(
        session=session,
        ingestion_run=ingestion_run,
        season=SEASON,
        http=http,
    )
    row2, created2 = collect_lg_hitter_split_stats(
        session=session,
        ingestion_run=ingestion_run,
        season=SEASON,
        http=http,
    )

    assert created1 is True
    assert created2 is False
    assert row1.id == row2.id
