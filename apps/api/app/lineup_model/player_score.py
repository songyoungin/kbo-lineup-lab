"""Pure functions for scoring an individual hitter.

Component weights (sum to 1.0):
  season_offense  35 %
  recent_form     30 %
  matchup         20 %
  position_fit    10 %
  start_rhythm     5 %

All functions are deterministic and free of side effects.
"""

from __future__ import annotations

from app.lineup_model.types import (
    Handedness,
    HitterStats,
    PlayerScoreBreakdown,
    Position,
    ScoringReason,
)

# ---------------------------------------------------------------------------
# Component weights
# ---------------------------------------------------------------------------

_W_SEASON = 0.35
_W_RECENT = 0.30
_W_MATCHUP = 0.20
_W_POSITION = 0.10
_W_RHYTHM = 0.05

# ---------------------------------------------------------------------------
# Season offense
# ---------------------------------------------------------------------------


def season_offense(stats: HitterStats) -> tuple[float, ScoringReason]:
    """Compute season offense score: OPS 60 % + OBP 25 % + SLG 15 %.

    Args:
        stats: Hitter season statistics.

    Returns:
        (score, reason) where score is in [0, 2+] (raw rate-stat space).
    """
    score = 0.60 * stats.ops + 0.25 * stats.obp + 0.15 * stats.slg
    reason = ScoringReason(
        component="season_offense",
        value=score,
        weight=_W_SEASON,
        note=f"OPS={stats.ops:.3f} OBP={stats.obp:.3f} SLG={stats.slg:.3f}",
    )
    return score, reason


# ---------------------------------------------------------------------------
# Recent form
# ---------------------------------------------------------------------------


def recent_form(stats: HitterStats) -> tuple[float, ScoringReason]:
    """Compute recent-form score: 14-day OPS 70 % + 30-day OPS 30 %.

    Falls back to season OPS for each missing component, with a note
    indicating the substitution.  If both are missing the baseline is
    the season OPS (same as a full-fallback scenario).

    Args:
        stats: Hitter statistics including optional recent fields.

    Returns:
        (score, reason).
    """
    fourteen = stats.recent_14d_ops
    thirty = stats.recent_30d_ops

    note_parts: list[str] = []
    if fourteen is None:
        fourteen = stats.ops
        note_parts.append("14d fallback=season")
    if thirty is None:
        thirty = stats.ops
        note_parts.append("30d fallback=season")

    score = 0.70 * fourteen + 0.30 * thirty
    if note_parts:
        note = "; ".join(note_parts)
    else:
        # Both fields are present at this point (covered the missing-field branches above).
        note = f"14d={stats.recent_14d_ops:.3f} 30d={stats.recent_30d_ops:.3f}"

    reason = ScoringReason(
        component="recent_form",
        value=score,
        weight=_W_RECENT,
        note=note,
    )
    return score, reason


# ---------------------------------------------------------------------------
# Handedness matchup
# ---------------------------------------------------------------------------


def matchup_score(stats: HitterStats, opp_handedness: Handedness) -> tuple[float, ScoringReason]:
    """Compute handedness matchup score with PA-threshold confidence blending.

    PA thresholds (vs the relevant side):
      >= 80 PA : split 100 %
      40-79 PA : split 70 % + season 30 %
      20-39 PA : split 40 % + season 60 %
      < 20 PA  : season OPS 100 % (no split data trusted)

    Switch hitters are treated as always having the favourable side — we
    use the split if available, otherwise season OPS.

    Args:
        stats: Hitter statistics with optional split fields.
        opp_handedness: Handedness of the opposing starter.

    Returns:
        (score, reason).
    """
    if opp_handedness in (Handedness.RIGHT, Handedness.SWITCH):
        split_ops = stats.vs_rhp_ops
        pa = stats.vs_rhp_pa
        side = "RHP"
    else:
        split_ops = stats.vs_lhp_ops
        pa = stats.vs_lhp_pa
        side = "LHP"

    season_ops = stats.ops

    if split_ops is None or pa < 20:
        score = season_ops
        note = f"vs_{side}: PA={pa} <20 or no split, using season OPS={season_ops:.3f}"
    elif pa < 40:
        score = 0.40 * split_ops + 0.60 * season_ops
        note = (
            f"vs_{side}: PA={pa} 20-39, blend 40/60 split={split_ops:.3f} season={season_ops:.3f}"
        )
    elif pa < 80:
        score = 0.70 * split_ops + 0.30 * season_ops
        note = (
            f"vs_{side}: PA={pa} 40-79, blend 70/30 split={split_ops:.3f} season={season_ops:.3f}"
        )
    else:
        score = split_ops
        note = f"vs_{side}: PA={pa} >=80, split={split_ops:.3f} full confidence"

    reason = ScoringReason(
        component="matchup",
        value=score,
        weight=_W_MATCHUP,
        note=note,
    )
    return score, reason


