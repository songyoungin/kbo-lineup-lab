"""Shared helpers for Naver-preview normalizers.

Consolidates logic duplicated across the lineup, player-stats, and (Task 6)
box-score normalizers: the KST timezone, the canonical content-hash helper, and
the gdate/gtime -> UTC parsing of ``previewData.gameInfo``.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from typing import Final

from app.util.time import to_utc

__all__ = ["KST", "compute_content_hash", "parse_game_datetime_kst"]

KST: Final = timezone(timedelta(hours=9))


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
