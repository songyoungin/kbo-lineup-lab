"""KBO 라인업 raw 페이로드를 ActualLineupSnapshot + ActualLineupSnapshotRow로 정규화한다."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ingestion.player_matcher import MatchStatus, match_player
from app.models.game import Game
from app.models.snapshot import (
    ActualLineupSnapshot,
    ActualLineupSnapshotRow,
    RawIngestionPayload,
)
from app.models.team import Team
from app.util.time import to_utc

__all__ = ["LineupNormalizeResult", "normalize_lineup"]


@dataclass(frozen=True)
class LineupNormalizeResult:
    """라인업 정규화 결과.

    Attributes:
        snapshot_id: 생성되거나 기존 ActualLineupSnapshot의 PK.
        rows_created: 새로 삽입된 ActualLineupSnapshotRow 수.
        rows_skipped: 선수를 찾지 못해 건너뛴 행 수.
        needs_review_reasons: 검토가 필요한 이유 목록.
    """

    snapshot_id: int
    rows_created: int
    rows_skipped: int
    needs_review_reasons: tuple[str, ...]


def _compute_content_hash(canonical: object) -> str:
    """정규화된 JSON 직렬화 후 SHA-256 해시를 반환한다."""
    text = json.dumps(canonical, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(text.encode()).hexdigest()


def normalize_lineup(
    session: Session,
    raw_payload: RawIngestionPayload,
) -> LineupNormalizeResult:
    """raw 라인업 페이로드를 파싱하여 ActualLineupSnapshot + 행을 생성한다.

    기대하는 페이로드 형태 (Naver Sports MVP 플레이스홀더):
    JSON:
        {
            "game_external_id": "20260415LGDOO",
            "team_code": "LG",
            "announced_at": "2026-04-15T17:30:00+09:00",
            "awayLineup": [...],   // 또는 homeLineup — team_code가 홈/어웨이 중 어느 쪽인지에 따라
            "homeLineup": [...]
        }

    team_code가 해당 경기에서 홈인지 어웨이인지 확인하여 올바른 라인업 배열을 선택한다.
    각 라인업 항목: {"player_external_id": "...", "batting_order": 1, "position": "CF"}

    ActualLineupSnapshot의 자연키는 (game_id, team_id, announced_at)이다.
    동일 키로 재실행해도 새 스냅샷이 생성되지 않는다 (멱등).

    HTML 폴백: MVP에서 미구현. NotImplementedError를 발생시킨다.

    Args:
        session: 활성 SQLAlchemy 세션. 커밋은 호출자가 담당.
        raw_payload: raw_ingestion_payloads 행.

    Returns:
        LineupNormalizeResult.

    Raises:
        NotImplementedError: content_type이 JSON이 아닌 경우.
        ValueError: 페이로드 JSON 형식이 올바르지 않은 경우.
    """
    if "json" not in raw_payload.content_type.lower():
        raise NotImplementedError(
            f"HTML lineup normalization not implemented in MVP; "
            f"content_type={raw_payload.content_type!r}"
        )

    try:
        body = json.loads(raw_payload.raw_body)
    except json.JSONDecodeError as exc:
        raise ValueError(f"lineup payload is not valid JSON: {exc}") from exc

    game_external_id: str | None = body.get("game_external_id")
    team_code: str | None = body.get("team_code")
    announced_at_str: str | None = body.get("announced_at")

    if not game_external_id:
        raise ValueError("lineup payload missing 'game_external_id'")
    if not team_code:
        raise ValueError("lineup payload missing 'team_code'")
    if not announced_at_str:
        raise ValueError("lineup payload missing 'announced_at'")

    try:
        announced_at = to_utc(datetime.fromisoformat(announced_at_str))
    except (ValueError, TypeError) as exc:
        raise ValueError(
            f"lineup payload has invalid announced_at={announced_at_str!r}: {exc}"
        ) from exc

    game = session.execute(
        select(Game).where(Game.external_id == game_external_id)
    ).scalar_one_or_none()
    if game is None:
        raise ValueError(f"lineup payload references unknown game: {game_external_id!r}")

    team = session.execute(select(Team).where(Team.code == team_code)).scalar_one_or_none()
    if team is None:
        raise ValueError(f"lineup payload references unknown team_code: {team_code!r}")

    # team이 홈인지 어웨이인지 판단하여 올바른 라인업 배열 선택
    if game.home_team_id == team.id:
        lineup_entries = body.get("homeLineup") or []
    elif game.away_team_id == team.id:
        lineup_entries = body.get("awayLineup") or []
    else:
        raise ValueError(
            f"team_code={team_code!r} is neither home nor away in game={game_external_id!r}"
        )

    existing_snapshot = session.execute(
        select(ActualLineupSnapshot).where(
            ActualLineupSnapshot.game_id == game.id,
            ActualLineupSnapshot.team_id == team.id,
            ActualLineupSnapshot.announced_at == announced_at,
        )
    ).scalar_one_or_none()

    if existing_snapshot is not None:
        return LineupNormalizeResult(
            snapshot_id=existing_snapshot.id,
            rows_created=0,
            rows_skipped=0,
            needs_review_reasons=(),
        )

    content_hash = _compute_content_hash(body)
    new_snapshot = ActualLineupSnapshot(
        game_id=game.id,
        team_id=team.id,
        ingestion_run_id=raw_payload.ingestion_run_id,
        announced_at=announced_at,
        content_hash=content_hash,
    )
    session.add(new_snapshot)
    session.flush()
    snapshot_id = new_snapshot.id

    rows_created = 0
    rows_skipped = 0
    needs_review_reasons: list[str] = []

    for entry in lineup_entries:
        external_id: str | None = entry.get("player_external_id")
        batting_order: int | None = entry.get("batting_order")
        position: str | None = entry.get("position")

        if not position:
            needs_review_reasons.append(f"lineup entry missing position: {entry!r}")
            rows_skipped += 1
            continue

        match = match_player(
            session,
            team_code=team_code,
            external_id=external_id,
            name=entry.get("name"),
        )

        if match.status == MatchStatus.NOT_FOUND:
            rows_skipped += 1
            needs_review_reasons.append(
                f"lineup row skipped — {match.reason} (player_external_id={external_id!r})"
            )
            continue

        if match.status == MatchStatus.NEEDS_REVIEW:
            needs_review_reasons.append(match.reason)
            if match.player_id is None:
                rows_skipped += 1
                continue

        assert match.player_id is not None
        session.add(
            ActualLineupSnapshotRow(
                snapshot_id=snapshot_id,
                player_id=match.player_id,
                batting_order=batting_order,
                position=position,
            )
        )
        rows_created += 1

    session.flush()
    return LineupNormalizeResult(
        snapshot_id=snapshot_id,
        rows_created=rows_created,
        rows_skipped=rows_skipped,
        needs_review_reasons=tuple(needs_review_reasons),
    )
