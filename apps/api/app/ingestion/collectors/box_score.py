"""Collector that fetches the LG Twins box score from the Naver record endpoint.

Architecture note
-----------------
Collectors fetch raw source data only. Parsing the box score into per-player
stat rows belongs to the normalizer. Raw JSON is written to
``raw_ingestion_payloads`` via :func:`~app.ingestion.raw_store.save_raw_payload`
for replay without re-fetching.

Data source
-----------
The verified endpoint is the Naver api-gw record API:
``https://api-gw.sports.naver.com/schedule/games/{naverGameId}/record``. The box
score lives at ``result.recordData.battersBoxscore.{home,away}`` and the final
score at ``result.recordData.scoreBoard.rheb``.

Postgame detection
------------------
The record endpoint returns HTTP 200 regardless of whether the game has finished.
We treat the presence of a non-empty ``battersBoxscore`` list on either side as
the COLLECTED signal; box-score data presence is a more robust marker than the
numeric ``statusCode``. An empty/absent list means the game is not yet final and
we return WAITING without creating a database row.
"""

from __future__ import annotations

import json
from datetime import datetime
from enum import StrEnum
from typing import Any, Final

from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from app.ingestion.collectors.lineup import NAVER_REFERER
from app.ingestion.game_id import kbo_to_naver
from app.ingestion.http_client import HttpClient
from app.ingestion.raw_store import save_raw_payload
from app.ingestion.types import PayloadCategory
from app.models.snapshot import IngestionRun, RawIngestionPayload
from app.schemas.ingestion import RawPayloadCreate

__all__ = [
    "SOURCE_NAME",
    "BoxScoreStatus",
    "FinalScore",
    "BoxScoreCollectionResult",
    "build_naver_record_url",
    "collect_lg_box_score",
]

SOURCE_NAME: Final = "naver_sports"

_NAVER_RECORD_URL: Final = "https://api-gw.sports.naver.com/schedule/games/{naver_id}/record"


def build_naver_record_url(*, kbo_game_id: str) -> str:
    """Build the Naver api-gw record URL for a KBO game id.

    Args:
        kbo_game_id: KBO G_ID string (e.g. "20250514WOLG0").

    Returns:
        Fully qualified record endpoint URL.

    Raises:
        ValueError: If kbo_game_id is not a valid KBO game id.
    """
    return _NAVER_RECORD_URL.format(naver_id=kbo_to_naver(kbo_game_id))


class BoxScoreStatus(StrEnum):
    """Status of a single box score poll attempt."""

    WAITING = "waiting"
    COLLECTED = "collected"


class FinalScore(BaseModel):
    """Final game score parsed from the box score payload.

    Attributes:
        home_runs: Runs scored by the home team.
        away_runs: Runs scored by the away team.
    """

    model_config = ConfigDict(frozen=True)

    home_runs: int
    away_runs: int


class BoxScoreCollectionResult(BaseModel):
    """Outcome of a single box score poll.

    Attributes:
        status: WAITING when the game is not yet final; COLLECTED when box-score
            data is available.
        raw_payload: The stored RawIngestionPayload row. Populated only when
            status is COLLECTED.
        fetched_at: When the source was polled (always populated).
        final_score: Home/away run totals parsed from the response body; None when
            the source does not include them. Authoritative only when
            status is COLLECTED; when status is WAITING a non-None value reflects
            a LIVE/partial mid-game score (Naver populates scoreBoard.rheb during
            play), not a confirmed final score.
        created: Whether the raw payload row was newly inserted. Always False
            when status is WAITING.
    """

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    status: BoxScoreStatus
    raw_payload: RawIngestionPayload | None = None
    fetched_at: datetime
    final_score: FinalScore | None = None
    created: bool = False


