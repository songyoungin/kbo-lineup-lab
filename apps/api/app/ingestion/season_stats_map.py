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


def map_season_stats(
    raw: dict[str, Any], *, bats: str | None, position: str | None
) -> dict[str, Any]:
    """Return an evaluator stats_json dict from a source season-batting row.

    Args:
        raw: One season batting row from the source. Recognized keys: ab, hit,
            h2, h3, hr, obp, and optionally slg/ops (preferred when present).
        bats: Player batting handedness ("L"/"R"/"S") or None.
        position: Canonical Position value ("CF","1B",...) or None.

    Returns:
        stats_json with float OPS/OBP/SLG, handedness, primary_position, and the
        raw source row preserved under "_source" for auditing.
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

    return {
        "OPS": ops,
        "OBP": obp,
        "SLG": slg,
        "handedness": bats if bats in ("L", "R", "S") else "R",
        "primary_position": position or "DH",
        "_source": raw,
    }
