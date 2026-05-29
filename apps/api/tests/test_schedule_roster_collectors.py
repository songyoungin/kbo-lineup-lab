"""Tests for the roster collector and HttpClient wrapper.

Verifies:
- collect_lg_roster saves a raw payload row with correct metadata
- The roster URL contains both team code and season year
- HttpClient retries on transient HTTP 5xx errors and succeeds on the third attempt
- HttpClient raises FetchError after exhausting all retries
- HttpClient raises FetchError when the response body exceeds max_bytes
- HttpClient sets the expected User-Agent header on every request

Note: Schedule collector tests have been superseded by
``tests/ingestion/test_schedule_naver.py`` (Naver primary source).

No real network connections are made. All HTTP interactions use httpx.MockTransport.
"""

from __future__ import annotations

from collections.abc import Iterator

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

import app.models  # noqa: F401 — registers all models with Base.metadata
from app.db.base import Base
from app.ingestion.collectors._constants import LG_TEAM_CODE
from app.ingestion.collectors.roster import build_roster_url, collect_lg_roster
from app.ingestion.http_client import USER_AGENT, FetchError, HttpClient
from app.models.snapshot import IngestionRun

# ---------------------------------------------------------------------------
# Shared constants
# ---------------------------------------------------------------------------

SAMPLE_ROSTER_HTML = "<html><body>LG roster 2026</body></html>"
CONTENT_TYPE_HTML = "text/html; charset=utf-8"
SEASON = 2026

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
    """A minimal IngestionRun row for use as parent of test payloads."""
    run = IngestionRun(source="test-collector", status="running")
    session.add(run)
    session.flush()
    return run


# ---------------------------------------------------------------------------
# HttpClient helpers
# ---------------------------------------------------------------------------


def _make_mock_http_client(
    responses: dict[str, tuple[int, str, str]],
    *,
    max_retries: int = 3,
    max_bytes: int = 5 * 1024 * 1024,
    captured_requests: list[httpx.Request] | None = None,
) -> HttpClient:
    """Build an HttpClient backed by httpx.MockTransport with canned responses.

    Args:
        responses: Mapping of URL string → (status_code, body, content_type).
            URLs not in the map receive a 404 response.
        max_retries: Number of retries to configure on the HttpClient.
        max_bytes: Max response body size in bytes.
        captured_requests: If provided, each incoming request is appended to
            this list so callers can inspect headers and URL.

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
    return HttpClient(
        client=inner,
        max_retries=max_retries,
        max_bytes=max_bytes,
        retry_backoff=(0.0,) * max(max_retries, 1),
    )


# ---------------------------------------------------------------------------
# Roster collector tests
# ---------------------------------------------------------------------------


def test_collect_roster_saves_raw_payload(session: Session, ingestion_run: IngestionRun) -> None:
    """collect_lg_roster returns (row, created=True) with correct metadata."""
    url = build_roster_url(year=SEASON)
    http = _make_mock_http_client({url: (200, SAMPLE_ROSTER_HTML, CONTENT_TYPE_HTML)})

    row, created = collect_lg_roster(
        session=session,
        ingestion_run=ingestion_run,
        season=SEASON,
        http=http,
    )

    assert created is True
    assert row.id is not None
    assert row.category == "roster"
    assert row.source_name == "kbo_official"
    assert row.source_url == url
    assert row.raw_body == SAMPLE_ROSTER_HTML
    assert row.ingestion_run_id == ingestion_run.id


def test_collect_roster_url_includes_team_and_year(
    session: Session, ingestion_run: IngestionRun
) -> None:
    """The roster URL must contain both the LG team code and the season year."""
    url = build_roster_url(year=SEASON)

    assert f"teamId={LG_TEAM_CODE}" in url
    assert f"year={SEASON}" in url

    captured: list[httpx.Request] = []
    http = _make_mock_http_client(
        {url: (200, SAMPLE_ROSTER_HTML, CONTENT_TYPE_HTML)},
        captured_requests=captured,
    )

    collect_lg_roster(
        session=session,
        ingestion_run=ingestion_run,
        season=SEASON,
        http=http,
    )

    assert len(captured) == 1
    requested_url = str(captured[0].url)
    assert f"teamId={LG_TEAM_CODE}" in requested_url
    assert f"year={SEASON}" in requested_url


# ---------------------------------------------------------------------------
# HttpClient behaviour tests
# ---------------------------------------------------------------------------


def test_http_client_retries_on_transient_error() -> None:
    """HttpClient retries after 5xx and succeeds when the third attempt returns 200."""
    target_url = "https://kbo.example.com/data"
    call_counts: dict[str, int] = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        call_counts["n"] += 1
        if call_counts["n"] < 3:
            return httpx.Response(500, text="server error")
        return httpx.Response(200, text="ok", headers={"content-type": "text/html"})

    transport = httpx.MockTransport(handler)
    inner = httpx.Client(transport=transport)
    http = HttpClient(client=inner, max_retries=3, retry_backoff=(0.0, 0.0, 0.0))

    result = http.fetch(target_url)

    assert result.status_code == 200
    assert result.body == "ok"
    assert call_counts["n"] == 3


def test_http_client_gives_up_after_max_retries() -> None:
    """HttpClient raises FetchError when every attempt returns 500."""
    target_url = "https://kbo.example.com/data"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="always broken")

    transport = httpx.MockTransport(handler)
    inner = httpx.Client(transport=transport)
    http = HttpClient(client=inner, max_retries=3, retry_backoff=(0.0, 0.0, 0.0))

    with pytest.raises(FetchError, match="Failed after 3 attempts"):
        http.fetch(target_url)


def test_http_client_raises_on_oversized_response() -> None:
    """HttpClient raises FetchError when the response body exceeds max_bytes."""
    target_url = "https://kbo.example.com/data"
    small_limit = 10  # 10 bytes — any real HTML will exceed this
    big_body = "x" * 100

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=big_body, headers={"content-type": "text/html"})

    transport = httpx.MockTransport(handler)
    inner = httpx.Client(transport=transport)
    http = HttpClient(client=inner, max_bytes=small_limit, retry_backoff=(0.0,))

    with pytest.raises(FetchError, match="Response too large"):
        http.fetch(target_url)


def test_http_client_includes_user_agent() -> None:
    """HttpClient sets the expected User-Agent header on every request."""
    target_url = "https://kbo.example.com/data"
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, text="hi", headers={"content-type": "text/html"})

    transport = httpx.MockTransport(handler)
    # Pass explicit headers so the mock client also carries User-Agent.
    inner = httpx.Client(transport=transport, headers={"User-Agent": USER_AGENT})
    http = HttpClient(client=inner, retry_backoff=(0.0,))

    http.fetch(target_url)

    assert len(captured) == 1
    assert captured[0].headers["user-agent"].startswith("kbo-lineup-lab/")
