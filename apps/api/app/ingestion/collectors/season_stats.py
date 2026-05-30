"""Collector for a single player's season batting stats from Naver api-gw.

Fetches the verified per-player record endpoint and stores the raw JSON via the
shared raw store (idempotent on source_name+source_url+payload_hash). The endpoint
was verified in docs/data-sources/player-season-stats-verification.md.
"""

from __future__ import annotations

from typing import Final

from sqlalchemy.orm import Session

from app.ingestion.collectors.lineup import NAVER_REFERER
from app.ingestion.http_client import HttpClient
from app.ingestion.raw_store import save_raw_payload
from app.ingestion.types import PayloadCategory
from app.models.snapshot import IngestionRun, RawIngestionPayload
from app.schemas.ingestion import RawPayloadCreate

__all__ = ["build_player_season_url", "collect_player_season_stats"]

PLAYER_SEASON_URL: Final = "https://api-gw.sports.naver.com/players/kbo/{code}/playerend-record"
NAVER_SOURCE_NAME: Final = "naver_sports"


def build_player_season_url(*, player_code: str) -> str:
    """Naver per-player season-record URL (categoryId=kbo) for a player code."""
    return PLAYER_SEASON_URL.format(code=player_code)


def collect_player_season_stats(
    *, session: Session, ingestion_run: IngestionRun, player_code: str, http: HttpClient
) -> tuple[RawIngestionPayload, bool]:
    """Fetch one player's season-stat JSON from Naver and store it raw.

    Args:
        session: Active SQLAlchemy session. Caller controls the transaction.
        ingestion_run: Parent ingestion run this fetch belongs to.
        player_code: Naver player code (== KBO playerCode).
        http: Configured HttpClient. Inject a mock client in tests.

    Returns:
        Tuple of (raw_payload_row, created). created is False when an identical
        payload was already stored.

    Raises:
        FetchError: If the request fails after retries.
    """
    result = http.fetch(
        build_player_season_url(player_code=player_code),
        headers={"Referer": NAVER_REFERER},
    )
    payload = RawPayloadCreate(
        ingestion_run_id=ingestion_run.id,
        category=PayloadCategory.PLAYER_STATS,
        source_name=NAVER_SOURCE_NAME,
        source_url=result.url,
        fetched_at=result.fetched_at,
        content_type=result.content_type,
        raw_body=result.body,
    )
    return save_raw_payload(session, payload)
