"""Normalize a Naver preview lineup payload into ActualLineupSnapshot + rows.

The collector stores the raw Naver api-gw preview JSON. This normalizer extracts
the LG side of ``result.previewData`` into one immutable lineup snapshot, upserts
the Player rows it references (including batting/throwing handedness parsed from
``hitType``), and creates one snapshot row per batter (the entries carrying a
``batorder``). The starting pitcher is upserted as a Player but produces no row.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Final

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ingestion.normalizers._shared import (
    LG_TEAM_CODE,
    compute_content_hash,
    parse_game_datetime_kst,
    resolve_game_from_naver_url,
)
from app.models.player import Player
from app.models.snapshot import (
    ActualLineupSnapshot,
    ActualLineupSnapshotRow,
    RawIngestionPayload,
)
from app.models.team import Team

__all__ = ["LineupNormalizeResult", "normalize_lineup"]

_HAND_MAP: Final[dict[str, str]] = {"좌": "L", "우": "R", "양": "S"}
_POSITION_PLAYER_RE: Final = re.compile(r"^([우좌])투([우좌양])타$")
# Pitchers: "우완투수"/"좌완투수" as well as underhand/sidearm "우완언더" etc.
_PITCHER_RE: Final = re.compile(r"^([우좌])완.*$")


@dataclass(frozen=True)
class LineupNormalizeResult:
    """Result of normalizing a lineup payload.

    Attributes:
        snapshot_id: PK of the created or existing ActualLineupSnapshot.
        rows_created: Number of newly inserted ActualLineupSnapshotRow rows.
        rows_skipped: Number of entries skipped due to missing fields.
        needs_review_reasons: Reasons that require manual review.
    """

    snapshot_id: int
    rows_created: int
    rows_skipped: int
    needs_review_reasons: tuple[str, ...]


def _parse_handedness(
    hit_type: str | None, bats_throws: str | None
) -> tuple[str | None, str | None]:
    """Parse batting/throwing handedness from Naver hitType / batsThrows strings.

    Args:
        hit_type: e.g. "우투좌타" (position player) or "좌완투수" (pitcher).
        bats_throws: Fallback such as "좌타" or "우투".

    Returns:
        ``(bats, throws)`` normalized to "L"/"R"/"S" (bats only), or None when
        a side cannot be determined.
    """
    if hit_type:
        position_match = _POSITION_PLAYER_RE.match(hit_type)
        if position_match:
            throws = _HAND_MAP.get(position_match.group(1))
            bats = _HAND_MAP.get(position_match.group(2))
            return bats, throws
        pitcher_match = _PITCHER_RE.match(hit_type)
        if pitcher_match:
            return None, _HAND_MAP.get(pitcher_match.group(1))

    if bats_throws:
        first = bats_throws[0]
        if bats_throws.endswith("타"):
            return _HAND_MAP.get(first), None
        # "투" = overhand throws; "언" = underhand/sidearm — both indicate arm side.
        if bats_throws.endswith("투") or bats_throws.endswith("언"):
            return None, _HAND_MAP.get(first)

    return None, None


def _upsert_player(session: Session, team_id: int, entry: dict[str, object]) -> Player:
    """Find a Player by playerCode or create it, backfilling handedness if null.

    Args:
        session: Active SQLAlchemy session.
        team_id: LG team id used when creating a new Player.
        entry: A single ``fullLineUp`` entry.

    Returns:
        The existing or newly created Player.
    """
    external_id = str(entry["playerCode"])
    name = str(entry.get("playerName") or "")
    position = str(entry.get("position") or "")
    hit_type = entry.get("hitType")
    bats_throws = entry.get("batsThrows")
    bats, throws = _parse_handedness(
        hit_type if isinstance(hit_type, str) else None,
        bats_throws if isinstance(bats_throws, str) else None,
    )

    player = session.execute(
        select(Player).where(Player.external_id == external_id)
    ).scalar_one_or_none()
    if player is None:
        # Lineup-sourced players carry the Naver numeric position code (e.g. "8");
        # acceptable as the roster collector is dropped in Task 7, making the
        # lineup/box-score payloads the player source of record.
        player = Player(
            team_id=team_id,
            external_id=external_id,
            name=name,
            position=position,
            bats=bats,
            throws=throws,
        )
        session.add(player)
        session.flush()
        return player

    # Backfill handedness when previously unknown.
    if player.bats is None and bats is not None:
        player.bats = bats
    if player.throws is None and throws is not None:
        player.throws = throws
    return player


def normalize_lineup(
    session: Session,
    raw_payload: RawIngestionPayload,
) -> LineupNormalizeResult:
    """Parse a Naver preview lineup payload into an ActualLineupSnapshot + rows.

    Selects the LG side via ``gameInfo.hCode``/``aCode``, derives a deterministic
    ``announced_at`` from gdate/gtime (KST -> UTC), upserts a Player per
    ``fullLineUp`` entry, and creates one snapshot row per batter (entries with a
    ``batorder``). The natural key (game_id, team_id, announced_at) makes re-runs
    idempotent.

    Args:
        session: Active SQLAlchemy session. Caller controls the transaction.
        raw_payload: Row from ``raw_ingestion_payloads``.

    Returns:
        LineupNormalizeResult.

    Raises:
        NotImplementedError: If the payload content_type is not JSON.
        ValueError: If the payload is malformed, the game/team cannot be
            resolved, or LG is not in the game.
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

    preview = (body.get("result") or {}).get("previewData") or {}
    if not isinstance(preview, dict):
        raise ValueError("lineup payload missing result.previewData")
    game_info = preview.get("gameInfo") or {}
    if not isinstance(game_info, dict):
        raise ValueError("lineup payload missing previewData.gameInfo")

    game = resolve_game_from_naver_url(session, raw_payload.source_url)

    home_code = game_info.get("hCode")
    away_code = game_info.get("aCode")
    if home_code == LG_TEAM_CODE:
        lineup_block = preview.get("homeTeamLineUp") or {}
    elif away_code == LG_TEAM_CODE:
        lineup_block = preview.get("awayTeamLineUp") or {}
    else:
        raise ValueError(f"LG not in game: hCode={home_code!r} aCode={away_code!r}")

    team = session.execute(select(Team).where(Team.code == LG_TEAM_CODE)).scalar_one_or_none()
    if team is None:
        raise ValueError(f"unknown team code: {LG_TEAM_CODE!r}")

    announced_at = parse_game_datetime_kst(game_info)

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

    entries = lineup_block.get("fullLineUp") or []
    # content_hash is informational only — this snapshot deduplicates on the
    # natural key (game_id, team_id, announced_at), not on content_hash (unlike
    # StatSnapshot / BoxScoreSnapshot which use content_hash as their dedup key).
    content_hash = compute_content_hash(entries)
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

    for entry in entries:
        if not entry.get("playerCode") or not entry.get("position"):
            rows_skipped += 1
            needs_review_reasons.append(f"lineup entry missing playerCode/position: {entry!r}")
            continue

        player = _upsert_player(session, team.id, entry)

        batorder = entry.get("batorder")
        if batorder is None:
            # Starting pitcher: upserted as Player but no lineup row.
            continue

        session.add(
            ActualLineupSnapshotRow(
                snapshot_id=snapshot_id,
                player_id=player.id,
                batting_order=batorder,
                position=str(entry["position"]),
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
