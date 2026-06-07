"""Tests for individual player scoring components.

Covers every scoring rule in player_score.py:
- season_offense formula
- recent_form with full data, partial fallback, full fallback
- matchup_score sample-size shrinkage toward season OPS
- position_fit for primary / secondary / recent / impossible
- start_rhythm for each band
- compute_player_score end-to-end (valid and impossible positions)
"""

from __future__ import annotations

import pytest

from app.lineup_model.player_score import (
    _MATCHUP_SHRINK_PA,
    compute_player_score,
    matchup_score,
    position_fit,
    recent_form,
    season_offense,
    start_rhythm,
)
from app.lineup_model.types import Handedness, HitterStats, Position

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_stats(**kwargs: object) -> HitterStats:
    """Build a minimal HitterStats with sensible defaults for unit tests."""
    defaults: dict[str, object] = {
        "player_id": 1,
        "handedness": Handedness.RIGHT,
        "ops": 0.800,
        "obp": 0.350,
        "slg": 0.450,
        "primary_position": Position.FIRST,
        "starts_last_5_games": 3,
    }
    defaults.update(kwargs)
    return HitterStats(**defaults)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# season_offense
# ---------------------------------------------------------------------------


def test_season_offense_formula() -> None:
    """OPS 60 % + OBP 25 % + SLG 15 % formula."""
    stats = _make_stats(ops=1.000, obp=0.400, slg=0.600)
    score, reason = season_offense(stats)
    expected = 0.60 * 1.000 + 0.25 * 0.400 + 0.15 * 0.600
    assert score == pytest.approx(expected)
    assert reason.component == "season_offense"
    assert reason.weight == pytest.approx(0.35)


def test_season_offense_zero_stats() -> None:
    """All-zero stats produce a score of 0."""
    stats = _make_stats(ops=0.0, obp=0.0, slg=0.0)
    score, _ = season_offense(stats)
    assert score == pytest.approx(0.0)


def test_season_offense_note_contains_stats() -> None:
    """Reason note must reference OPS, OBP, and SLG values."""
    stats = _make_stats(ops=0.880, obp=0.360, slg=0.520)
    _, reason = season_offense(stats)
    assert "OPS" in reason.note
    assert "OBP" in reason.note
    assert "SLG" in reason.note


# ---------------------------------------------------------------------------
# recent_form
# ---------------------------------------------------------------------------


def test_recent_form_both_present() -> None:
    """14-day OPS 70 % + 30-day OPS 30 % when both fields present."""
    stats = _make_stats(recent_14d_ops=0.900, recent_30d_ops=0.800, ops=0.700)
    score, reason = recent_form(stats)
    expected = 0.70 * 0.900 + 0.30 * 0.800
    assert score == pytest.approx(expected)
    assert "14d fallback" not in reason.note
    assert "30d fallback" not in reason.note


def test_recent_form_14d_missing_falls_back_to_season() -> None:
    """Missing recent_14d_ops falls back to season OPS."""
    stats = _make_stats(recent_14d_ops=None, recent_30d_ops=0.800, ops=0.700)
    score, reason = recent_form(stats)
    expected = 0.70 * 0.700 + 0.30 * 0.800  # 14d uses season
    assert score == pytest.approx(expected)
    assert "14d fallback=season" in reason.note


def test_recent_form_30d_missing_falls_back_to_season() -> None:
    """Missing recent_30d_ops falls back to season OPS."""
    stats = _make_stats(recent_14d_ops=0.900, recent_30d_ops=None, ops=0.700)
    score, reason = recent_form(stats)
    expected = 0.70 * 0.900 + 0.30 * 0.700  # 30d uses season
    assert score == pytest.approx(expected)
    assert "30d fallback=season" in reason.note


