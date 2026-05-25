"""Collector that fetches the LG Twins box score from KBO Official after a game finishes.

Architecture note
-----------------
Collectors are responsible for fetching raw source data only. Parsing the HTML
into per-player stat rows belongs to the normalizer task (Plan 17). Raw HTML is
written to ``raw_ingestion_payloads`` via
:func:`~app.ingestion.raw_store.save_raw_payload` for replay without re-fetching.

Postgame detection
------------------
The KBO Official box score page is only meaningful after the game ends. We treat
a ``gameStatus=FINAL`` field in a JSON body, or the presence of a ``FINAL`` /
``경기종료`` substring in HTML, as the COLLECTED signal. When neither signal is
present we return WAITING so the caller can poll until the game concludes. No
database row is created in the WAITING case.

URL accuracy warning
--------------------
The URL template below is *tentative*. Verify the live KBO Official page before
enabling scheduled ingestion runs. See docs/data-sources/kbo-source-matrix.md.

# VERIFY before live use: confirm that
#   https://www.koreabaseball.com/Schedule/Boxscore.aspx?gameId={game_id}
# returns HTML (or JSON) that contains a 'FINAL' / '경기종료' marker when the
# game has ended, and that 'gameStatus' key is present in any JSON variant.
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

__all__ = [
    "SOURCE_NAME",
    "BOX_SCORE_URL_TEMPLATE",
    "BoxScoreStatus",
    "FinalScore",
    "BoxScoreCollectionResult",
    "collect_lg_box_score",
]

SOURCE_NAME: Final = "kbo_official"

# VERIFY before live use; KBO Official box score page.
BOX_SCORE_URL_TEMPLATE: Final = (
    "https://www.koreabaseball.com/Schedule/Boxscore.aspx?gameId={game_id}"
)


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
        status: WAITING when game not yet final; COLLECTED when hitter data is available.
        raw_payload: The stored RawIngestionPayload row. Populated only when
            status is COLLECTED.
        fetched_at: When the source was polled (always populated).
        final_score: Home/away run totals parsed from the response body; None when
            the source does not include them or the game is not yet final.
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

    Polls the KBO Official box score page. When the response signals a final game
    (``gameStatus=FINAL`` in JSON, or ``FINAL`` / ``경기종료`` substring in HTML),
    the raw body is persisted via the Plan 12 raw store and COLLECTED is returned.
    Otherwise WAITING is returned and no database row is created.

    Args:
        session: Active SQLAlchemy session. Caller controls the transaction.
        ingestion_run: Parent ingestion run this fetch belongs to.
        game_id: KBO external game id (e.g. "20260415LGDOO").
        http: Configured HttpClient to use for the request. Inject a mock
            client in tests.

    Returns:
        BoxScoreCollectionResult with status WAITING (no DB row) or COLLECTED
        (raw payload row inserted or de-duplicated).

    Raises:
        FetchError: If the HTTP request fails after retries.
    """
    url = BOX_SCORE_URL_TEMPLATE.format(game_id=game_id)
    result = http.fetch(url)

    final_score = _parse_final_score(result.body)
    if not _game_is_final(result.body):
        return BoxScoreCollectionResult(
            status=BoxScoreStatus.WAITING,
            raw_payload=None,
            fetched_at=result.fetched_at,
            final_score=final_score,  # may still be partial / scheduled
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


def _game_is_final(body: str) -> bool:
    """Decide whether the body represents a finalised game.

    For KBO Official JSON the ``gameStatus`` field is checked. For HTML pages
    a substring scan for ``FINAL`` or ``경기종료`` is used as a minimal
    placeholder until real samples are captured and the exact DOM structure
    is known.

    # VERIFY before live use: confirm the exact JSON field name and HTML marker
    # against a captured sample from a finished KBO Official box score page.

    Args:
        body: Raw response body string.

    Returns:
        True when the body signals a finished game; False otherwise
        (including for empty bodies or in-progress games).
    """
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        # KBO returns HTML; do a minimal substring check as a placeholder until
        # real samples land.  # VERIFY before live use
        return "FINAL" in body or "경기종료" in body
    status = parsed.get("gameStatus")
    return isinstance(status, str) and status.upper() == "FINAL"


def _parse_final_score(body: str) -> FinalScore | None:
    """Extract home/away runs from the source body if present.

    Expects ``homeRuns`` and ``awayRuns`` integer fields in a JSON body. The
    exact field names must be VERIFIED once real samples are captured from the
    KBO Official box score page.

    # VERIFY before live use: confirm field names against a real KBO Official
    # JSON response.

    Args:
        body: Raw response body string.

    Returns:
        FinalScore when both integer fields are present; None otherwise
        (including for HTML bodies and JSON without the expected fields).
    """
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    home = parsed.get("homeRuns")
    away = parsed.get("awayRuns")
    if isinstance(home, int) and isinstance(away, int):
        return FinalScore(home_runs=home, away_runs=away)
    return None
