"""Tests for the LG Twins box score collector.

Verifies:
- collect_lg_box_score returns WAITING when response signals game not final (JSON)
- collect_lg_box_score returns COLLECTED when response signals game is final (JSON)
- The requested URL contains the game_id
- Repeated calls with identical final response bodies return same row id (idempotent)
- final_score is parsed when homeRuns/awayRuns are present in JSON
- final_score is None when score fields are absent from JSON
- HTML body containing 'FINAL' marker → COLLECTED via substring fallback
- HTML body containing '경기종료' marker → COLLECTED via substring fallback
- HTML body without any final marker → WAITING
- DB row count is unchanged when status is WAITING
- Empty body → WAITING

No real network connections are made. All HTTP interactions use httpx.MockTransport.
"""

from __future__ import annotations

import json
from collections.abc import Iterator

import httpx
import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

import app.models  # noqa: F401 — registers all models with Base.metadata
from app.db.base import Base
from app.ingestion.collectors.box_score import (
    BOX_SCORE_URL_TEMPLATE,
    BoxScoreStatus,
    FinalScore,
    collect_lg_box_score,
)
from app.ingestion.http_client import HttpClient
from app.models.snapshot import IngestionRun, RawIngestionPayload

# ---------------------------------------------------------------------------
# Shared constants
# ---------------------------------------------------------------------------

GAME_ID = "20260415LGDOO"
CONTENT_TYPE_JSON = "application/json; charset=utf-8"
CONTENT_TYPE_HTML = "text/html; charset=utf-8"

SAMPLE_BOX_SCORE_URL = BOX_SCORE_URL_TEMPLATE.format(game_id=GAME_ID)

# JSON with final game status and score → COLLECTED
BODY_FINAL_WITH_SCORE = json.dumps(
    {
        "gameStatus": "FINAL",
        "homeRuns": 5,
        "awayRuns": 3,
        "hitters": [{"playerId": "1", "ab": 4, "h": 2}],
    }
)

# JSON with in-progress status → WAITING
BODY_IN_PROGRESS = json.dumps(
    {
        "gameStatus": "IN_PROGRESS",
        "homeRuns": 3,
        "awayRuns": 2,
    }
)

# JSON with final status but no score fields → COLLECTED, no FinalScore
BODY_FINAL_WITHOUT_SCORE = json.dumps({"gameStatus": "FINAL"})

# JSON with final status and score — same as above for idempotency tests
BODY_FINAL_IDEMPOTENT = json.dumps({"gameStatus": "FINAL", "homeRuns": 7, "awayRuns": 4})

# HTML with 'FINAL' substring → COLLECTED via fallback
BODY_HTML_FINAL = "<html><body>경기결과: FINAL<table>...</table></body></html>"

# HTML with '경기종료' substring → COLLECTED via fallback
BODY_HTML_GAMEOVER = "<html><body>경기종료 5:3 LG 승</body></html>"

# HTML without any final marker → WAITING
BODY_HTML_IN_PROGRESS = "<html><body>진행중 3:2 (7회)</body></html>"

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
    run = IngestionRun(source="test-box-score-collector", status="running")
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


def test_collect_returns_waiting_when_game_not_final(
    session: Session, ingestion_run: IngestionRun
) -> None:
    """JSON gameStatus=IN_PROGRESS → result.status is WAITING, no row created.

    Mocks: handler returns {"gameStatus": "IN_PROGRESS", "homeRuns": 3, "awayRuns": 2}.
    Verifies: status WAITING, raw_payload is None, created=False.
    """
    http = _make_mock_http_client(
        {SAMPLE_BOX_SCORE_URL: (200, BODY_IN_PROGRESS, CONTENT_TYPE_JSON)}
    )

    result = collect_lg_box_score(
        session=session,
        ingestion_run=ingestion_run,
        game_id=GAME_ID,
        http=http,
    )

    assert result.status == BoxScoreStatus.WAITING
    assert result.raw_payload is None
    assert result.created is False


def test_waiting_result_has_no_raw_payload_row(
    session: Session, ingestion_run: IngestionRun
) -> None:
    """When status is WAITING, no RawIngestionPayload row is inserted into the DB."""
    http = _make_mock_http_client(
        {SAMPLE_BOX_SCORE_URL: (200, BODY_IN_PROGRESS, CONTENT_TYPE_JSON)}
    )

    collect_lg_box_score(
        session=session,
        ingestion_run=ingestion_run,
        game_id=GAME_ID,
        http=http,
    )

    count = session.execute(select(func.count()).select_from(RawIngestionPayload)).scalar_one()
    assert count == 0


def test_collect_html_body_without_final_marker_returns_waiting(
    session: Session, ingestion_run: IngestionRun
) -> None:
    """HTML body without 'FINAL' or '경기종료' → WAITING via substring fallback."""
    http = _make_mock_http_client(
        {SAMPLE_BOX_SCORE_URL: (200, BODY_HTML_IN_PROGRESS, CONTENT_TYPE_HTML)}
    )

    result = collect_lg_box_score(
        session=session,
        ingestion_run=ingestion_run,
        game_id=GAME_ID,
        http=http,
    )

    assert result.status == BoxScoreStatus.WAITING
    assert result.raw_payload is None


def test_collect_handles_empty_body(session: Session, ingestion_run: IngestionRun) -> None:
    """Empty string body cannot signal a final game → WAITING."""
    http = _make_mock_http_client({SAMPLE_BOX_SCORE_URL: (200, "", CONTENT_TYPE_JSON)})

    result = collect_lg_box_score(
        session=session,
        ingestion_run=ingestion_run,
        game_id=GAME_ID,
        http=http,
    )

    assert result.status == BoxScoreStatus.WAITING
    assert result.raw_payload is None