def collect_lg_box_score(
    *,
    session: Session,
    ingestion_run: IngestionRun,
    game_id: str,
    http: HttpClient,
) -> BoxScoreCollectionResult:
    """Fetch the LG game's box score; return WAITING if the game isn't final yet.

    Polls the Naver record endpoint. When the response carries box-score data
    (a non-empty ``battersBoxscore`` list on either side), the raw body is
    persisted via the raw store and COLLECTED is returned. Otherwise WAITING is
    returned and no database row is created.

    Args:
        session: Active SQLAlchemy session. Caller controls the transaction.
        ingestion_run: Parent ingestion run this fetch belongs to.
        game_id: KBO external game id (e.g. "20250514WOLG0").
        http: Configured HttpClient to use. Inject a mock client in tests.

    Returns:
        BoxScoreCollectionResult with status WAITING (no DB row) or COLLECTED
        (raw payload row inserted or de-duplicated).

    Raises:
        FetchError: If the HTTP request fails after retries.
        ValueError: If game_id is not a valid KBO game id.
    """
    url = build_naver_record_url(kbo_game_id=game_id)
    result = http.fetch(url, headers={"Referer": NAVER_REFERER})

    # Parse the body once and reuse for both score parsing and the final gate.
    record = _record_data(result.body)
    final_score = _parse_final_score(record)
    if not _game_is_final(record):
        return BoxScoreCollectionResult(
            status=BoxScoreStatus.WAITING,
            raw_payload=None,
            fetched_at=result.fetched_at,
            final_score=final_score,
            created=False,
        )

    payload = RawPayloadCreate(
        ingestion_run_id=ingestion_run.id,
        category=PayloadCategory.BOX_SCORE,
        source_name=SOURCE_NAME,
        source_url=result.url,
        fetched_at=result.fetched_at,
        content_type=result.content_type,
        raw_body=result.body,
    )
    row, created = save_raw_payload(session, payload)
    return BoxScoreCollectionResult(
        status=BoxScoreStatus.COLLECTED,
        raw_payload=row,
        fetched_at=result.fetched_at,
        final_score=final_score,
        created=created,
    )


def _record_data(body: str) -> dict[str, Any]:
    """Parse the body and return ``result.recordData`` defensively.

    Args:
        body: Raw response body string.

    Returns:
        The recordData dict, or an empty dict if the body is unparseable or the
        expected nesting is absent.
    """
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        return {}
    if not isinstance(parsed, dict):
        return {}
    result = parsed.get("result") or {}
    record = result.get("recordData") or {}
    return record if isinstance(record, dict) else {}


def _game_is_final(record: dict[str, Any]) -> bool:
    """Decide whether the record represents a finalised game.

    A non-empty ``battersBoxscore`` list under either ``home`` or ``away`` is the
    robust signal that the box score is available.

    Note: this collector gate accepts EITHER side being populated, whereas the
    normalizer gates on the LG side specifically. A real completed KBO game always
    has batters on both sides so they agree; in the pathological case where only
    the opponent side is populated, the payload is stored COLLECTED yet the
    normalizer returns ``skipped_not_final`` — safe on replay.

    Args:
        record: The parsed ``result.recordData`` dict (empty if unparseable).

    Returns:
        True when at least one side has a non-empty batters list; False otherwise.
    """
    batters = record.get("battersBoxscore") or {}
    if not isinstance(batters, dict):
        return False
    home = batters.get("home") or []
    away = batters.get("away") or []
    return bool(home or away)


def _parse_final_score(record: dict[str, Any]) -> FinalScore | None:
    """Extract home/away runs from ``scoreBoard.rheb`` when present.

    Naver populates ``scoreBoard.rheb`` during live play, so a non-None result is
    a confirmed final score only when the caller has also confirmed the game is
    final (see ``_game_is_final``).

    Args:
        record: The parsed ``result.recordData`` dict (empty if unparseable).

    Returns:
        FinalScore when both run totals are present as ints; None otherwise.
    """
    rheb = (record.get("scoreBoard") or {}).get("rheb") or {}
    if not isinstance(rheb, dict):
        return None
    home = (rheb.get("home") or {}).get("r")
    away = (rheb.get("away") or {}).get("r")
    if isinstance(home, int) and isinstance(away, int):
        return FinalScore(home_runs=home, away_runs=away)
    return None
