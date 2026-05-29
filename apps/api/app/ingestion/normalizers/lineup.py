"""Normalize a Naver preview lineup payload into ActualLineupSnapshot + rows.

The collector stores the raw Naver api-gw preview JSON. This normalizer extracts
the LG side of ``result.previewData`` into one immutable lineup snapshot, upserts
the Player rows it references (including batting/throwing handedness parsed from
``hitType``), and creates one snapshot row per batter (the entries carrying a
``batorder``). The starting pitcher is upserted as a Player but produces no row.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Final
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ingestion.game_id import naver_to_kbo
from app.models.game import Game
from app.models.player import Player
from app.models.snapshot import (
    ActualLineupSnapshot,
    ActualLineupSnapshotRow,
    RawIngestionPayload,
)
from app.models.team import Team
from app.util.time import to_utc

__all__ = ["LineupNormalizeResult", "normalize_lineup"]

_LG_CODE: Final = "LG"
_KST: Final = timezone(timedelta(hours=9))
_HAND_MAP: Final[dict[str, str]] = {"좌": "L", "우": "R", "양": "S"}
_POSITION_PLAYER_RE: Final = re.compile(r"^([우좌])투([우좌양])타$")
_PITCHER_RE: Final = re.compile(r"^([우좌])완투수$")
# Extracts the Naver game id from ".../schedule/games/{naverId}/preview".
_GAME_ID_URL_RE: Final = re.compile(r"/schedule/games/([^/]+)/preview")


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


def _compute_content_hash(canonical: object) -> str:
    """Return the SHA-256 hash of the canonical JSON serialization."""
    text = json.dumps(canonical, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(text.encode()).hexdigest()


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
        if bats_throws.endswith("투"):
            return None, _HAND_MAP.get(first)

    return None, None


def _derive_announced_at(game_info: dict[str, object]) -> datetime:
    """Derive a deterministic UTC announcement timestamp from gameInfo.

    Args:
        game_info: ``previewData.gameInfo`` dict with gdate/gtime.

    Returns:
        UTC datetime built from gdate + gtime interpreted as KST.

    Raises:
        ValueError: If gdate is missing or unparseable.
    """
    gdate_raw = game_info.get("gdate")
    # Naver returns gdate as an int (e.g. 20250514); coerce to a YYYYMMDD string.
    gdate = str(gdate_raw) if isinstance(gdate_raw, int) else gdate_raw
    if not isinstance(gdate, str) or len(gdate) != 8:
        raise ValueError(f"lineup payload gameInfo missing valid gdate: {gdate_raw!r}")
    gtime = game_info.get("gtime")
    if not isinstance(gtime, str) or not gtime.strip():
        gtime = "00:00"
    try:
        local = datetime.strptime(f"{gdate} {gtime}", "%Y%m%d %H:%M").replace(tzinfo=_KST)
    except ValueError as exc:
        raise ValueError(f"lineup payload gameInfo has invalid gdate/gtime: {exc}") from exc
    return to_utc(local)


def _resolve_game(session: Session, source_url: str) -> Game:
    """Resolve the Game from the Naver game id embedded in the source URL.

    Args:
        session: Active SQLAlchemy session.
        source_url: The raw payload source URL containing the Naver game id.

    Returns:
        The matching Game row.

    Raises:
        ValueError: If the URL has no Naver game id or no Game matches.
    """
    match = _GAME_ID_URL_RE.search(urlsplit(source_url).path)
    if match is None:
        raise ValueError(f"cannot extract Naver game id from source_url: {source_url!r}")
    try:
        external_id = naver_to_kbo(match.group(1))
    except ValueError as exc:
        raise ValueError(f"unparseable Naver game id in source_url: {source_url!r}") from exc
    game = session.execute(select(Game).where(Game.external_id == external_id)).scalar_one_or_none()
    if game is None:
        raise ValueError(f"lineup payload references unknown game: {external_id!r}")
    return game


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

    game = _resolve_game(session, raw_payload.source_url)

    home_code = game_info.get("hCode")
    away_code = game_info.get("aCode")
    if home_code == _LG_CODE:
        lineup_block = preview.get("homeTeamLineUp") or {}
    elif away_code == _LG_CODE:
        lineup_block = preview.get("awayTeamLineUp") or {}
    else:
        raise ValueError("LG not in game")

    team = session.execute(select(Team).where(Team.code == _LG_CODE)).scalar_one_or_none()
    if team is None:
        raise ValueError(f"unknown team code: {_LG_CODE!r}")

    announced_at = _derive_announced_at(game_info)

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
    content_hash = _compute_content_hash(entries)
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
