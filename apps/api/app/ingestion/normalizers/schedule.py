"""KBO 일정 raw 페이로드를 Game 도메인 행으로 정규화한다."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.game import Game
from app.models.snapshot import RawIngestionPayload
from app.models.team import Team

__all__ = ["ScheduleNormalizeResult", "normalize_schedule"]


@dataclass(frozen=True)
class ScheduleNormalizeResult:
    """일정 정규화 결과.

    Attributes:
        games_created: 새로 삽입된 Game 행 수.
        games_existing: 이미 존재하여 건너뛴 Game 행 수.
        needs_review_reasons: 검토가 필요한 이유 목록.
    """

    games_created: int
    games_existing: int
    needs_review_reasons: tuple[str, ...]


def normalize_schedule(
    session: Session,
    raw_payload: RawIngestionPayload,
) -> ScheduleNormalizeResult:
    """raw 일정 페이로드(KBO Official)를 파싱하여 Game 행을 upsert한다.

    기대하는 페이로드 형태 (MVP 플레이스홀더 — 실제 샘플로 검증 필요):
    JSON:
        {
            "games": [
                {"external_id": "...", "game_date": "YYYY-MM-DD",
                 "home_team_code": "LG", "away_team_code": "...", "venue": "..."},
                ...
            ]
        }

    HTML 폴백: MVP에서 미구현. NotImplementedError를 발생시킨다.

    Args:
        session: 활성 SQLAlchemy 세션. 커밋은 호출자가 담당.
        raw_payload: raw_ingestion_payloads 행.

    Returns:
        ScheduleNormalizeResult — 생성/기존 게임 수와 검토 필요 이유 목록.

    Raises:
        NotImplementedError: content_type이 JSON이 아닌 경우.
        ValueError: 페이로드 JSON 형식이 올바르지 않은 경우.
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

    games_list = body.get("games")
    if not isinstance(games_list, list):
        raise ValueError("schedule payload missing 'games' list")

    games_created = 0
    games_existing = 0
    needs_review_reasons: list[str] = []

    for entry in games_list:
        external_id: str | None = entry.get("external_id")
        if not external_id:
            needs_review_reasons.append(f"game entry missing external_id: {entry!r}")
            continue

        existing = session.execute(
            select(Game).where(Game.external_id == external_id)
        ).scalar_one_or_none()
        if existing is not None:
            games_existing += 1
            continue

        home_code: str | None = entry.get("home_team_code")
        away_code: str | None = entry.get("away_team_code")
        game_date_str: str | None = entry.get("game_date")
        venue: str | None = entry.get("venue")

        if not home_code or not away_code or not game_date_str:
            needs_review_reasons.append(
                f"game {external_id!r} missing required fields "
                f"(home_team_code, away_team_code, game_date)"
            )
            continue

        home_team = session.execute(select(Team).where(Team.code == home_code)).scalar_one_or_none()
        away_team = session.execute(select(Team).where(Team.code == away_code)).scalar_one_or_none()

        if home_team is None:
            needs_review_reasons.append(
                f"game {external_id!r}: unknown home_team_code={home_code!r}"
            )
            continue
        if away_team is None:
            needs_review_reasons.append(
                f"game {external_id!r}: unknown away_team_code={away_code!r}"
            )
            continue

        try:
            parsed_date = date.fromisoformat(game_date_str)
        except ValueError:
            needs_review_reasons.append(
                f"game {external_id!r}: unparseable game_date={game_date_str!r}"
            )
            continue

        new_game = Game(
            external_id=external_id,
            home_team_id=home_team.id,
            away_team_id=away_team.id,
            game_date=parsed_date,
            venue=venue,
        )
        session.add(new_game)
        session.flush()
        games_created += 1

    return ScheduleNormalizeResult(
        games_created=games_created,
        games_existing=games_existing,
        needs_review_reasons=tuple(needs_review_reasons),
    )