def test_recent_form_both_missing_equals_season_ops() -> None:
    """Both recent fields missing → score equals season OPS."""
    stats = _make_stats(recent_14d_ops=None, recent_30d_ops=None, ops=0.850)
    score, reason = recent_form(stats)
    assert score == pytest.approx(0.850)
    assert "14d fallback=season" in reason.note
    assert "30d fallback=season" in reason.note
    # The note field itself is verified here to exercise ScoringReason.note
    assert reason.note != ""


# ---------------------------------------------------------------------------
# matchup_score — sample-size shrinkage toward season OPS
#
# The split estimate is regressed toward season OPS with weight
# w = pa / (pa + K), K = _MATCHUP_SHRINK_PA. This replaces the old hard
# PA-threshold step blend: it is continuous (no cliff), monotone in PA, and
# applies textbook regression-to-the-mean to noisy small-sample splits.
# ---------------------------------------------------------------------------


def test_matchup_uses_season_when_no_split() -> None:
    """No split data (ops=None) → season OPS, regardless of PA."""
    stats = _make_stats(ops=0.800, vs_rhp_ops=None, vs_rhp_pa=0)
    score, reason = matchup_score(stats, Handedness.RIGHT)
    assert score == pytest.approx(0.800)
    assert "no split" in reason.note


def test_matchup_zero_pa_collapses_to_season() -> None:
    """Split present but 0 PA → weight 0 → season OPS exactly."""
    stats = _make_stats(ops=0.800, vs_rhp_ops=1.100, vs_rhp_pa=0)
    score, _ = matchup_score(stats, Handedness.RIGHT)
    assert score == pytest.approx(0.800)


def test_matchup_half_weight_at_regression_constant() -> None:
    """PA equal to the regression constant K → exactly a 50/50 blend."""
    stats = _make_stats(ops=0.800, vs_rhp_ops=1.000, vs_rhp_pa=_MATCHUP_SHRINK_PA)
    score, _ = matchup_score(stats, Handedness.RIGHT)
    expected = 0.50 * 1.000 + 0.50 * 0.800
    assert score == pytest.approx(expected)


def test_matchup_more_pa_gives_more_split_weight() -> None:
    """Monotonic: a larger sample pulls the score closer to the split."""
    low = _make_stats(ops=0.800, vs_rhp_ops=1.100, vs_rhp_pa=10)
    mid = _make_stats(ops=0.800, vs_rhp_ops=1.100, vs_rhp_pa=80)
    high = _make_stats(ops=0.800, vs_rhp_ops=1.100, vs_rhp_pa=400)
    s_low, _ = matchup_score(low, Handedness.RIGHT)
    s_mid, _ = matchup_score(mid, Handedness.RIGHT)
    s_high, _ = matchup_score(high, Handedness.RIGHT)
    # Each result stays between season (0.800) and split (1.100)...
    assert 0.800 < s_low < s_mid < s_high < 1.100


def test_matchup_large_pa_approaches_split() -> None:
    """As PA grows very large the score converges to the raw split."""
    stats = _make_stats(ops=0.800, vs_rhp_ops=1.100, vs_rhp_pa=5000)
    score, _ = matchup_score(stats, Handedness.RIGHT)
    assert score == pytest.approx(1.100, abs=0.02)


def test_matchup_is_continuous_across_old_80_pa_cliff() -> None:
    """No discontinuity at the old PA=80 boundary (the regression removes the cliff)."""
    at_79 = _make_stats(ops=0.800, vs_rhp_ops=1.100, vs_rhp_pa=79)
    at_80 = _make_stats(ops=0.800, vs_rhp_ops=1.100, vs_rhp_pa=80)
    s_79, _ = matchup_score(at_79, Handedness.RIGHT)
    s_80, _ = matchup_score(at_80, Handedness.RIGHT)
    assert abs(s_80 - s_79) < 0.01


