"""Collector that fetches LG Twins schedule data from KBO Official.

Architecture note
-----------------
Collectors are responsible for fetching raw source data only. Parsing the HTML
into domain tables (games, schedule rows, etc.) belongs to the normalizer task
(Plan 17). This module writes raw HTML to ``raw_ingestion_payloads`` via the
shared :func:`~app.ingestion.raw_store.save_raw_payload` helper so that the
normalizer can replay against any parser version without re-fetching.

LG filtering
------------
Filtering to LG Twins happens at the *URL level*: the ``teamId`` query
parameter is set to ``LG``. The KBO Official site returns only LG schedule
entries for that team code. The normalizer still needs to ignore any
incidental entries, but the collector does not post-filter.

Date range (MVP)
----------------
``date_from`` and ``date_to`` bound the range of interest. For MVP the KBO
Official schedule page returns a full season view, so the collector fetches
the season identified by ``date_from.year``. Finer date filtering belongs in
the normalizer (Plan 17).

URL accuracy warning
--------------------
The URL template below is *tentative*. Verify the live KBO Official site
before enabling scheduled ingestion runs. See docs/data-sources/kbo-source-matrix.md.
"""

from __future__ import annotations

from datetime import date
from typing import Final

from sqlalchemy.orm import Session

from app.ingestion.collectors._constants import LG_TEAM_CODE
from app.ingestion.http_client import HttpClient
from app.ingestion.raw_store import save_raw_payload
from app.ingestion.types import PayloadCategory
from app.models.snapshot import IngestionRun, RawIngestionPayload
from app.schemas.ingestion import RawPayloadCreate

__all__ = ["LG_TEAM_CODE", "build_schedule_url", "collect_lg_schedule"]

# VERIFY before live use: navigate to https://www.koreabaseball.com/Schedule/Schedule.aspx
# and confirm the teamId parameter value for LG Twins and the seasonId/seriesId format.
SCHEDULE_URL_TEMPLATE: Final = (
    "https://www.koreabaseball.com/Schedule/Schedule.aspx"
    "?seriesId=0&seasonId={year}&teamId={team_code}"
)


def build_schedule_url(*, year: int, team_code: str = LG_TEAM_CODE) -> str:
    """Construct the schedule URL for a given year and team.

    Args:
        year: KBO season year (e.g. 2026).
        team_code: KBO team identifier; defaults to ``"LG"``.

    Returns:
        Fully qualified URL string for the KBO Official schedule page.
    """
    return SCHEDULE_URL_TEMPLATE.format(year=year, team_code=team_code)


def collect_lg_schedule(
    *,
    session: Session,
    ingestion_run: IngestionRun,
    date_from: date,
    date_to: date,
    http: HttpClient,
) -> tuple[RawIngestionPayload, bool]:
    """Fetch the LG Twins schedule for the season covering ``[date_from, date_to]``.

    The fetched URL is LG-filtered at the source via the ``teamId`` query
    parameter. The returned payload stores the full HTML page; the normalizer
    (Plan 17) extracts individual game rows. ``date_from`` and ``date_to``
    are recorded as metadata — for MVP the KBO page returns a full-season
    view, so date range enforcement is deferred to the normalizer.

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
        ValueError: If ``date_from`` is later than ``date_to``.
        FetchError: If the HTTP request fails after retries.
    """
    if date_from > date_to:
        raise ValueError(f"date_from ({date_from}) must not be later than date_to ({date_to})")
    year = date_from.year
    url = build_schedule_url(year=year, team_code=LG_TEAM_CODE)
    result = http.fetch(url)
    payload = RawPayloadCreate(
        ingestion_run_id=ingestion_run.id,
        category=PayloadCategory.SCHEDULE,
        source_name="kbo_official",
        source_url=result.url,
        fetched_at=result.fetched_at,
        content_type=result.content_type,
        raw_body=result.body,
    )
    return save_raw_payload(session, payload)
