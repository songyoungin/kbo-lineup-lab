"""Normalizer that parses Naver KBO schedule JSON into Game domain rows."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ingestion.game_id import naver_to_kbo
from app.ingestion.normalizers._shared import LG_TEAM_CODE
from app.models.game import Game
from app.models.snapshot import RawIngestionPayload
from app.models.team import Team

__all__ = ["ScheduleNormalizeResult", "normalize_schedule"]


@dataclass(frozen=True)
class ScheduleNormalizeResult:
    """Result of normalizing a schedule payload.

    Attributes:
        games_created: Number of newly inserted Game rows.
        games_existing: Number of Game rows skipped because they already exist.
        needs_review_reasons: Reasons that require manual review.
    """

    games_created: int
    games_existing: int
    needs_review_reasons: tuple[str, ...]


def normalize_schedule(
    session: Session,
    raw_payload: RawIngestionPayload,
) -> ScheduleNormalizeResult:
    """Parse a Naver schedule JSON payload and upsert Game rows for LG games.

    Expected payload shape (Naver api-gw.sports.naver.com/schedule/games):

    .. code-block:: json

        {
            "result": {
                "games": [
                    {
                        "gameId": "20250514WOLG02025",
                        "gameDate": "2025-05-14",
                        "homeTeamCode": "LG",
                        "awayTeamCode": "WO",
                        "stadium": "Jamsil"
                    }
                ]
            }
        }

    Only games where ``homeTeamCode`` or ``awayTeamCode`` is ``"LG"`` are
    inserted (single-team MVP). ``venue`` is populated from ``stadium``; if
    absent, ``Game.venue`` is left ``None`` (nullable column).

    Args:
        session: Active SQLAlchemy session. Caller controls the transaction.
        raw_payload: Row from ``raw_ingestion_payloads``.

    Returns:
        ScheduleNormalizeResult with counts of created/existing games and any
        review reasons.

    Raises:
        NotImplementedError: If the payload content_type is not JSON.
        ValueError: If the payload body is not valid JSON or is missing the
            expected ``result.games`` list.
    """
    if "json" not in raw_payload.content_type.lower():
        raise NotImplementedError(
            f"HTML schedule normalization not implemented in MVP; "
            f"content_type={raw_payload.content_type!r}"
        )

    try:
        body = json.loads(raw_payload.raw_body)
    except json.JSONDecodeError as exc:
        raise ValueError(f"schedule payload is not valid JSON: {exc}") from exc

    games_list = (body.get("result") or {}).get("games")
    if not isinstance(games_list, list):
        raise ValueError("schedule payload missing result.games list")

    games_created = 0
    games_existing = 0
    needs_review_reasons: list[str] = []

    for entry in games_list:
        home_code = entry.get("homeTeamCode")
        away_code = entry.get("awayTeamCode")
        if LG_TEAM_CODE not in (home_code, away_code):
            continue  # single-team MVP: only LG games
        naver_id = entry.get("gameId")
        game_date_str = entry.get("gameDate")
        if not naver_id or not game_date_str:
            needs_review_reasons.append(f"game entry missing gameId/gameDate: {entry!r}")
            continue
        try:
            external_id = naver_to_kbo(naver_id)
        except ValueError:
            needs_review_reasons.append(f"unparseable Naver gameId={naver_id!r}")
            continue

        existing = session.execute(
            select(Game).where(Game.external_id == external_id)
        ).scalar_one_or_none()
        if existing is not None:
            games_existing += 1
            continue

        home_team = session.execute(select(Team).where(Team.code == home_code)).scalar_one_or_none()
        away_team = session.execute(select(Team).where(Team.code == away_code)).scalar_one_or_none()
        if home_team is None or away_team is None:
            needs_review_reasons.append(
                f"game {external_id!r}: unknown team code(s) home={home_code!r} away={away_code!r}"
            )
            continue
        try:
            parsed_date = date.fromisoformat(game_date_str)
        except ValueError:
            needs_review_reasons.append(f"game {external_id!r}: bad gameDate={game_date_str!r}")
            continue

        session.add(
            Game(
                external_id=external_id,
                home_team_id=home_team.id,
                away_team_id=away_team.id,
                game_date=parsed_date,
                venue=entry.get("stadium"),
            )
        )
        session.flush()
        games_created += 1

    return ScheduleNormalizeResult(
        games_created=games_created,
        games_existing=games_existing,
        needs_review_reasons=tuple(needs_review_reasons),
    )