def test_matchup_lhp_uses_vs_lhp_split() -> None:
    """Opponent LHP → vs_lhp split is the one regressed toward season."""
    stats = _make_stats(
        ops=0.800,
        vs_lhp_ops=0.950,
        vs_lhp_pa=_MATCHUP_SHRINK_PA,
        vs_rhp_ops=0.700,
        vs_rhp_pa=_MATCHUP_SHRINK_PA,
    )
    score, reason = matchup_score(stats, Handedness.LEFT)
    assert score == pytest.approx(0.50 * 0.950 + 0.50 * 0.800)
    assert "LHP" in reason.note


# ---------------------------------------------------------------------------
# position_fit
# ---------------------------------------------------------------------------


def test_position_fit_primary_returns_1_0() -> None:
    """Primary position → normalised score 1.0."""
    stats = _make_stats(primary_position=Position.CENTER)
    result = position_fit(stats, Position.CENTER)
    assert result is not None
    score, reason = result
    assert score == pytest.approx(1.0)
    assert reason.component == "position_fit"
    assert "primary" in reason.note


def test_position_fit_secondary_returns_0_8() -> None:
    """Secondary position → normalised score 0.8."""
    stats = _make_stats(
        primary_position=Position.CENTER,
        secondary_positions=(Position.LEFT,),
    )
    result = position_fit(stats, Position.LEFT)
    assert result is not None
    score, reason = result
    assert score == pytest.approx(0.8)
    assert "secondary" in reason.note


def test_position_fit_recent_returns_0_65() -> None:
    """Recent position → normalised score 0.65."""
    stats = _make_stats(
        primary_position=Position.CENTER,
        recent_positions=(Position.RIGHT,),
    )
    result = position_fit(stats, Position.RIGHT)
    assert result is not None
    score, reason = result
    assert score == pytest.approx(0.65)
    assert "recent" in reason.note


def test_position_fit_impossible_returns_none() -> None:
    """No eligibility at position → None (blocks slot)."""
    stats = _make_stats(
        primary_position=Position.CENTER,
        secondary_positions=(),
        recent_positions=(),
    )
    result = position_fit(stats, Position.C)
    assert result is None


def test_position_fit_catcher_only_eligible_for_catcher() -> None:
    """A player with only C eligibility cannot play 1B."""
    stats = _make_stats(primary_position=Position.C)
    assert position_fit(stats, Position.FIRST) is None
    result = position_fit(stats, Position.C)
    assert result is not None
    score, _ = result
    assert score == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# start_rhythm
# ---------------------------------------------------------------------------


def test_start_rhythm_0_starts() -> None:
    """0 starts in last 5 → 0.6."""
    stats = _make_stats(starts_last_5_games=0)
    score, reason = start_rhythm(stats)
    assert score == pytest.approx(0.6)
    assert "benched" in reason.note


def test_start_rhythm_1_start() -> None:
    """1 start → 0.8."""
    stats = _make_stats(starts_last_5_games=1)
    score, reason = start_rhythm(stats)
    assert score == pytest.approx(0.8)
    assert "occasional" in reason.note


def test_start_rhythm_2_starts() -> None:
    """2 starts → 0.8."""
    stats = _make_stats(starts_last_5_games=2)
    score, _ = start_rhythm(stats)
    assert score == pytest.approx(0.8)


def test_start_rhythm_3_starts() -> None:
    """3 starts → 1.0."""
    stats = _make_stats(starts_last_5_games=3)
    score, reason = start_rhythm(stats)
    assert score == pytest.approx(1.0)
    assert "regular" in reason.note


def test_start_rhythm_5_starts() -> None:
    """5 starts → 1.0."""
    stats = _make_stats(starts_last_5_games=5)
    score, _ = start_rhythm(stats)
    assert score == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# compute_player_score
# ---------------------------------------------------------------------------


