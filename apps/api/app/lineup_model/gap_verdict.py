"""Shared classification of the actual-vs-recommended lineup score gap.

Single source of truth for both the pregame verdict and the postgame gap label,
so the two layers can never drift into contradictory bands for the same gap (a
real bug once existed where pregame thresholds were 100x tighter than postgame,
so one game could read "Low offensive efficiency" pregame yet "nearly optimal"
postgame).

Scores live in run-expectancy space: ``total_score`` is full-game expected runs
(~4-6) plus a small handedness adjustment. The gap is
``actual_score - recommended_score`` (negative = the manager's lineup scores
fewer expected runs than the model's recommendation).

Threshold rationale (empirical, full-game expected-runs gap; expected runs ~4.0):
  * Batting-order-only suboptimality caps near -0.10 run -- even fully reversing
    the SAME nine batters costs only ~0.1 run -- so "nearly optimal" must absorb
    gaps down to ~-0.15 and never punish mere reordering.
  * Benching one or two genuine bats costs ~0.4-0.7 run -> "questionable".
  * A gap past ~0.8 run means several real bats sat -> "low offensive efficiency".
"""

from __future__ import annotations

from enum import Enum

# Gap = actual_score - recommended_score, in expected runs over a full game.
GAP_NEARLY_OPTIMAL = -0.15
GAP_ACCEPTABLE = -0.40
GAP_QUESTIONABLE = -0.80


class GapTier(Enum):
    """Quality band for an actual-vs-recommended lineup score gap."""

    NEARLY_OPTIMAL = "nearly_optimal"
    ACCEPTABLE = "acceptable"
    QUESTIONABLE = "questionable"
    LOW = "low"


def classify_gap(gap: float) -> GapTier:
    """Map an actual-minus-recommended score gap to its quality tier.

    Args:
        gap: actual_score - recommended_score (negative = actual is weaker).

    Returns:
        The GapTier band the gap falls into.
    """
    if gap >= GAP_NEARLY_OPTIMAL:
        return GapTier.NEARLY_OPTIMAL
    if gap >= GAP_ACCEPTABLE:
        return GapTier.ACCEPTABLE
    if gap >= GAP_QUESTIONABLE:
        return GapTier.QUESTIONABLE
    return GapTier.LOW
