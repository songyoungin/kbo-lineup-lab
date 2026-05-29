"""Collector that fetches LG Twins hitter stat payloads.

Architecture note
-----------------
Collectors are responsible for fetching raw source data only. Parsing the JSON
into domain rows belongs to the normalizer. Raw payloads are written to
``raw_ingestion_payloads`` via :func:`~app.ingestion.raw_store.save_raw_payload`
for replay without re-fetching.

Season stats source
-------------------
Season stats (``collect_lg_hitter_season_stats``) are fetched from the same
Naver Sports preview endpoint used by the lineup collector.  The preview's
``result.previewData.homeTopPlayer`` / ``homeStarter`` nodes embed
``currentSeasonStats`` for the featured hitter and starting pitcher
respectively.  Source identifier: ``naver_sports`` (``NAVER_SOURCE_NAME``).

Recent / split stats source
----------------------------
Rolling-window recent stats and handedness-split stats still come from STATIZ
(``SOURCE_NAME = "statiz"``).  Those collectors are unchanged.

Split availability
------------------
STATIZ exposes handedness splits (vs LHP / vs RHP) in its team batting tables.
``SOURCE_SUPPORTS_HANDEDNESS_SPLITS`` encodes this at import time.  When it is
``False``, :func:`collect_lg_hitter_split_stats` writes a marker payload with
``content_type="application/x-source-metadata+json"`` so the normalizer can
explicitly skip split scoring rather than silently treating missing data as
zero plate-appearances.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, date, datetime
from typing import Final

from sqlalchemy.orm import Session

from app.ingestion.collectors._constants import LG_TEAM_CODE
from app.ingestion.collectors.lineup import NAVER_REFERER, build_naver_preview_url
from app.ingestion.http_client import HttpClient
from app.ingestion.raw_store import save_raw_payload
from app.ingestion.types import PayloadCategory
from app.models.snapshot import IngestionRun, RawIngestionPayload
from app.schemas.ingestion import RawPayloadCreate

__all__ = [
    "NAVER_SOURCE_NAME",
    "SOURCE_NAME",
    "SOURCE_SUPPORTS_HANDEDNESS_SPLITS",
    "collect_lg_hitter_recent_stats",
    "collect_lg_hitter_season_stats",
    "collect_lg_hitter_split_stats",
]

# Source name for the Naver Sports preview endpoint (season stats).
NAVER_SOURCE_NAME: Final = "naver_sports"

# Source name retained for the STATIZ-backed recent / split collectors.
SOURCE_NAME: Final = "statiz"

# VERIFY before live use: navigate to https://statiz.sporki.com/team/?team=LG
# and confirm exact query parameter names for year, recent window, and asof date.
RECENT_STATS_URL_TEMPLATE: Final = (
    "https://statiz.sporki.com/team/?team={team_code}&year={year}&recent={days}&asof={as_of}"
)
SPLIT_STATS_URL_TEMPLATE: Final = (
    "https://statiz.sporki.com/team/?team={team_code}&year={year}&split=handedness"
)

# STATIZ exposes LHP/RHP splits per the source matrix (Plan 11). Set to False
# if the source stops exposing splits; the collector will then write a marker
# payload instead of attempting a real fetch.
SOURCE_SUPPORTS_HANDEDNESS_SPLITS: Final = True

DEFAULT_RECENT_WINDOWS: Final[tuple[int, ...]] = (14, 30)


def collect_lg_hitter_season_stats(
    *,
    session: Session,
    ingestion_run: IngestionRun,
    game_id: str,
    http: HttpClient,
) -> tuple[RawIngestionPayload, bool]:
    """Fetch LG Twins season stats for a game day from the Naver preview endpoint.

    The Naver preview payload for a given game embeds ``currentSeasonStats`` for
    both the featured hitter (``homeTopPlayer``) and the starting pitcher
    (``homeStarter``).  These fields are parsed by the player-stats normalizer.

    The same preview JSON is also used by the lineup collector; fetching it here
    is independent and stores the payload under ``category=PLAYER_STATS``.

    Args:
        session: Active SQLAlchemy session. Caller controls the transaction.
        ingestion_run: Parent ingestion run this fetch belongs to.
        game_id: KBO external game id (e.g. ``"20250514WOLG0"``).
        http: Configured HttpClient to use. Inject a mock client in tests.

    Returns:
        Tuple of ``(raw_payload_row, created)``. ``created`` is ``False`` when
        an identical payload (same URL + body hash) was already stored.

    Raises:
        FetchError: If the HTTP request fails after retries.
        ValueError: If ``game_id`` is not a valid KBO game id.
    """
    url = build_naver_preview_url(kbo_game_id=game_id)
    result = http.fetch(url, headers={"Referer": NAVER_REFERER})
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


def collect_lg_hitter_recent_stats(
    *,
    session: Session,
    ingestion_run: IngestionRun,
    as_of_date: date,
    http: HttpClient,
    windows: Sequence[int] = DEFAULT_RECENT_WINDOWS,
) -> list[tuple[RawIngestionPayload, bool]]:
    """Fetch one payload per rolling window (e.g. last 14 days, last 30 days).

    Each window maps to a separate payload row so downstream consumers can
    independently select the window that matches their scoring formula.

    Args:
        session: Active SQLAlchemy session. Caller controls the transaction.
        ingestion_run: Parent ingestion run this fetch belongs to.
        as_of_date: Reference date for the rolling window (inclusive end).
        http: Configured :class:`~app.ingestion.http_client.HttpClient` to use
            for the request. Inject a mock client in tests.
        windows: Non-empty sequence of rolling-window sizes in days.
            Defaults to ``(14, 30)`` per Plan 05's scoring model.

    Returns:
        List of ``(raw_payload_row, created)`` tuples in window order.

    Raises:
        ValueError: If ``windows`` is empty.
        FetchError: If any HTTP request fails after retries.
    """
    if not windows:
        raise ValueError("windows must not be empty")
    results: list[tuple[RawIngestionPayload, bool]] = []
    for window_days in windows:
        url = RECENT_STATS_URL_TEMPLATE.format(
            team_code=LG_TEAM_CODE,
            year=as_of_date.year,
            days=window_days,
            as_of=as_of_date.isoformat(),
        )
        result = http.fetch(url)
        payload = RawPayloadCreate(
            ingestion_run_id=ingestion_run.id,
            category=PayloadCategory.PLAYER_STATS,
            source_name=SOURCE_NAME,
            source_url=result.url,
            fetched_at=result.fetched_at,
            content_type=result.content_type,
            raw_body=result.body,
        )
        results.append(save_raw_payload(session, payload))
    return results


def collect_lg_hitter_split_stats(
    *,
    session: Session,
    ingestion_run: IngestionRun,
    season: int,
    http: HttpClient,
) -> tuple[RawIngestionPayload, bool]:
    """Fetch LG Twins handedness-split stats, or record a marker payload when unavailable.

    When ``SOURCE_SUPPORTS_HANDEDNESS_SPLITS`` is ``True``, the STATIZ split
    URL is fetched and stored verbatim. When ``False``, no HTTP request is made;
    instead a JSON marker payload with ``content_type="application/x-source-metadata+json"``
    is saved. The marker body contains ``"supported": false`` so the normalizer
    (Plan 17) can explicitly skip split scoring rather than fabricating zero-PA
    splits for missing data.

    Args:
        session: Active SQLAlchemy session. Caller controls the transaction.
        ingestion_run: Parent ingestion run this fetch belongs to.
        season: KBO season year (e.g. 2026).
        http: Configured :class:`~app.ingestion.http_client.HttpClient` to use
            for the request. Inject a mock client in tests. Not called when
            ``SOURCE_SUPPORTS_HANDEDNESS_SPLITS`` is ``False``.

    Returns:
        Tuple of ``(raw_payload_row, created)``. ``created`` is ``False`` when
        an identical payload was already stored.

    Raises:
        FetchError: If the HTTP request fails after retries (only when supported).
    """
    if not SOURCE_SUPPORTS_HANDEDNESS_SPLITS:
        marker_body = json.dumps(
            {
                "schema_version": 1,
                "category": "player_stats",
                "subkind": "splits",
                "supported": False,
                "reason": "Source does not expose handedness splits at this time.",
                "source_name": SOURCE_NAME,
                "team_code": LG_TEAM_CODE,
                "season": season,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        payload = RawPayloadCreate(
            ingestion_run_id=ingestion_run.id,
            category=PayloadCategory.PLAYER_STATS,
            source_name=SOURCE_NAME,
            source_url=f"marker://no-splits/{LG_TEAM_CODE}/{season}",
            fetched_at=datetime.now(UTC),
            content_type="application/x-source-metadata+json",
            raw_body=marker_body,
        )
        return save_raw_payload(session, payload)

    url = SPLIT_STATS_URL_TEMPLATE.format(team_code=LG_TEAM_CODE, year=season)
    result = http.fetch(url)
    payload = RawPayloadCreate(
        ingestion_run_id=ingestion_run.id,
        category=PayloadCategory.PLAYER_STATS,
        source_name=SOURCE_NAME,
        source_url=result.url,
        fetched_at=result.fetched_at,
        content_type=result.content_type,
        raw_body=result.body,
    )
    return save_raw_payload(session, payload)
