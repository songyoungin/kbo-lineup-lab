"""Collectors for KBO official player record pages.

Fetches a player's KBO official record pages (hitter situation/basic splits and
pitcher basics) by playerId and stores the raw HTML via the shared raw store
(idempotent on source_name+source_url+payload_hash). KBO official pages reject
the default scraper UA, so requests carry a browser User-Agent.
"""

from __future__ import annotations

from typing import Final

from sqlalchemy.orm import Session

from app.ingestion.http_client import HttpClient
from app.ingestion.raw_store import save_raw_payload
from app.ingestion.types import PayloadCategory
from app.models.snapshot import IngestionRun, RawIngestionPayload
from app.schemas.ingestion import RawPayloadCreate

__all__ = [
    "KBO_SOURCE_NAME",
    "KBO_USER_AGENT",
    "build_kbo_hitter_basic_url",
    "build_kbo_hitter_situation_url",
    "build_kbo_pitcher_basic_url",
    "collect_kbo_hitter_basic",
    "collect_kbo_hitter_situation",
    "collect_kbo_pitcher_basic",
]

KBO_SOURCE_NAME: Final = "kbo_official"
KBO_USER_AGENT: Final = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

_BASE_URL: Final = "https://www.koreabaseball.com/Record/Player"


def build_kbo_hitter_situation_url(*, player_code: str) -> str:
    """KBO official hitter situational-splits page URL for a player code."""
    return f"{_BASE_URL}/HitterDetail/Situation.aspx?playerId={player_code}"


def build_kbo_hitter_basic_url(*, player_code: str) -> str:
    """KBO official hitter basic-record page URL for a player code."""
    return f"{_BASE_URL}/HitterDetail/Basic.aspx?playerId={player_code}"


def build_kbo_pitcher_basic_url(*, player_code: str) -> str:
    """KBO official pitcher basic-record page URL for a player code."""
    return f"{_BASE_URL}/PitcherDetail/Basic.aspx?playerId={player_code}"


def _collect(
    session: Session, ingestion_run: IngestionRun, url: str, http: HttpClient
) -> tuple[RawIngestionPayload, bool]:
    """Fetch *url* with a browser UA and store the raw HTML payload.

    Args:
        session: Active SQLAlchemy session. Caller controls the transaction.
        ingestion_run: Parent ingestion run this fetch belongs to.
        url: Fully qualified KBO official record page URL.
        http: Configured HttpClient. Inject a mock client in tests.

    Returns:
        Tuple of (raw_payload_row, created). created is False when an identical
        payload was already stored.

    Raises:
        FetchError: If the request fails after retries.
    """
    result = http.fetch(url, headers={"User-Agent": KBO_USER_AGENT})
    payload = RawPayloadCreate(
        ingestion_run_id=ingestion_run.id,
        category=PayloadCategory.PLAYER_STATS,
        source_name=KBO_SOURCE_NAME,
        source_url=result.url,
        fetched_at=result.fetched_at,
        content_type=result.content_type,
        raw_body=result.body,
    )
    return save_raw_payload(session, payload)


def collect_kbo_hitter_situation(
    *, session: Session, ingestion_run: IngestionRun, player_code: str, http: HttpClient
) -> tuple[RawIngestionPayload, bool]:
    """Fetch a player's KBO hitter situational-splits page and store it raw.

    Args:
        session: Active SQLAlchemy session. Caller controls the transaction.
        ingestion_run: Parent ingestion run this fetch belongs to.
        player_code: KBO playerId.
        http: Configured HttpClient. Inject a mock client in tests.

    Returns:
        Tuple of (raw_payload_row, created).

    Raises:
        FetchError: If the request fails after retries.
    """
    url = build_kbo_hitter_situation_url(player_code=player_code)
    return _collect(session, ingestion_run, url, http)


def collect_kbo_hitter_basic(
    *, session: Session, ingestion_run: IngestionRun, player_code: str, http: HttpClient
) -> tuple[RawIngestionPayload, bool]:
    """Fetch a player's KBO hitter basic-record page and store it raw.

    Args:
        session: Active SQLAlchemy session. Caller controls the transaction.
        ingestion_run: Parent ingestion run this fetch belongs to.
        player_code: KBO playerId.
        http: Configured HttpClient. Inject a mock client in tests.

    Returns:
        Tuple of (raw_payload_row, created).

    Raises:
        FetchError: If the request fails after retries.
    """
    url = build_kbo_hitter_basic_url(player_code=player_code)
    return _collect(session, ingestion_run, url, http)


def collect_kbo_pitcher_basic(
    *, session: Session, ingestion_run: IngestionRun, player_code: str, http: HttpClient
) -> tuple[RawIngestionPayload, bool]:
    """Fetch a player's KBO pitcher basic-record page and store it raw.

    Args:
        session: Active SQLAlchemy session. Caller controls the transaction.
        ingestion_run: Parent ingestion run this fetch belongs to.
        player_code: KBO playerId.
        http: Configured HttpClient. Inject a mock client in tests.

    Returns:
        Tuple of (raw_payload_row, created).

    Raises:
        FetchError: If the request fails after retries.
    """
    url = build_kbo_pitcher_basic_url(player_code=player_code)
    return _collect(session, ingestion_run, url, http)
