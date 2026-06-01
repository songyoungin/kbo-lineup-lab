"""Map a source season-batting dict into the evaluator stats_json schema.

The evaluator (app/services/lineup_evaluator.py::build_hitter_stats) requires
numeric OPS/OBP/SLG (a string raises TypeError) plus handedness and a canonical
primary_position. The verified Naver record.season rows carry obp/slg/ops as
native numbers, so those pass through; when a source omits SLG/OPS they are
derived from the counts (total_bases / AB; OPS = OBP + SLG). Numeric strings are
coerced to float defensively. Field names follow the source verified in
docs/data-sources/player-season-stats-verification.md.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, timedelta
from typing import Any

__all__ = ["map_season_stats"]


def _num(value: Any, default: float = 0.0) -> float:
    """Coerce a source value (number or numeric string) to float."""
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _recent_window_ops(
    game_log: Sequence[dict[str, Any]] | None,
    as_of: date | None,
    window_days: int,
) -> float | None:
    """Compute OPS over games played within ``[as_of - window_days, as_of)``.

    Returns None when there is no game log, no as_of date, or no plate
    appearances in the window — callers then fall back to season OPS.
    Note: HBP is absent from the game log, so OBP is computed without it
    (slightly conservative).
    """
    if not game_log or as_of is None:
        return None
    start = as_of - timedelta(days=window_days)
    ab = h = h2 = h3 = hr = bb = sf = 0
    for row in game_log:
        gday = str(row.get("gday", ""))
        if len(gday) != 8 or not gday.isdigit():
            continue
        played = date(int(gday[0:4]), int(gday[4:6]), int(gday[6:8]))
        if not (start <= played < as_of):
            continue
        ab += int(_num(row.get("ab")))
        h += int(_num(row.get("hit")))
        h2 += int(_num(row.get("h2")))
        h3 += int(_num(row.get("h3")))
        hr += int(_num(row.get("hr")))
        bb += int(_num(row.get("bb")))
        sf += int(_num(row.get("sf")))
    on_base_denom = ab + bb + sf
    if ab == 0 or on_base_denom == 0:
        return None
    singles = max(0, h - h2 - h3 - hr)
    total_bases = singles + 2 * h2 + 3 * h3 + 4 * hr
    slg = total_bases / ab
    obp = (h + bb) / on_base_denom
    return obp + slg


def map_season_stats(
    raw: dict[str, Any],
    *,
    bats: str | None,
    position: str | None,
    game_log: Sequence[dict[str, Any]] | None = None,
    as_of: date | None = None,
) -> dict[str, Any]:
    """Return an evaluator stats_json dict from a source season-batting row.

    Args:
        raw: One season batting row from the source. Recognized keys: ab, hit,
            h2, h3, hr, obp, and optionally slg/ops (preferred when present).
        bats: Player batting handedness ("L"/"R"/"S") or None.
        position: Canonical Position value ("CF","1B",...) or None.
        game_log: Optional per-game batting rows (keys: gday "YYYYMMDD", ab, hit,
            h2, h3, hr, bb, sf) used to derive recent-form OPS.
        as_of: Reference date for the rolling windows; recent fields are only
            emitted when both game_log and as_of are provided.

    Returns:
        stats_json with float OPS/OBP/SLG, handedness, primary_position, the raw
        source row under "_source" for auditing, and — when a game log yields
        plate appearances inside the window — recent_14d_ops / recent_30d_ops.
    """
    ab = _num(raw.get("ab"))
    obp = _num(raw.get("obp"))

    slg_raw = raw.get("slg")
    if slg_raw is not None:
        slg = _num(slg_raw)
    elif ab > 0:
        singles = (
            _num(raw.get("hit")) - _num(raw.get("h2")) - _num(raw.get("h3")) - _num(raw.get("hr"))
        )
        total_bases = (
            singles + 2 * _num(raw.get("h2")) + 3 * _num(raw.get("h3")) + 4 * _num(raw.get("hr"))
        )
        slg = total_bases / ab
    else:
        slg = 0.0

    ops_raw = raw.get("ops")
    ops = _num(ops_raw) if ops_raw is not None else obp + slg

    result = {
        "OPS": ops,
        "OBP": obp,
        "SLG": slg,
        "handedness": bats if bats in ("L", "R", "S") else "R",
        "primary_position": position or "DH",
        "_source": raw,
    }

    recent_14 = _recent_window_ops(game_log, as_of, 14)
    if recent_14 is not None:
        result["recent_14d_ops"] = recent_14
    recent_30 = _recent_window_ops(game_log, as_of, 30)
    if recent_30 is not None:
        result["recent_30d_ops"] = recent_30
    return result
