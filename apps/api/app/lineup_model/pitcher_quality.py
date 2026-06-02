"""Opponent-starter quality -> lineup score-calibration multiplier (pure).

Applied EQUALLY to recommended and actual lineup totals, so it calibrates the
score magnitude for matchup difficulty without changing player selection or
batting order. League baselines are approximate KBO run-environment constants;
revisit per season.
"""

from __future__ import annotations

# Approximate KBO league baselines (revisit per season).
_LEAGUE_ERA = 4.5
_LEAGUE_WHIP = 1.45
_MULT_MIN = 0.9
_MULT_MAX = 1.1


def matchup_difficulty_multiplier(*, era: float | None, whip: float | None) -> float:
    """Return a clamped lineup-score multiplier for the opposing starter.

    A tougher starter (lower ERA/WHIP) pulls the multiplier toward ``0.9``;
    a weaker one toward ``1.1``. Returns ``1.0`` when either input is missing
    or non-positive (graceful no-op).

    Args:
        era: Opposing starter's season ERA, or None.
        whip: Opposing starter's season WHIP, or None.

    Returns:
        Multiplier in ``[0.9, 1.1]``.
    """
    if era is None or whip is None or era <= 0 or whip <= 0:
        return 1.0
    raw = (era / _LEAGUE_ERA + whip / _LEAGUE_WHIP) / 2
    return min(_MULT_MAX, max(_MULT_MIN, raw))
