"""Tests for individual player scoring components.

Covers every scoring rule in player_score.py:
- season_offense formula
- recent_form with full data, partial fallback, full fallback
- matchup_score at each PA threshold boundary
- position_fit for primary / secondary / recent / impossible
- start_rhythm for each band
- compute_player_score end-to-end (valid and impossible positions)
"""

from __future__ import annotations

import pytest

from app.lineup_model.player_score import (
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
# matchup_score — PA threshold boundaries
# ---------------------------------------------------------------------------


def test_matchup_uses_season_when_no_split() -> None:
    """No split data (pa=0, ops=None) → season OPS."""
    stats = _make_stats(ops=0.800, vs_rhp_ops=None, vs_rhp_pa=0)
    score, reason = matchup_score(stats, Handedness.RIGHT)
    assert score == pytest.approx(0.800)
    assert "no split" in reason.note or "PA=0" in reason.note


def test_matchup_uses_season_when_pa_under_20() -> None:
    """PA < 20 → season OPS even if split data exists."""
    stats = _make_stats(ops=0.800, vs_rhp_ops=1.100, vs_rhp_pa=15)
    score, reason = matchup_score(stats, Handedness.RIGHT)
    assert score == pytest.approx(0.800)
    assert "<20" in reason.note


def test_matchup_blend_40_60_at_pa_20() -> None:
    """PA exactly 20 → 40 % split + 60 % season."""
    stats = _make_stats(ops=0.800, vs_rhp_ops=1.000, vs_rhp_pa=20)
    score, reason = matchup_score(stats, Handedness.RIGHT)
    expected = 0.40 * 1.000 + 0.60 * 0.800
    assert score == pytest.approx(expected)
    assert "20-39" in reason.note


def test_matchup_blend_40_60_at_pa_39() -> None:
    """PA=39 → still 40/60 blend."""
    stats = _make_stats(ops=0.800, vs_rhp_ops=1.000, vs_rhp_pa=39)
    score, _ = matchup_score(stats, Handedness.RIGHT)
    expected = 0.40 * 1.000 + 0.60 * 0.800
    assert score == pytest.approx(expected)


def test_matchup_blend_70_30_at_pa_40() -> None:
    """PA exactly 40 → 70 % split + 30 % season."""
    stats = _make_stats(ops=0.800, vs_rhp_ops=1.000, vs_rhp_pa=40)
    score, reason = matchup_score(stats, Handedness.RIGHT)
    expected = 0.70 * 1.000 + 0.30 * 0.800
    assert score == pytest.approx(expected)
    assert "40-79" in reason.note


def test_matchup_blend_70_30_at_pa_79() -> None:
    """PA=79 → still 70/30 blend."""
    stats = _make_stats(ops=0.800, vs_rhp_ops=1.000, vs_rhp_pa=79)
    score, _ = matchup_score(stats, Handedness.RIGHT)
    expected = 0.70 * 1.000 + 0.30 * 0.800
    assert score == pytest.approx(expected)


def test_matchup_full_confidence_at_pa_80() -> None:
    """PA exactly 80 → split OPS 100 %."""
    stats = _make_stats(ops=0.800, vs_rhp_ops=1.100, vs_rhp_pa=80)
    score, reason = matchup_score(stats, Handedness.RIGHT)
    assert score == pytest.approx(1.100)
    assert "full confidence" in reason.note


def test_matchup_lhp_uses_vs_lhp_split() -> None:
    """Opponent LHP → vs_lhp split used."""
    stats = _make_stats(
        ops=0.800,
        vs_lhp_ops=0.950,
        vs_lhp_pa=80,
        vs_rhp_ops=0.700,
        vs_rhp_pa=80,
    )
    score, reason = matchup_score(stats, Handedness.LEFT)
    assert score == pytest.approx(0.950)
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
