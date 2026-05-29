"""Schedule collector hits the Naver schedule endpoint and stores raw JSON;
the normalizer parses result.games[] into Game rows (LG games only)."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, date, datetime

import httpx
from sqlalchemy.orm import Session

from app.ingestion.collectors.schedule import build_naver_schedule_url, collect_lg_schedule
from app.ingestion.http_client import HttpClient
from app.ingestion.normalizers.schedule import normalize_schedule
from app.ingestion.raw_store import save_raw_payload
from app.ingestion.types import PayloadCategory
from app.models.game import Game
from app.models.snapshot import IngestionRun
from app.models.team import Team
from app.schemas.ingestion import RawPayloadCreate

MockHttpBuilder = Callable[[Callable[[httpx.Request], httpx.Response]], HttpClient]


def _seed_teams(session: Session) -> None:
    """Seed LG and Kiwoom teams for normalizer tests."""
    for code, name in [("LG", "LG"), ("WO", "Kiwoom")]:
        session.add(Team(code=code, name=name))
    session.flush()


def test_build_naver_schedule_url_has_kbo_category_and_dates() -> None:
    url = build_naver_schedule_url(date_from=date(2025, 5, 14), date_to=date(2025, 5, 14))
    assert "api-gw.sports.naver.com/schedule/games" in url
    assert "categoryId=kbo" in url and "fromDate=2025-05-14" in url and "toDate=2025-05-14" in url


def test_collect_schedule_stores_naver_payload(
    session: Session,
    mock_http: MockHttpBuilder,
    load_source: Callable[[str], str],
) -> None:
    body = load_source("naver/schedule_20250514.json")

    def handler(request: httpx.Request) -> httpx.Response:
        assert "Referer" in request.headers
        return httpx.Response(200, text=body, headers={"content-type": "application/json"})

    run = IngestionRun(source="test:sched", status="running")
    session.add(run)
    session.flush()
    payload, created = collect_lg_schedule(
        session=session,
        ingestion_run=run,
        date_from=date(2025, 5, 14),
        date_to=date(2025, 5, 14),
        http=mock_http(handler),
    )
    assert created is True
    assert payload.source_name == "naver_sports"
    assert "result" in json.loads(payload.raw_body)


def test_normalize_schedule_creates_lg_game(
    session: Session,
    load_source: Callable[[str], str],
) -> None:
    _seed_teams(session)
    run = IngestionRun(source="test:sched", status="running")
    session.add(run)
    session.flush()
    payload, _ = save_raw_payload(
        session,
        RawPayloadCreate(
            ingestion_run_id=run.id,
            category=PayloadCategory.SCHEDULE,
            source_name="naver_sports",
            source_url="https://api-gw.sports.naver.com/schedule/games?x",
            fetched_at=datetime.now(UTC),
            content_type="application/json",
            raw_body=load_source("naver/schedule_20250514.json"),
        ),
    )
    result = normalize_schedule(session, payload)
    game = session.query(Game).filter(Game.external_id == "20250514WOLG0").one()
    assert game.game_date == date(2025, 5, 14)
    assert result.games_created == 1
    # Fixture has 4 non-LG games that must be filtered out.
    assert session.query(Game).count() == 1