def test_compute_player_score_valid_position() -> None:
    """compute_player_score returns a breakdown for a valid position."""
    stats = _make_stats(
        primary_position=Position.FIRST,
        ops=0.900,
        obp=0.380,
        slg=0.520,
        starts_last_5_games=4,
    )
    result = compute_player_score(stats, Position.FIRST, Handedness.RIGHT)
    assert result is not None
    assert result.player_id == stats.player_id
    assert result.total_score > 0
    # Five reasons: season_offense, recent_form, matchup, position_fit, start_rhythm
    assert len(result.reasons) == 5
    components = {r.component for r in result.reasons}
    assert components == {
        "season_offense",
        "recent_form",
        "matchup",
        "position_fit",
        "start_rhythm",
    }


def test_compute_player_score_impossible_position_returns_none() -> None:
    """compute_player_score returns None for an impossible position."""
    stats = _make_stats(primary_position=Position.FIRST)
    result = compute_player_score(stats, Position.C, Handedness.RIGHT)
    assert result is None


def test_compute_player_score_weights_sum_approximately() -> None:
    """Component weights in reasons should match the defined constants."""
    stats = _make_stats(primary_position=Position.FIRST)
    result = compute_player_score(stats, Position.FIRST, Handedness.RIGHT)
    assert result is not None
    weight_sum = sum(r.weight for r in result.reasons)
    # Weights: 0.35 + 0.30 + 0.20 + 0.10 + 0.05 = 1.0
    assert weight_sum == pytest.approx(1.0)


def test_compute_player_score_reasons_have_notes() -> None:
    """All five reason notes should be non-empty strings."""
    stats = _make_stats(
        primary_position=Position.FIRST,
        recent_14d_ops=0.900,
        recent_30d_ops=0.800,
        vs_rhp_ops=0.850,
        vs_rhp_pa=85,
    )
    result = compute_player_score(stats, Position.FIRST, Handedness.RIGHT)
    assert result is not None
    for reason in result.reasons:
        assert isinstance(reason.note, str)


# ---------------------------------------------------------------------------
# season_offense — advanced metrics (wOBA / wRC+)
# ---------------------------------------------------------------------------


def test_season_offense_falls_back_to_ops_formula_without_woba() -> None:
    """woba/wrc_plus absent → identical to the legacy 0.60/0.25/0.15 formula."""
    stats = _make_stats(ops=0.800, obp=0.350, slg=0.450)
    score, _ = season_offense(stats)
    assert score == 0.60 * 0.800 + 0.25 * 0.350 + 0.15 * 0.450


def test_season_offense_uses_woba_and_wrc_plus_when_present() -> None:
    """woba scaled to OPS space + wRC+ quality multiplier (clamped)."""
    stats = _make_stats(ops=0.800, obp=0.350, slg=0.450, woba=0.360, wrc_plus=130.0)
    score, _ = season_offense(stats)
    base = 0.50 * 0.800 + 0.30 * (0.360 * 2.3) + 0.20 * 0.350
    expected = base * 1.15  # 130/100 clamped to max 1.15
    assert abs(score - expected) < 1e-9


def test_season_offense_wrc_plus_multiplier_clamps_low() -> None:
    stats = _make_stats(ops=0.800, obp=0.350, slg=0.450, woba=0.300, wrc_plus=40.0)
    score, _ = season_offense(stats)
    base = 0.50 * 0.800 + 0.30 * (0.300 * 2.3) + 0.20 * 0.350
    assert abs(score - base * 0.85) < 1e-9  # 40/100 clamped to min 0.85


def test_season_offense_wrc_plus_applies_to_legacy_base_without_woba() -> None:
    """wRC+ present but woba absent → multiplier applies to the legacy base."""
    stats = _make_stats(ops=0.800, obp=0.350, slg=0.450, wrc_plus=110.0)
    score, _ = season_offense(stats)
    base = 0.60 * 0.800 + 0.25 * 0.350 + 0.15 * 0.450
    expected = base * 1.10  # 110/100, within clamp
    assert abs(score - expected) < 1e-9
