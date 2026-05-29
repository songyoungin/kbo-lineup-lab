"""Shared helpers for Naver-preview normalizers.

Consolidates logic duplicated across the lineup, player-stats, and (Task 6)
box-score normalizers: the KST timezone, the canonical content-hash helper, and
the gdate/gtime -> UTC parsing of ``previewData.gameInfo``.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from datetime import datetime
from typing import Final
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ingestion.game_id import naver_to_kbo
from app.models.game import Game
from app.util.time import KST, to_utc

__all__ = [
    "KST",
    "LG_TEAM_CODE",
    "compute_content_hash",
    "parse_game_datetime_kst",
    "resolve_game_from_naver_url",
]

# The single-team MVP scope sentinel for normalizers: only LG-side data is parsed.
LG_TEAM_CODE: Final = "LG"

# Extracts the Naver game id from ".../schedule/games/{naverId}/<sub>" where the
# sub-resource is either "preview" (lineup/stats) or "record" (box score).
_GAME_ID_URL_RE: Final = re.compile(r"/schedule/games/([^/]+)/")


def compute_content_hash(canonical: object) -> str:
    """Return the SHA-256 hex digest of the canonical JSON serialization.

    Args:
        canonical: Any JSON-serialisable object.

    Returns:
        64-character lowercase hex digest.
    """
    text = json.dumps(canonical, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(text.encode()).hexdigest()


def parse_game_datetime_kst(game_info: Mapping[str, object]) -> datetime:
    """Derive a deterministic UTC datetime from ``gameInfo`` gdate/gtime.

    Naver returns ``gdate`` as an int (e.g. ``20250514``) or string; it is coerced
    to an 8-character ``YYYYMMDD`` string. ``gtime`` (``HH:MM``) defaults to
    ``"00:00"`` when missing or blank. The combined local time is interpreted as
    KST and normalised to UTC.

    Args:
        game_info: The ``previewData.gameInfo`` mapping.

    Returns:
        UTC-normalised datetime.

    Raises:
        ValueError: If ``gdate`` is missing or cannot be parsed as ``YYYYMMDD``.
    """
    gdate_raw = game_info.get("gdate")
    gdate = str(gdate_raw) if isinstance(gdate_raw, int) else gdate_raw
    if not isinstance(gdate, str) or len(gdate) != 8:
        raise ValueError(f"gameInfo missing valid gdate (YYYYMMDD): {gdate_raw!r}")
    gtime_raw = game_info.get("gtime")
    gtime = gtime_raw if isinstance(gtime_raw, str) and gtime_raw.strip() else "00:00"
    try:
        local = datetime.strptime(f"{gdate} {gtime}", "%Y%m%d %H:%M").replace(tzinfo=KST)
    except ValueError as exc:
        raise ValueError(f"gameInfo has invalid gdate/gtime: {exc}") from exc
    return to_utc(local)


def resolve_game_from_naver_url(session: Session, source_url: str) -> Game:
    """Resolve the Game from a Naver /schedule/games/{id}/<sub> URL via naver_to_kbo lookup.

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
        raise ValueError(f"payload references unknown game: {external_id!r}")
    return game
