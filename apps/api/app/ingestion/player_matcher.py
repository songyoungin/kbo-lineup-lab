"""선수 매칭 — raw 소스 레코드를 Player 도메인 행에 연결한다."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.player import Player
from app.models.team import Team

__all__ = ["MatchStatus", "PlayerMatch", "match_player"]


class MatchStatus(StrEnum):
    """선수 매칭 결과 상태."""

    MATCHED = "matched"
    NEEDS_REVIEW = "needs_review"
    NOT_FOUND = "not_found"


@dataclass(frozen=True)
class PlayerMatch:
    """선수 매칭 결과.

    Attributes:
        status: 매칭 결과 상태.
        player_id: 매칭된 Player 행의 PK. 미매칭 또는 모호한 경우 None.
        reason: 사람이 읽을 수 있는 매칭 근거. MATCHED 상태이면 빈 문자열.
    """

    status: MatchStatus
    player_id: int | None
    reason: str  # 빈 문자열 when matched

    @property
    def is_matched(self) -> bool:
        """MATCHED 상태 여부를 반환한다."""
        return self.status == MatchStatus.MATCHED


def match_player(
    session: Session,
    *,
    team_code: str,
    external_id: str | None,
    name: str | None,
) -> PlayerMatch:
    """소스 선수 레코드를 Player 행에 매칭한다.

    매칭 전략:
    1. external_id (안정적인 소스 식별자) — 우선 시도. 히트 시 MATCHED 반환.
       - team_code가 함께 제공되었고 매칭된 player의 team_id가 해당 팀과 다르면
         NEEDS_REVIEW로 반환 (player_id는 채워서 호출자가 검토 가능).
    2. (team_code, name) 폴백 — external_id가 None이거나 DB에 없을 경우에만 시도.
       - 팀 내 정확히 1명 매칭 → NEEDS_REVIEW (호출자가 중요 경로에 사용 전 확인 필요).
         player_id가 반환되므로 호출자가 조심스럽게 진행 가능.
       - 0명 매칭 → NOT_FOUND.
       - 다수 매칭 → NEEDS_REVIEW (reason에 "ambiguous: N matches" 포함).

    Args:
        session: 활성 SQLAlchemy 세션.
        team_code: 팀 코드 (예: "LG").
        external_id: 소스 제공 선수 고유 식별자. 없으면 None.
        name: 선수 이름. 없으면 None.

    Returns:
        PlayerMatch 결과 객체.
    """
    if external_id:
        row = session.execute(
            select(Player).where(Player.external_id == external_id)
        ).scalar_one_or_none()
        if row is not None:
            if team_code:
                team_row = session.execute(
                    select(Team).where(Team.code == team_code)
                ).scalar_one_or_none()
                if team_row is not None and row.team_id != team_row.id:
                    return PlayerMatch(
                        MatchStatus.NEEDS_REVIEW,
                        row.id,
                        (
                            f"external_id {external_id!r} matched but player "
                            f"belongs to a different team"
                        ),
                    )
            return PlayerMatch(MatchStatus.MATCHED, row.id, "")

    if name:
        team_row = session.execute(select(Team).where(Team.code == team_code)).scalar_one_or_none()
        if team_row is None:
            return PlayerMatch(MatchStatus.NOT_FOUND, None, f"unknown team_code: {team_code}")
        candidates = (
            session.execute(
                select(Player).where(
                    Player.team_id == team_row.id,
                    Player.name == name,
                )
            )
            .scalars()
            .all()
        )
        if len(candidates) == 1:
            return PlayerMatch(
                MatchStatus.NEEDS_REVIEW,
                candidates[0].id,
                f"fallback match by (team_code, name)={team_code!r},{name!r}",
            )
        if len(candidates) > 1:
            return PlayerMatch(
                MatchStatus.NEEDS_REVIEW,
                None,
                f"ambiguous: {len(candidates)} matches by (team_code, name)={team_code!r},{name!r}",
            )

    return PlayerMatch(MatchStatus.NOT_FOUND, None, "no external_id and no name match")