# ---------------------------------------------------------------------------
# COLLECTED state tests
# ---------------------------------------------------------------------------


def test_collect_returns_collected_when_game_is_final(
    session: Session, ingestion_run: IngestionRun
) -> None:
    """JSON gameStatus=FINAL → status COLLECTED, raw_payload populated, created=True.

    Mocks: handler returns {"gameStatus": "FINAL", "homeRuns": 5, "awayRuns": 3, ...}.
    Verifies: status COLLECTED, raw_payload set, source fields match, created=True.
    """
    http = _make_mock_http_client(
        {SAMPLE_BOX_SCORE_URL: (200, BODY_FINAL_WITH_SCORE, CONTENT_TYPE_JSON)}
    )

    result = collect_lg_box_score(
        session=session,
        ingestion_run=ingestion_run,
        game_id=GAME_ID,
        http=http,
    )

    assert result.status == BoxScoreStatus.COLLECTED
    assert result.raw_payload is not None
    assert result.raw_payload.id is not None
    assert result.raw_payload.category == "box_score"
    assert result.raw_payload.source_name == "kbo_official"
    assert result.raw_payload.source_url == SAMPLE_BOX_SCORE_URL
    assert result.raw_payload.raw_body == BODY_FINAL_WITH_SCORE
    assert result.raw_payload.ingestion_run_id == ingestion_run.id
    assert result.created is True


def test_collect_html_body_with_final_marker_returns_collected(
    session: Session, ingestion_run: IngestionRun
) -> None:
    """HTML body containing 'FINAL' substring → COLLECTED via substring fallback path."""
    http = _make_mock_http_client({SAMPLE_BOX_SCORE_URL: (200, BODY_HTML_FINAL, CONTENT_TYPE_HTML)})

    result = collect_lg_box_score(
        session=session,
        ingestion_run=ingestion_run,
        game_id=GAME_ID,
        http=http,
    )

    assert result.status == BoxScoreStatus.COLLECTED
    assert result.raw_payload is not None
    assert result.created is True


def test_collect_html_body_with_gameover_marker_returns_collected(
    session: Session, ingestion_run: IngestionRun
) -> None:
    """HTML body containing '경기종료' substring → COLLECTED via substring fallback path."""
    http = _make_mock_http_client(
        {SAMPLE_BOX_SCORE_URL: (200, BODY_HTML_GAMEOVER, CONTENT_TYPE_HTML)}
    )

    result = collect_lg_box_score(
        session=session,
        ingestion_run=ingestion_run,
        game_id=GAME_ID,
        http=http,
    )

    assert result.status == BoxScoreStatus.COLLECTED
    assert result.raw_payload is not None
    assert result.created is True


# ---------------------------------------------------------------------------
# URL test
# ---------------------------------------------------------------------------


def test_collect_url_includes_game_id(session: Session, ingestion_run: IngestionRun) -> None:
    """The game_id must appear in the URL actually requested."""
    captured: list[httpx.Request] = []
    http = _make_mock_http_client(
        {SAMPLE_BOX_SCORE_URL: (200, BODY_FINAL_WITH_SCORE, CONTENT_TYPE_JSON)},
        captured_requests=captured,
    )

    collect_lg_box_score(
        session=session,
        ingestion_run=ingestion_run,
        game_id=GAME_ID,
        http=http,
    )

    assert len(captured) == 1
    assert GAME_ID in str(captured[0].url)


# ---------------------------------------------------------------------------
# Idempotency test
# ---------------------------------------------------------------------------


def test_collect_idempotent_on_identical_final_body(
    session: Session, ingestion_run: IngestionRun
) -> None:
    """Second call with same final response body returns same row id with created=False.

    Proves duplicate final payloads dedupe via (source_name, source_url, payload_hash).
    """
    http = _make_mock_http_client(
        {SAMPLE_BOX_SCORE_URL: (200, BODY_FINAL_IDEMPOTENT, CONTENT_TYPE_JSON)}
    )

    result1 = collect_lg_box_score(
        session=session,
        ingestion_run=ingestion_run,
        game_id=GAME_ID,
        http=http,
    )
    result2 = collect_lg_box_score(
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
# FinalScore parsing tests
# ---------------------------------------------------------------------------


def test_collect_parses_final_score_when_present(
    session: Session, ingestion_run: IngestionRun
) -> None:
    """Both homeRuns and awayRuns in JSON → FinalScore populated correctly.

    Mocks: handler returns {"gameStatus": "FINAL", "homeRuns": 5, "awayRuns": 3, ...}.
    Verifies: final_score.home_runs=5, final_score.away_runs=3.
    """
    http = _make_mock_http_client(
        {SAMPLE_BOX_SCORE_URL: (200, BODY_FINAL_WITH_SCORE, CONTENT_TYPE_JSON)}
    )

    result = collect_lg_box_score(
        session=session,
        ingestion_run=ingestion_run,
        game_id=GAME_ID,
        http=http,
    )

    assert result.final_score is not None
    assert isinstance(result.final_score, FinalScore)
    assert result.final_score.home_runs == 5
    assert result.final_score.away_runs == 3


def test_collect_returns_none_final_score_when_absent(
    session: Session, ingestion_run: IngestionRun
) -> None:
    """JSON without homeRuns/awayRuns → final_score is None even when game is final."""
    http = _make_mock_http_client(
        {SAMPLE_BOX_SCORE_URL: (200, BODY_FINAL_WITHOUT_SCORE, CONTENT_TYPE_JSON)}
    )

    result = collect_lg_box_score(
        session=session,
        ingestion_run=ingestion_run,
        game_id=GAME_ID,
        http=http,
    )

    assert result.status == BoxScoreStatus.COLLECTED
    assert result.final_score is None
