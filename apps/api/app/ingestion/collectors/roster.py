"""Collector that fetches LG Twins roster data from KBO Official.

Architecture note
-----------------
Collectors are responsible for fetching raw source data only. Parsing the HTML
into domain rows (players, positions, etc.) belongs to the normalizer task
(Plan 17). Raw HTML is written to ``raw_ingestion_payloads`` via
:func:`~app.ingestion.raw_store.save_raw_payload` for replay without re-fetching.

LG filtering
------------
Filtering to LG Twins happens at the *URL level*: the ``teamId`` query
parameter is set to ``LG``.

URL accuracy warning
--------------------
The URL template below is *tentative*. Verify the live KBO Official site
before enabling scheduled ingestion runs. See docs/data-sources/kbo-source-matrix.md.
"""

from __future__ import annotations

from typing import Final

from sqlalchemy.orm import Session

from app.ingestion.collectors._constants import LG_TEAM_CODE
from app.ingestion.http_client import HttpClient
from app.ingestion.raw_store import save_raw_payload
from app.ingestion.types import PayloadCategory
from app.models.snapshot import IngestionRun, RawIngestionPayload
from app.schemas.ingestion import RawPayloadCreate

__all__ = ["LG_TEAM_CODE", "build_roster_url", "collect_lg_roster"]

# VERIFY before live use: navigate to https://www.koreabaseball.com/Player/Search.aspx
# and confirm the teamId parameter value for LG Twins and any additional query params.
ROSTER_URL_TEMPLATE: Final = (
    "https://www.koreabaseball.com/Player/Search.aspx?teamId={team_code}&year={year}"
)


def build_roster_url(*, year: int, team_code: str = LG_TEAM_CODE) -> str:
    """Construct the roster URL for a given season year and team.

    Args:
        year: KBO season year (e.g. 2026).
        team_code: KBO team identifier; defaults to ``"LG"``.

    Returns:
        Fully qualified URL string for the KBO Official roster page.
    """
    return ROSTER_URL_TEMPLATE.format(year=year, team_code=team_code)


def collect_lg_roster(
    *,
    session: Session,
    ingestion_run: IngestionRun,
    season: int,
    http: HttpClient,
) -> tuple[RawIngestionPayload, bool]:
    """Fetch the LG Twins roster for a given season and store the raw payload.

    The fetched URL is LG-filtered at the source via the ``teamId`` query
    parameter. The returned payload stores the full HTML page; the normalizer
    (Plan 17) extracts individual player rows.

    Args:
        session: Active SQLAlchemy session. Caller controls the transaction.
        ingestion_run: The parent ingestion run this fetch belongs to.
        season: KBO season year (e.g. 2026).
        http: Configured :class:`~app.ingestion.http_client.HttpClient` to use
            for the request. Inject a mock client in tests.

    Returns:
        Tuple of ``(raw_payload_row, created)``. ``created`` is ``False`` when
        an identical payload (same URL + body hash) was already stored.

    Raises:
        FetchError: If the HTTP request fails after retries.
    """
    url = build_roster_url(year=season, team_code=LG_TEAM_CODE)
    result = http.fetch(url)
    payload = RawPayloadCreate(
        ingestion_run_id=ingestion_run.id,
        category=PayloadCategory.ROSTER,
        source_name="kbo_official",
        source_url=result.url,
        fetched_at=result.fetched_at,
        content_type=result.content_type,
        raw_body=result.body,
    )
    return save_raw_payload(session, payload)
