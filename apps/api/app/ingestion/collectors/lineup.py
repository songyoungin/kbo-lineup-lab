"""Collector that fetches the announced LG Twins starting lineup from Naver Sports.

Architecture note
-----------------
Collectors are responsible for fetching raw source data only. Parsing the JSON
into domain rows (batting order, player ids, positions) belongs to the normalizer
task (Plan 17). Raw JSON is written to ``raw_ingestion_payloads`` via
:func:`~app.ingestion.raw_store.save_raw_payload` for replay without re-fetching.

Lineup announcement detection
------------------------------
The Naver mobile preview endpoint returns HTTP 200 regardless of whether the
lineup has been announced. We treat the presence of a non-empty ``awayLineup``
or ``homeLineup`` array in the JSON body as the COLLECTED signal. An empty or
absent array means the lineup has not yet been announced and we return WAITING.
No database row is created in the WAITING case — the caller is expected to poll.

URL accuracy warning
--------------------
The URL template below is *tentative*. Verify the live Naver mobile sports API
before enabling scheduled ingestion runs. See docs/data-sources/kbo-source-matrix.md.

# VERIFY before live use: confirm that
#   https://m.sports.naver.com/api/game/{game_id}/preview
# returns JSON with "awayLineup" / "homeLineup" arrays when announced, and
# "lineupAnnouncedAt" (ISO-8601 string) when the timestamp is available.
"""

from __future__ import annotations

import json
from datetime import datetime
from enum import StrEnum
from typing import Final

from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from app.ingestion.http_client import HttpClient
from app.ingestion.raw_store import save_raw_payload
from app.ingestion.types import PayloadCategory
from app.models.snapshot import IngestionRun, RawIngestionPayload
from app.schemas.ingestion import RawPayloadCreate
from app.util.time import to_utc

__all__ = [
    "SOURCE_NAME",
    "LINEUP_URL_TEMPLATE",
    "LineupStatus",
    "LineupCollectionResult",
    "collect_lg_lineup",
]

SOURCE_NAME: Final = "naver_sports"

# VERIFY before live use; Naver mobile sports API.
LINEUP_URL_TEMPLATE: Final = "https://m.sports.naver.com/api/game/{game_id}/preview"


class LineupStatus(StrEnum):
    """Status of a single lineup poll attempt."""

    WAITING = "waiting"
    COLLECTED = "collected"


class LineupCollectionResult(BaseModel):
    """Outcome of a single lineup poll.

    Attributes:
        status: WAITING when lineup not yet announced; COLLECTED when data is available.
        raw_payload: The stored RawIngestionPayload row. Populated only when
            status is COLLECTED.
        fetched_at: When the source was polled (always populated).
        announced_at: Official lineup announcement timestamp parsed from the
            response body; None when the source does not include it.
        created: Whether the raw payload row was newly inserted. Always False
            when status is WAITING.
    """

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    status: LineupStatus
    raw_payload: RawIngestionPayload | None = None
    fetched_at: datetime
    announced_at: datetime | None = None
    created: bool = False


def collect_lg_lineup(
    *,
    session: Session,
    ingestion_run: IngestionRun,
    game_id: str,
    http: HttpClient,
) -> LineupCollectionResult:
    """Fetch the announced LG lineup for the game, or return WAITING if not yet announced.

    The Naver preview endpoint returns 200 with a payload that contains either a
    populated lineup section or a 'lineup not yet announced' marker. We treat the
    presence of a non-empty starting lineup array as the COLLECTED signal.

    Args:
        session: Active SQLAlchemy session. Caller controls the transaction.
        ingestion_run: Parent ingestion run this fetch belongs to.
        game_id: KBO external game id (e.g. "20260415LGDOO").
        http: Configured HttpClient to use for the request. Inject a mock
            client in tests.

    Returns:
        LineupCollectionResult with status WAITING (no DB row) or COLLECTED
        (raw payload row inserted or de-duplicated).

    Raises:
        FetchError: If the HTTP request fails after retries.
    """
    url = LINEUP_URL_TEMPLATE.format(game_id=game_id)
    result = http.fetch(url)

    announced_at = _parse_announced_at(result.body)
    if not _lineup_is_announced(result.body):
        return LineupCollectionResult(
            status=LineupStatus.WAITING,
            raw_payload=None,
            fetched_at=result.fetched_at,
            announced_at=announced_at,
            created=False,
        )

    payload = RawPayloadCreate(
        ingestion_run_id=ingestion_run.id,
        category=PayloadCategory.LINEUP,
        source_name=SOURCE_NAME,
        source_url=result.url,
        fetched_at=result.fetched_at,
        content_type=result.content_type,
        raw_body=result.body,
    )
    row, created = save_raw_payload(session, payload)
    return LineupCollectionResult(
        status=LineupStatus.COLLECTED,
        raw_payload=row,
        fetched_at=result.fetched_at,
        announced_at=announced_at,
        created=created,
    )


def _lineup_is_announced(body: str) -> bool:
    """Decide whether the response body represents an announced lineup.

    For Naver mobile preview JSON, the presence of a non-empty 'awayLineup' or
    'homeLineup' array signals an announced lineup. The exact shape must be
    VERIFIED once we capture a real sample.

    Args:
        body: Raw response body string.

    Returns:
        True when at least one lineup array is non-empty; False otherwise
        (including when the body is not valid JSON).
    """
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        return False  # treat unparseable bodies as waiting; future hardening: fail loud
    away = parsed.get("awayLineup") or []
    home = parsed.get("homeLineup") or []
    return bool(away or home)


def _parse_announced_at(body: str) -> datetime | None:
    """Extract the lineup announcement timestamp from the source body, when present.

    Expects an ISO-8601 string at ``body["lineupAnnouncedAt"]``. The exact
    field name must be VERIFIED once we capture a real Naver response sample.

    Args:
        body: Raw response body string.

    Returns:
        UTC-normalised datetime when the field is present and parseable;
        None otherwise.
    """
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        return None
    raw = parsed.get("lineupAnnouncedAt")
    if not isinstance(raw, str):
        return None
    try:
        return to_utc(datetime.fromisoformat(raw))
    except ValueError:
        return None
