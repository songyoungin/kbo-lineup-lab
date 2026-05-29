"""Tests for the LG Twins box score collector (Naver record endpoint).

Verifies:
- collect_lg_box_score returns WAITING when the record has no batters box score
- collect_lg_box_score returns COLLECTED when batters box score data is present
- The requested URL targets the api-gw record endpoint with the game id
- Repeated calls with identical final response bodies return same row id (idempotent)
- final_score is parsed from scoreBoard.rheb when present
- final_score is None when score fields are absent
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
    BoxScoreStatus,
    FinalScore,
    build_naver_record_url,
    collect_lg_box_score,
)
from app.ingestion.http_client import HttpClient
from app.models.snapshot import IngestionRun, RawIngestionPayload

# ---------------------------------------------------------------------------
# Shared constants
# ---------------------------------------------------------------------------

GAME_ID = "20250514WOLG0"
CONTENT_TYPE_JSON = "application/json; charset=utf-8"

RECORD_URL = build_naver_record_url(kbo_game_id=GAME_ID)


def _record_body(
    *,
    home_batters: list[dict[str, object]],
    away_batters: list[dict[str, object]],
    rheb: dict[str, object] | None = None,
) -> str:
    """Build a minimal Naver record body for collector tests."""
    record: dict[str, object] = {
        "battersBoxscore": {"home": home_batters, "away": away_batters},
    }
    if rheb is not None:
        record["scoreBoard"] = {"rheb": rheb}
    return json.dumps({"result": {"recordData": record}})


# JSON with batters box score and final score → COLLECTED
BODY_FINAL_WITH_SCORE = _record_body(
    home_batters=[{"playerCode": "62415", "ab": 4, "hit": 2}],
    away_batters=[],
    rheb={"home": {"r": 5}, "away": {"r": 3}},
)

# JSON with empty box score → WAITING
BODY_NOT_FINAL = _record_body(home_batters=[], away_batters=[])

# JSON with batters but no score board → COLLECTED, no FinalScore
BODY_FINAL_WITHOUT_SCORE = _record_body(
    home_batters=[{"playerCode": "62415", "ab": 4, "hit": 2}],
    away_batters=[],
)

# JSON identical-final body for idempotency tests
BODY_FINAL_IDEMPOTENT = _record_body(
    home_batters=[{"playerCode": "62415", "ab": 3, "hit": 1}],
    away_batters=[],
    rheb={"home": {"r": 7}, "away": {"r": 4}},
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
    """Empty battersBoxscore → result.status is WAITING, no row created."""
    http = _make_mock_http_client({RECORD_URL: (200, BODY_NOT_FINAL, CONTENT_TYPE_JSON)})

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
    http = _make_mock_http_client({RECORD_URL: (200, BODY_NOT_FINAL, CONTENT_TYPE_JSON)})

    collect_lg_box_score(
        session=session,
        ingestion_run=ingestion_run,
        game_id=GAME_ID,
        http=http,
    )

    count = session.execute(select(func.count()).select_from(RawIngestionPayload)).scalar_one()
    assert count == 0


def test_collect_handles_empty_body(session: Session, ingestion_run: IngestionRun) -> None:
    """Empty string body cannot signal a final game → WAITING."""
    http = _make_mock_http_client({RECORD_URL: (200, "", CONTENT_TYPE_JSON)})

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
    """Populated battersBoxscore → status COLLECTED, raw_payload populated."""
    http = _make_mock_http_client({RECORD_URL: (200, BODY_FINAL_WITH_SCORE, CONTENT_TYPE_JSON)})

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
    assert result.raw_payload.source_name == "naver_sports"
    assert result.raw_payload.source_url == RECORD_URL
    assert result.raw_payload.raw_body == BODY_FINAL_WITH_SCORE
    assert result.raw_payload.ingestion_run_id == ingestion_run.id
    assert result.created is True


# ---------------------------------------------------------------------------
# URL test
# ---------------------------------------------------------------------------


def test_collect_url_targets_record_endpoint(session: Session, ingestion_run: IngestionRun) -> None:
    """The requested URL must be the api-gw record endpoint and send a Referer."""
    captured: list[httpx.Request] = []
    http = _make_mock_http_client(
        {RECORD_URL: (200, BODY_FINAL_WITH_SCORE, CONTENT_TYPE_JSON)},
        captured_requests=captured,
    )

    collect_lg_box_score(
        session=session,
        ingestion_run=ingestion_run,
        game_id=GAME_ID,
        http=http,
    )

    assert len(captured) == 1
    assert str(captured[0].url) == RECORD_URL
    assert captured[0].headers.get("Referer") == "https://m.sports.naver.com/"


# ---------------------------------------------------------------------------
# Idempotency test
# ---------------------------------------------------------------------------


def test_collect_idempotent_on_identical_final_body(
    session: Session, ingestion_run: IngestionRun
) -> None:
    """Second call with same final response body returns same row id with created=False.

    Proves duplicate final payloads dedupe via (source_name, source_url, payload_hash).
    """
    http = _make_mock_http_client({RECORD_URL: (200, BODY_FINAL_IDEMPOTENT, CONTENT_TYPE_JSON)})

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
    """scoreBoard.rheb home/away runs → FinalScore populated correctly."""
    http = _make_mock_http_client({RECORD_URL: (200, BODY_FINAL_WITH_SCORE, CONTENT_TYPE_JSON)})

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
    """No scoreBoard → final_score is None even when the box score is present."""
    http = _make_mock_http_client({RECORD_URL: (200, BODY_FINAL_WITHOUT_SCORE, CONTENT_TYPE_JSON)})

    result = collect_lg_box_score(
        session=session,
        ingestion_run=ingestion_run,
        game_id=GAME_ID,
        http=http,
    )

    assert result.status == BoxScoreStatus.COLLECTED
    assert result.final_score is None
