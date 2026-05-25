"""Tests for the LG Twins lineup collector.

Verifies:
- collect_lg_lineup returns WAITING when response has no lineup arrays
- collect_lg_lineup returns COLLECTED when lineup arrays are non-empty
- The requested URL contains the game_id
- Repeated calls with identical response bodies return same row id (idempotent)
- announced_at is parsed from lineupAnnouncedAt when present
- announced_at is None when lineupAnnouncedAt is absent
- Unparseable JSON body yields WAITING status
- Both empty lineup arrays explicitly yield WAITING
- DB row count is unchanged when status is WAITING

No real network connections are made. All HTTP interactions use httpx.MockTransport.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime

import httpx
import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

import app.models  # noqa: F401 — registers all models with Base.metadata
from app.db.base import Base
from app.ingestion.collectors.lineup import (
    LINEUP_URL_TEMPLATE,
    LineupStatus,
    collect_lg_lineup,
)
from app.ingestion.http_client import HttpClient
from app.models.snapshot import IngestionRun, RawIngestionPayload

# ---------------------------------------------------------------------------
# Shared constants
# ---------------------------------------------------------------------------

GAME_ID = "20260415LGDOO"
CONTENT_TYPE_JSON = "application/json; charset=utf-8"

SAMPLE_LINEUP_URL = LINEUP_URL_TEMPLATE.format(game_id=GAME_ID)

# JSON with non-empty lineup arrays → COLLECTED
BODY_WITH_LINEUP = json.dumps(
    {
        "awayLineup": [{"playerId": "1", "position": "CF", "battingOrder": 1}],
        "homeLineup": [{"playerId": "2", "position": "SS", "battingOrder": 1}],
        "lineupAnnouncedAt": "2026-04-15T09:00:00+09:00",
    }
)

# JSON with empty lineup arrays → WAITING
BODY_NO_LINEUP = json.dumps({"awayLineup": [], "homeLineup": []})

# JSON with lineup but no announced timestamp
BODY_WITH_LINEUP_NO_TS = json.dumps(
    {
        "awayLineup": [{"playerId": "3", "position": "1B", "battingOrder": 3}],
        "homeLineup": [],
    }
)

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
    run = IngestionRun(source="test-lineup-collector", status="running")
    session.add(run)
    session.flush()
    return run


# ---------------------------------------------------------------------------
# HttpClient helpers
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
# WAITING state tests
# ---------------------------------------------------------------------------


def test_collect_returns_waiting_when_no_lineup_in_body(
    session: Session, ingestion_run: IngestionRun
) -> None:
    """Empty lineup arrays in response → result.status is WAITING, no row created."""
    http = _make_mock_http_client({SAMPLE_LINEUP_URL: (200, BODY_NO_LINEUP, CONTENT_TYPE_JSON)})

    result = collect_lg_lineup(
        session=session,
        ingestion_run=ingestion_run,
        game_id=GAME_ID,
        http=http,
    )

    assert result.status == LineupStatus.WAITING
    assert result.raw_payload is None
    assert result.created is False


def test_collect_returns_waiting_when_both_lineups_empty(
    session: Session, ingestion_run: IngestionRun
) -> None:
    """Explicit test: both awayLineup and homeLineup are empty → WAITING."""
    body = json.dumps({"awayLineup": [], "homeLineup": []})
    http = _make_mock_http_client({SAMPLE_LINEUP_URL: (200, body, CONTENT_TYPE_JSON)})

    result = collect_lg_lineup(
        session=session,
        ingestion_run=ingestion_run,
        game_id=GAME_ID,
        http=http,
    )

    assert result.status == LineupStatus.WAITING


def test_collect_returns_waiting_on_unparseable_body(
    session: Session, ingestion_run: IngestionRun
) -> None:
    """Non-JSON body cannot be parsed → result.status is WAITING."""
    http = _make_mock_http_client({SAMPLE_LINEUP_URL: (200, "not json", "text/plain")})

    result = collect_lg_lineup(
        session=session,
        ingestion_run=ingestion_run,
        game_id=GAME_ID,
        http=http,
    )

    assert result.status == LineupStatus.WAITING
    assert result.raw_payload is None


def test_waiting_result_has_no_raw_payload_row(
    session: Session, ingestion_run: IngestionRun
) -> None:
    """When status is WAITING, no RawIngestionPayload row is inserted."""
    http = _make_mock_http_client({SAMPLE_LINEUP_URL: (200, BODY_NO_LINEUP, CONTENT_TYPE_JSON)})

    collect_lg_lineup(
        session=session,
        ingestion_run=ingestion_run,
        game_id=GAME_ID,
        http=http,
    )

    count = session.execute(select(func.count()).select_from(RawIngestionPayload)).scalar_one()
    assert count == 0


# ---------------------------------------------------------------------------
# COLLECTED state tests
# ---------------------------------------------------------------------------


def test_collect_returns_collected_when_lineup_in_body(
    session: Session, ingestion_run: IngestionRun
) -> None:
    """Non-empty lineup arrays → status COLLECTED, raw_payload populated, created=True."""
    http = _make_mock_http_client({SAMPLE_LINEUP_URL: (200, BODY_WITH_LINEUP, CONTENT_TYPE_JSON)})

    result = collect_lg_lineup(
        session=session,
        ingestion_run=ingestion_run,
        game_id=GAME_ID,
        http=http,
    )

    assert result.status == LineupStatus.COLLECTED
    assert result.raw_payload is not None
    assert result.raw_payload.id is not None
    assert result.raw_payload.category == "lineup"
    assert result.raw_payload.source_name == "naver_sports"
    assert result.raw_payload.source_url == SAMPLE_LINEUP_URL
    assert result.raw_payload.raw_body == BODY_WITH_LINEUP
    assert result.raw_payload.ingestion_run_id == ingestion_run.id
    assert result.created is True


# ---------------------------------------------------------------------------
# URL tests
# ---------------------------------------------------------------------------


def test_collect_url_includes_game_id(session: Session, ingestion_run: IngestionRun) -> None:
    """The game_id must appear in the URL actually requested."""
    captured: list[httpx.Request] = []
    http = _make_mock_http_client(
        {SAMPLE_LINEUP_URL: (200, BODY_WITH_LINEUP, CONTENT_TYPE_JSON)},
        captured_requests=captured,
    )

    collect_lg_lineup(
        session=session,
        ingestion_run=ingestion_run,
        game_id=GAME_ID,
        http=http,
    )

    assert len(captured) == 1
    assert GAME_ID in str(captured[0].url)


# ---------------------------------------------------------------------------
# Idempotency tests
# ---------------------------------------------------------------------------


def test_collect_idempotent_on_identical_body(
    session: Session, ingestion_run: IngestionRun
) -> None:
    """Second call with same response body returns same row id with created=False."""
    http = _make_mock_http_client({SAMPLE_LINEUP_URL: (200, BODY_WITH_LINEUP, CONTENT_TYPE_JSON)})

    result1 = collect_lg_lineup(
        session=session,
        ingestion_run=ingestion_run,
        game_id=GAME_ID,
        http=http,
    )
    result2 = collect_lg_lineup(
        session=session,
        ingestion_run=ingestion_run,
        game_id=GAME_ID,
        http=http,
    )

    assert result1.created is True
    assert result2.created is False
    assert result1.raw_payload is not None
    assert result2.raw_payload is not None
    assert result1.raw_payload.id == result2.raw_payload.id


# ---------------------------------------------------------------------------
# announced_at parsing tests
# ---------------------------------------------------------------------------


def test_collect_parses_announced_at_when_present(
    session: Session, ingestion_run: IngestionRun
) -> None:
    """lineupAnnouncedAt ISO timestamp in body → announced_at is UTC datetime."""
    http = _make_mock_http_client({SAMPLE_LINEUP_URL: (200, BODY_WITH_LINEUP, CONTENT_TYPE_JSON)})

    result = collect_lg_lineup(
        session=session,
        ingestion_run=ingestion_run,
        game_id=GAME_ID,
        http=http,
    )

    assert result.announced_at is not None
    assert result.announced_at.tzinfo == UTC
    # "2026-04-15T09:00:00+09:00" in UTC is "2026-04-15T00:00:00+00:00"
    assert result.announced_at == datetime(2026, 4, 15, 0, 0, 0, tzinfo=UTC)


def test_collect_returns_none_announced_at_when_absent(
    session: Session, ingestion_run: IngestionRun
) -> None:
    """JSON without lineupAnnouncedAt key → announced_at is None."""
    http = _make_mock_http_client(
        {SAMPLE_LINEUP_URL: (200, BODY_WITH_LINEUP_NO_TS, CONTENT_TYPE_JSON)}
    )

    result = collect_lg_lineup(
        session=session,
        ingestion_run=ingestion_run,
        game_id=GAME_ID,
        http=http,
    )

    assert result.announced_at is None


def test_collect_parses_announced_at_in_waiting_state(
    session: Session, ingestion_run: IngestionRun
) -> None:
    """announced_at may be populated even in WAITING state when timestamp is present."""
    body = json.dumps(
        {
            "awayLineup": [],
            "homeLineup": [],
            "lineupAnnouncedAt": "2026-04-15T10:30:00+09:00",
        }
    )
    http = _make_mock_http_client({SAMPLE_LINEUP_URL: (200, body, CONTENT_TYPE_JSON)})

    result = collect_lg_lineup(
        session=session,
        ingestion_run=ingestion_run,
        game_id=GAME_ID,
        http=http,
    )

    assert result.status == LineupStatus.WAITING
    assert result.announced_at is not None
    assert result.announced_at.tzinfo == UTC
