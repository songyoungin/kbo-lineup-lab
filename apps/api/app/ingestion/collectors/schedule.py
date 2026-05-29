"""Schedule collector that fetches KBO game data from the Naver Sports API.

Architecture note
-----------------
Collectors are responsible for fetching raw source data only. Parsing the JSON
into domain tables (games, schedule rows, etc.) belongs to the normalizer layer.
This module writes raw JSON to ``raw_ingestion_payloads`` via the shared
:func:`~app.ingestion.raw_store.save_raw_payload` helper so that the normalizer
can replay against any parser version without re-fetching.

Data source
-----------
Primary: ``api-gw.sports.naver.com/schedule/games`` (JSON endpoint). The full
KBO league schedule for the requested date range is returned; the normalizer
filters down to LG Twins games.

Date range
----------
``date_from`` and ``date_to`` bound the inclusive date range sent to Naver.
For MVP the collector fetches a single day (``date_from == date_to``), but any
range is supported.
"""

from __future__ import annotations

from datetime import date
from typing import Final

from sqlalchemy.orm import Session

from app.ingestion.http_client import HttpClient
from app.ingestion.raw_store import save_raw_payload
from app.ingestion.types import PayloadCategory
from app.models.snapshot import IngestionRun, RawIngestionPayload
from app.schemas.ingestion import RawPayloadCreate

__all__ = ["build_naver_schedule_url", "collect_lg_schedule"]

NAVER_SCHEDULE_URL: Final = (
    "https://api-gw.sports.naver.com/schedule/games"
    "?fields=basic&upperCategoryId=kbaseball&categoryId=kbo&fromDate={frm}&toDate={to}"
)
NAVER_REFERER: Final = "https://m.sports.naver.com/"


def build_naver_schedule_url(*, date_from: date, date_to: date) -> str:
    """Naver KBO schedule URL for an inclusive date range (full league, not LG-only).

    Args:
        date_from: Inclusive start of the date range.
        date_to: Inclusive end of the date range.

    Returns:
        Fully qualified URL string for the Naver KBO schedule endpoint.
    """
    return NAVER_SCHEDULE_URL.format(frm=date_from.isoformat(), to=date_to.isoformat())


def collect_lg_schedule(
    *,
    session: Session,
    ingestion_run: IngestionRun,
    date_from: date,
    date_to: date,
    http: HttpClient,
) -> tuple[RawIngestionPayload, bool]:
    """Fetch the KBO schedule for [date_from, date_to] from Naver and store raw JSON.

    The payload holds all KBO games for the range; the normalizer filters to LG.

    Args:
        session: Active SQLAlchemy session. Caller controls the transaction.
        ingestion_run: The parent ingestion run this fetch belongs to.
        date_from: Inclusive start of the date range of interest.
        date_to: Inclusive end of the date range of interest.
        http: Configured :class:`~app.ingestion.http_client.HttpClient` to use
            for the request. Inject a mock client in tests.

    Returns:
        Tuple of ``(raw_payload_row, created)``. ``created`` is ``False`` when
        an identical payload (same URL + body hash) was already stored.

    Raises:
        ValueError: If date_from is later than date_to.
        FetchError: If the request fails after retries.
    """
    if date_from > date_to:
        raise ValueError(f"date_from ({date_from}) must not be later than date_to ({date_to})")
    url = build_naver_schedule_url(date_from=date_from, date_to=date_to)
    result = http.fetch(url, headers={"Referer": NAVER_REFERER})
    payload = RawPayloadCreate(
        ingestion_run_id=ingestion_run.id,
        category=PayloadCategory.SCHEDULE,
        source_name="naver_sports",
        source_url=result.url,
        fetched_at=result.fetched_at,
        content_type=result.content_type,
        raw_body=result.body,
    )
    return save_raw_payload(session, payload)
