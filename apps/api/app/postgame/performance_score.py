"""Pure weighted performance score computation from a single box score row.

Performance score thresholds for classification:
- Overperformer: score >= OVERPERFORM_THRESHOLD (4.0)
- Underperformer: score <= UNDERPERFORM_THRESHOLD (-1.0)
- Otherwise: Expected
"""

from __future__ import annotations

from collections.abc import Mapping

from app.postgame.types import PerformanceLabel

PERFORMANCE_WEIGHTS: dict[str, float] = {
    "single": 1.0,
    "double": 2.0,
    "triple": 3.0,
    "home_run": 4.0,
    "walk_hbp": 1.0,
    "run": 1.0,
    "rbi": 1.0,
    "strikeout": -0.5,
    "gidp": -1.5,
}

# A player scoring 4+ points (e.g. a home run + run + RBI) is exceptional.
OVERPERFORM_THRESHOLD: float = 4.0

# A player scoring -1 or worse (at least two strikeouts, or strikeout + GIDP) is poor.
UNDERPERFORM_THRESHOLD: float = -1.0


def compute_performance_score(box_line: Mapping[str, object]) -> float:
    """Compute the weighted performance score from a single box score row.

    The BoxScoreRow has explicit ``at_bats``, ``hits``, ``runs``, ``rbis``,
    and an ``extra_stats_json`` blob for everything else (BB, HBP, SO, GIDP,
    2B, 3B, HR).  ``hits`` is the total — singles are inferred as
    ``hits - 2B - 3B - HR`` (clamped at 0).

    Missing fields in ``extra_stats_json`` default to 0.

    Args:
        box_line: Mapping that mirrors the BoxScoreRow column layout.  The
            ``extra_stats_json`` key should be a nested mapping; if absent or
            None it is treated as an empty dict.

    Returns:
        Weighted performance score (float, may be negative).
    """

    def _int(key: str, source: Mapping[str, object]) -> int:
        v = source.get(key, 0)
        if v is None:
            return 0
        return int(v) if isinstance(v, (int, float)) else 0

    runs = _int("runs", box_line)
    rbis = _int("rbis", box_line)
    hits = _int("hits", box_line)

    extra = box_line.get("extra_stats_json") or {}
    if not isinstance(extra, Mapping):
        extra = {}

    doubles = _int("2B", extra)
    triples = _int("3B", extra)
    home_runs = _int("HR", extra)
    singles = max(0, hits - doubles - triples - home_runs)
    walk_hbp = _int("BB", extra) + _int("HBP", extra)
    strikeouts = _int("SO", extra)
    gidp = _int("GIDP", extra)

    score = (
        singles * PERFORMANCE_WEIGHTS["single"]
        + doubles * PERFORMANCE_WEIGHTS["double"]
        + triples * PERFORMANCE_WEIGHTS["triple"]
        + home_runs * PERFORMANCE_WEIGHTS["home_run"]
        + walk_hbp * PERFORMANCE_WEIGHTS["walk_hbp"]
        + runs * PERFORMANCE_WEIGHTS["run"]
        + rbis * PERFORMANCE_WEIGHTS["rbi"]
        + strikeouts * PERFORMANCE_WEIGHTS["strikeout"]
        + gidp * PERFORMANCE_WEIGHTS["gidp"]
    )
    return score


def classify_performance(score: float) -> PerformanceLabel:
    """Classify a performance score into over / expected / under.

    Args:
        score: Output of ``compute_performance_score``.

    Returns:
        ``PerformanceLabel`` enum member.
    """
    if score >= OVERPERFORM_THRESHOLD:
        return PerformanceLabel.OVERPERFORMED
    if score <= UNDERPERFORM_THRESHOLD:
        return PerformanceLabel.UNDERPERFORMED
    return PerformanceLabel.EXPECTED
