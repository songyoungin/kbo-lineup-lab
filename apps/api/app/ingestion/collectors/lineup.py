"""Collector that fetches the announced LG Twins starting lineup from Naver Sports.

Architecture note
-----------------
Collectors are responsible for fetching raw source data only. Parsing the JSON
into domain rows (batting order, player ids, positions) belongs to the
normalizer. Raw JSON is written to ``raw_ingestion_payloads`` via
:func:`~app.ingestion.raw_store.save_raw_payload` for replay without re-fetching.

Data source
-----------
The verified endpoint is the Naver api-gw preview API:
``https://api-gw.sports.naver.com/schedule/games/{naverGameId}/preview``. The
lineup lives at ``result.previewData.{home,away}TeamLineUp.fullLineUp``.

Lineup announcement detection
-----------------------------
The preview endpoint returns HTTP 200 regardless of whether the lineup has been
announced. We treat the presence of a non-empty ``fullLineUp`` list on either
side as the COLLECTED signal. An empty or absent list means the lineup has not
yet been announced and we return WAITING without creating a database row.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from typing import Any, Final

from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from app.ingestion.game_id import kbo_to_naver
from app.ingestion.http_client import HttpClient
from app.ingestion.raw_store import save_raw_payload
from app.ingestion.types import PayloadCategory
from app.models.snapshot import IngestionRun, RawIngestionPayload
from app.schemas.ingestion import RawPayloadCreate
from app.util.time import to_utc

__all__ = [
    "SOURCE_NAME",
    "NAVER_REFERER",
    "LineupStatus",
    "LineupCollectionResult",
    "build_naver_preview_url",
    "collect_lg_lineup",
]

SOURCE_NAME: Final = "naver_sports"
NAVER_REFERER: Final = "https://m.sports.naver.com/"

_NAVER_PREVIEW_URL: Final = "https://api-gw.sports.naver.com/schedule/games/{naver_id}/preview"
_KST: Final = timezone(timedelta(hours=9))


def build_naver_preview_url(*, kbo_game_id: str) -> str:
    """Build the Naver api-gw preview URL for a KBO game id.

    Args:
        kbo_game_id: KBO G_ID string (e.g. "20250514WOLG0").

    Returns:
        Fully qualified preview endpoint URL.

    Raises:
        ValueError: If kbo_game_id is not a valid KBO game id.
    """
    return _NAVER_PREVIEW_URL.format(naver_id=kbo_to_naver(kbo_game_id))


class LineupStatus(StrEnum):
    """Status of a single lineup poll attempt."""

    WAITING = "waiting"
    COLLECTED = "collected"


class LineupCollectionResult(BaseModel):
    """Outcome of a single lineup poll.

    Attributes:
        status: WAITING when lineup not yet announced; COLLECTED when available.
        raw_payload: The stored RawIngestionPayload row. Populated only when
            status is COLLECTED.
        fetched_at: When the source was polled (always populated).
        announced_at: Lineup announcement timestamp derived from the payload
            (informational); None when it cannot be derived.
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
    """Fetch the announced LG lineup for the game, or WAITING if not yet announced.

    The Naver preview endpoint returns 200 with a payload that contains either a
    populated ``fullLineUp`` list or empty lists. A non-empty list on either
    side is the COLLECTED signal.

    Args:
        session: Active SQLAlchemy session. Caller controls the transaction.
        ingestion_run: Parent ingestion run this fetch belongs to.
        game_id: KBO external game id (e.g. "20250514WOLG0").
        http: Configured HttpClient to use. Inject a mock client in tests.

    Returns:
        LineupCollectionResult with status WAITING (no DB row) or COLLECTED
        (raw payload row inserted or de-duplicated).

    Raises:
        FetchError: If the HTTP request fails after retries.
        ValueError: If game_id is not a valid KBO game id.
    """
    url = build_naver_preview_url(kbo_game_id=game_id)
    result = http.fetch(url, headers={"Referer": NAVER_REFERER})

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


def _preview_data(body: str) -> dict[str, Any]:
    """Parse the body and return ``result.previewData`` defensively.

    Args:
        body: Raw response body string.

    Returns:
        The previewData dict, or an empty dict if the body is unparseable or
        the expected nesting is absent.
    """
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        return {}
    if not isinstance(parsed, dict):
        return {}
    result = parsed.get("result") or {}
    preview = result.get("previewData") or {}
    return preview if isinstance(preview, dict) else {}


def _lineup_is_announced(body: str) -> bool:
    """Decide whether the response represents an announced lineup.

    A non-empty ``fullLineUp`` list under either ``homeTeamLineUp`` or
    ``awayTeamLineUp`` signals an announced lineup.

    Args:
        body: Raw response body string.

    Returns:
        True when at least one side has a non-empty lineup; False otherwise
        (including when the body is not valid JSON).
    """
    preview = _preview_data(body)
    home = (preview.get("homeTeamLineUp") or {}).get("fullLineUp") or []
    away = (preview.get("awayTeamLineUp") or {}).get("fullLineUp") or []
    return bool(home or away)


def _parse_announced_at(body: str) -> datetime | None:
    """Derive the announcement timestamp from ``gameInfo`` (KST -> UTC).

    Uses ``gdate`` (YYYYMMDD) and ``gtime`` (HH:MM, defaulting to "00:00").
    Informational only; the normalizer computes its own authoritative value.

    Args:
        body: Raw response body string.

    Returns:
        UTC-normalised datetime when derivable; None otherwise.
    """
    preview = _preview_data(body)
    game_info = preview.get("gameInfo") or {}
    gdate_raw = game_info.get("gdate")
    # Naver returns gdate as an int (e.g. 20250514); coerce to a YYYYMMDD string.
    gdate = str(gdate_raw) if isinstance(gdate_raw, int) else gdate_raw
    if not isinstance(gdate, str) or len(gdate) != 8:
        return None
    gtime = game_info.get("gtime")
    if not isinstance(gtime, str) or not gtime.strip():
        gtime = "00:00"
    try:
        local = datetime.strptime(f"{gdate} {gtime}", "%Y%m%d %H:%M").replace(tzinfo=_KST)
    except ValueError:
        return None
    return to_utc(local)
