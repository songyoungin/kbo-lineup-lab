"""KBO 선수 스탯 raw 페이로드를 StatSnapshot + PlayerStatSnapshotRow로 정규화한다."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ingestion.player_matcher import MatchStatus, match_player
from app.models.snapshot import PlayerStatSnapshotRow, RawIngestionPayload, StatSnapshot
from app.util.time import to_utc

__all__ = ["PlayerStatsNormalizeResult", "normalize_player_stats"]


@dataclass(frozen=True)
class PlayerStatsNormalizeResult:
    """선수 스탯 정규화 결과.

    Attributes:
        snapshot_id: 생성되거나 기존 StatSnapshot의 PK.
        rows_created: 새로 삽입된 PlayerStatSnapshotRow 수.
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


def normalize_player_stats(
    session: Session,
    raw_payload: RawIngestionPayload,
) -> PlayerStatsNormalizeResult:
    """raw 선수 스탯 페이로드를 파싱하여 StatSnapshot + PlayerStatSnapshotRow를 생성한다.

    기대하는 페이로드 형태 (MVP 플레이스홀더 — 실제 샘플로 검증 필요):
    JSON:
        {
            "team_code": "LG",
            "snapshot_at": "2026-04-15T16:00:00+09:00",
            "rows": [
                {"player_external_id": "LG-P001",
                 "stats": {"OPS": 0.880, "OBP": 0.380, "SLG": 0.500}},
                ...
            ]
        }

    StatSnapshot은 content_hash로 중복 여부를 판단한다. 동일 raw 페이로드를
    재처리해도 새 스냅샷이 생성되지 않는다 (멱등).

    MATCHED 또는 player_id가 있는 NEEDS_REVIEW 매칭에 대해 PlayerStatSnapshotRow를
    삽입한다. NOT_FOUND 또는 모호한 매칭은 건너뛰고 이유를 기록한다.

    Args:
        session: 활성 SQLAlchemy 세션. 커밋은 호출자가 담당.
        raw_payload: raw_ingestion_payloads 행.

    Returns:
        PlayerStatsNormalizeResult.

    Raises:
        NotImplementedError: content_type이 JSON이 아닌 경우.
        ValueError: 페이로드 JSON 형식이 올바르지 않은 경우.
    """
    if "json" not in raw_payload.content_type.lower():
        raise NotImplementedError(
            f"HTML player_stats normalization not implemented in MVP; "
            f"content_type={raw_payload.content_type!r}"
        )

    try:
        body = json.loads(raw_payload.raw_body)
    except json.JSONDecodeError as exc:
        raise ValueError(f"player_stats payload is not valid JSON: {exc}") from exc

    team_code: str | None = body.get("team_code")
    snapshot_at_str: str | None = body.get("snapshot_at")
    rows_list = body.get("rows")

    if not team_code:
        raise ValueError("player_stats payload missing 'team_code'")
    if not snapshot_at_str:
        raise ValueError("player_stats payload missing 'snapshot_at'")
    if not isinstance(rows_list, list):
        raise ValueError("player_stats payload missing 'rows' list")

    try:
        snapshot_at = to_utc(datetime.fromisoformat(snapshot_at_str))
    except (ValueError, TypeError) as exc:
        raise ValueError(
            f"player_stats payload has invalid snapshot_at={snapshot_at_str!r}: {exc}"
        ) from exc

    content_hash = _compute_content_hash(body)

    existing_snapshot = session.execute(
        select(StatSnapshot).where(StatSnapshot.content_hash == content_hash)
    ).scalar_one_or_none()

    if existing_snapshot is not None:
        return PlayerStatsNormalizeResult(
            snapshot_id=existing_snapshot.id,
            rows_created=0,
            rows_skipped=len(rows_list),
            needs_review_reasons=(),
        )

    new_snapshot = StatSnapshot(
        ingestion_run_id=raw_payload.ingestion_run_id,
        snapshot_at=snapshot_at,
        content_hash=content_hash,
    )
    session.add(new_snapshot)
    session.flush()
    snapshot_id = new_snapshot.id

    rows_created = 0
    rows_skipped = 0
    needs_review_reasons: list[str] = []

    for entry in rows_list:
        external_id: str | None = entry.get("player_external_id")
        stats: object = entry.get("stats", {})

        match = match_player(
            session,
            team_code=team_code,
            external_id=external_id,
            name=None,
        )

        if match.status == MatchStatus.NOT_FOUND:
            rows_skipped += 1
            needs_review_reasons.append(
                f"player_stats row skipped — {match.reason} (player_external_id={external_id!r})"
            )
            continue

        if match.status == MatchStatus.NEEDS_REVIEW:
            needs_review_reasons.append(match.reason)
            if match.player_id is None:
                rows_skipped += 1
                continue

        assert match.player_id is not None
        session.add(
            PlayerStatSnapshotRow(
                snapshot_id=snapshot_id,
                player_id=match.player_id,
                stats_json=stats if isinstance(stats, dict) else {},
            )
        )
        rows_created += 1

    session.flush()
    return PlayerStatsNormalizeResult(
        snapshot_id=snapshot_id,
        rows_created=rows_created,
        rows_skipped=rows_skipped,
        needs_review_reasons=tuple(needs_review_reasons),
    )
