"""KBO 로스터 raw 페이로드를 Player 도메인 행으로 정규화한다."""

from __future__ import annotations

import json
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.player import Player
from app.models.snapshot import RawIngestionPayload
from app.models.team import Team

__all__ = ["RosterNormalizeResult", "normalize_roster"]


@dataclass(frozen=True)
class RosterNormalizeResult:
    """로스터 정규화 결과.

    Attributes:
        players_created: 새로 삽입된 Player 행 수.
        players_existing: 이미 존재하여 건너뛴 Player 행 수.
        needs_review_reasons: 검토가 필요한 이유 목록.
    """

    players_created: int
    players_existing: int
    needs_review_reasons: tuple[str, ...]


def normalize_roster(
    session: Session,
    raw_payload: RawIngestionPayload,
) -> RosterNormalizeResult:
    """raw 로스터 페이로드를 파싱하여 Player 행을 upsert한다.

    기대하는 페이로드 형태 (MVP 플레이스홀더 — 실제 샘플로 검증 필요):
    JSON:
        {
            "team_code": "LG",
            "players": [
                {"external_id": "...", "name": "...", "position": "..."},
                ...
            ]
        }

    HTML 폴백: MVP에서 미구현. NotImplementedError를 발생시킨다.

    Args:
        session: 활성 SQLAlchemy 세션. 커밋은 호출자가 담당.
        raw_payload: raw_ingestion_payloads 행.

    Returns:
        RosterNormalizeResult — 생성/기존 선수 수와 검토 필요 이유 목록.

    Raises:
        NotImplementedError: content_type이 JSON이 아닌 경우.
        ValueError: 페이로드 JSON 형식이 올바르지 않은 경우.
    """
    if "json" not in raw_payload.content_type.lower():
        raise NotImplementedError(
            f"HTML roster normalization not implemented in MVP; "
            f"content_type={raw_payload.content_type!r}"
        )

    try:
        body = json.loads(raw_payload.raw_body)
    except json.JSONDecodeError as exc:
        raise ValueError(f"roster payload is not valid JSON: {exc}") from exc

    team_code: str | None = body.get("team_code")
    if not team_code:
        raise ValueError("roster payload missing 'team_code'")

    players_list = body.get("players")
    if not isinstance(players_list, list):
        raise ValueError("roster payload missing 'players' list")

    team_row = session.execute(select(Team).where(Team.code == team_code)).scalar_one_or_none()
    if team_row is None:
        raise ValueError(f"unknown team_code in roster payload: {team_code!r}")

    players_created = 0
    players_existing = 0
    needs_review_reasons: list[str] = []

    for entry in players_list:
        external_id: str | None = entry.get("external_id")
        if not external_id:
            needs_review_reasons.append(f"player entry missing external_id: {entry!r}")
            continue

        existing = session.execute(
            select(Player).where(Player.external_id == external_id)
        ).scalar_one_or_none()
        if existing is not None:
            players_existing += 1
            continue

        name: str | None = entry.get("name")
        position: str | None = entry.get("position")

        if not name or not position:
            needs_review_reasons.append(
                f"player {external_id!r} missing required fields (name, position)"
            )
            continue

        new_player = Player(
            external_id=external_id,
            team_id=team_row.id,
            name=name,
            position=position,
        )
        session.add(new_player)
        session.flush()
        players_created += 1

    return RosterNormalizeResult(
        players_created=players_created,
        players_existing=players_existing,
        needs_review_reasons=tuple(needs_review_reasons),
    )
