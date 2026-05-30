# tests/ingestion/test_season_stats_collector.py
"""Season-stat collector builds the verified per-player URL, sends a Referer,
and stores the raw JSON payload (idempotent via the raw store)."""

from __future__ import annotations

from collections.abc import Callable

import httpx
from sqlalchemy.orm import Session

from app.ingestion.collectors.season_stats import (
    build_player_season_url,
    collect_player_season_stats,
)
from app.ingestion.http_client import HttpClient
from app.models.snapshot import IngestionRun

MockHttp = Callable[[Callable[[httpx.Request], httpx.Response]], HttpClient]


def test_build_player_season_url_contains_player_code() -> None:
    url = build_player_season_url(player_code="62415")
    assert url == "https://api-gw.sports.naver.com/players/kbo/62415/playerend-record"


def test_collect_stores_payload(
    session: Session, mock_http: MockHttp, load_source: Callable[[str], str]
) -> None:
    body = load_source("naver/player_season_62415.json")

    def handler(request: httpx.Request) -> httpx.Response:
        assert "Referer" in request.headers
        return httpx.Response(200, text=body, headers={"content-type": "application/json"})

    run = IngestionRun(source="test:season", status="running")
    session.add(run)
    session.flush()
    payload, created = collect_player_season_stats(
        session=session, ingestion_run=run, player_code="62415", http=mock_http(handler)
    )
    assert created is True
    assert payload.source_name == "naver_sports"
    assert "62415" in payload.source_url


def test_collect_is_idempotent(
    session: Session, mock_http: MockHttp, load_source: Callable[[str], str]
) -> None:
    body = load_source("naver/player_season_62415.json")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=body, headers={"content-type": "application/json"})

    run = IngestionRun(source="test:season", status="running")
    session.add(run)
    session.flush()
    first, created1 = collect_player_season_stats(
        session=session, ingestion_run=run, player_code="62415", http=mock_http(handler)
    )
    second, created2 = collect_player_season_stats(
        session=session, ingestion_run=run, player_code="62415", http=mock_http(handler)
    )
    assert created1 is True and created2 is False
    assert first.id == second.id