# ---------------------------------------------------------------------------
# Position fit
# ---------------------------------------------------------------------------

# Scores are in [0, 100] then normalised to [0, 1] before weighting.
_POS_PRIMARY = 100.0
_POS_SECONDARY = 80.0
_POS_RECENT = 65.0


def position_fit(stats: HitterStats, slot_position: Position) -> tuple[float, ScoringReason] | None:
    """Compute position eligibility score for a specific slot.

    Returns None when the player cannot legally play this position
    (impossible position — blocks the slot assignment entirely).

    Scoring:
      primary position  → 100
      secondary position → 80
      recent position   → 65
      impossible         → None (blocked)

    Args:
        stats: Hitter statistics with positional eligibility.
        slot_position: The defensive position being evaluated.

    Returns:
        (score_in_0_1, reason) or None if impossible.
    """
    if slot_position == stats.primary_position:
        raw = _POS_PRIMARY
        tier = "primary"
    elif slot_position in stats.secondary_positions:
        raw = _POS_SECONDARY
        tier = "secondary"
    elif slot_position in stats.recent_positions:
        raw = _POS_RECENT
        tier = "recent"
    else:
        # Impossible: no eligibility at this position
        return None

    score = raw / 100.0  # normalise to [0, 1]
    reason = ScoringReason(
        component="position_fit",
        value=score,
        weight=_W_POSITION,
        note=f"pos={slot_position} tier={tier} raw={raw}",
    )
    return score, reason


# ---------------------------------------------------------------------------
# Start rhythm
# ---------------------------------------------------------------------------


def start_rhythm(stats: HitterStats) -> tuple[float, ScoringReason]:
    """Compute start-rhythm score based on recent starts.

    Bands (normalised to 0–1):
      3-5 starts in last 5 games → 100 → 1.0
      1-2 starts in last 5 games →  80 → 0.8
      0 starts in last 5 games   →  60 → 0.6

    Args:
        stats: Hitter statistics.

    Returns:
        (score, reason).
    """
    s = stats.starts_last_5_games
    if s >= 3:
        raw = 100.0
        band = "regular-starter"
    elif s >= 1:
        raw = 80.0
        band = "occasional"
    else:
        raw = 60.0
        band = "benched"

    score = raw / 100.0
    reason = ScoringReason(
        component="start_rhythm",
        value=score,
        weight=_W_RHYTHM,
        note=f"starts_last_5={s} band={band}",
    )
    return score, reason


# ---------------------------------------------------------------------------
# Composite player score
# ---------------------------------------------------------------------------


def compute_player_score(
    stats: HitterStats,
    slot_position: Position,
    opp_handedness: Handedness,
) -> PlayerScoreBreakdown | None:
    """Combine all five components into a single player score.

    Returns None when position_fit returns None (impossible position).

    Weights: season_offense 35 %, recent_form 30 %, matchup 20 %,
             position_fit 10 %, start_rhythm 5 %.

    The season_offense and matchup scores live in OPS-space (~0.6–1.0)
    while position_fit and start_rhythm are normalised to [0.6, 1.0].
    recent_form inherits the same scale as season_offense.  The composite
    is therefore a weighted average in a consistent rate-stat space.

    Args:
        stats: Hitter statistics.
        slot_position: Defensive position for this slot.
        opp_handedness: Opposing starter's handedness.

    Returns:
        PlayerScoreBreakdown or None if the position is impossible.
    """
    pos_result = position_fit(stats, slot_position)
    if pos_result is None:
        return None

    s_off, r_off = season_offense(stats)
    s_rec, r_rec = recent_form(stats)
    s_mat, r_mat = matchup_score(stats, opp_handedness)
    s_pos, r_pos = pos_result
    s_rhy, r_rhy = start_rhythm(stats)

    total = (
        _W_SEASON * s_off
        + _W_RECENT * s_rec
        + _W_MATCHUP * s_mat
        + _W_POSITION * s_pos
        + _W_RHYTHM * s_rhy
    )

    return PlayerScoreBreakdown(
        player_id=stats.player_id,
        total_score=total,
        reasons=(r_off, r_rec, r_mat, r_pos, r_rhy),
    )
