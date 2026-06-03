"""Approximate per-PA event probabilities derived from season OBP/SLG.

Pure, deterministic. HBP is folded into ``bb``; SF/SH/ROE are ignored. The
league constants are recent-KBO-average approximations -- documented as such; a
future upgrade can replace this provider with real per-hitter counting lines
without changing the ``EventRates`` interface.
"""

from __future__ import annotations

from dataclasses import dataclass

# League-average approximations (per-PA walk+HBP rate, and the split of
# extra-base HITS into 2B/3B/HR). Tunable; documented as estimates.
_LEAGUE_BB_RATE = 0.085
_XBH_SHARES = (0.78, 0.04, 0.18)  # (double, triple, hr) fractions of XB hits


@dataclass(frozen=True)
class EventRates:
    """Per-plate-appearance outcome probabilities (sum to 1.0)."""

    bb: float
    single: float
    double: float
    triple: float
    hr: float
    out: float


def event_rates(obp: float, slg: float) -> EventRates:
    """Derive a per-PA outcome distribution from season OBP and SLG.

    Args:
        obp: On-base percentage (reaches base per PA).
        slg: Slugging percentage (total bases per at-bat).

    Returns:
        EventRates whose six probabilities sum to exactly 1.0.
    """
    obp = min(max(obp, 0.0), 0.999)
    slg = max(slg, 0.0)

    bb = min(_LEAGUE_BB_RATE, obp * 0.99)
    hit = max(0.0, obp - bb)  # hits per PA
    out = 1.0 - obp

    ab_per_pa = 1.0 - bb
    tb_per_pa = slg * ab_per_pa
    extra = max(0.0, tb_per_pa - hit)  # extra bases beyond one-per-hit

    f2, f3, f4 = _XBH_SHARES
    weight = f2 * 1.0 + f3 * 2.0 + f4 * 3.0  # extra bases per XB hit
    xb_hits = extra / weight if weight > 0 else 0.0
    xb_hits = min(xb_hits, hit)  # cannot exceed total hits

    double = xb_hits * f2
    triple = xb_hits * f3
    hr = xb_hits * f4
    single = max(0.0, hit - (double + triple + hr))

    raw = [bb, single, double, triple, hr, out]
    total = sum(raw)
    bb, single, double, triple, hr, out = (v / total for v in raw)
    return EventRates(bb=bb, single=single, double=double, triple=triple, hr=hr, out=out)
