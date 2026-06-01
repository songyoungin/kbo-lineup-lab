# tests/ingestion/test_kbo_official_collector.py
"""KBO official collectors build per-player record URLs, send a browser
User-Agent, and store the raw HTML payload (idempotent via the raw store)."""

from __future__ import annotations

from collections.abc import Callable

import httpx
from sqlalchemy.orm import Session

from app.ingestion.collectors.kbo_official import (
    KBO_SOURCE_NAME,
    KBO_USER_AGENT,
    build_kbo_hitter_situation_url,
    collect_kbo_hitter_situation,
)
from app.ingestion.http_client import HttpClient
from app.ingestion.types import PayloadCategory
from app.models.snapshot import IngestionRun

MockHttp = Callable[[Callable[[httpx.Request], httpx.Response]], HttpClient]


def test_build_kbo_hitter_situation_url() -> None:
    url = build_kbo_hitter_situation_url(player_code="66108")
    assert url.endswith("/HitterDetail/Situation.aspx?playerId=66108")
    assert "koreabaseball.com" in url


def test_collect_kbo_hitter_situation_stores_raw(session: Session, mock_http: MockHttp) -> None:
    body = "<html><body><h6>투수유형별</h6></body></html>"

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers.get("User-Agent") == KBO_USER_AGENT
        return httpx.Response(200, text=body, headers={"content-type": "text/html"})

    run = IngestionRun(source="test:kbo", status="running")
    session.add(run)
    session.flush()
    payload, created = collect_kbo_hitter_situation(
        session=session, ingestion_run=run, player_code="66108", http=mock_http(handler)
    )
    assert created is True
    assert payload.source_name == KBO_SOURCE_NAME
    assert payload.source_name == "kbo_official"
    assert payload.category == PayloadCategory.PLAYER_STATS.value
    assert "투수유형별" in payload.raw_body
