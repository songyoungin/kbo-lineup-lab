"""Tests for the opponent-starter quality calibration multiplier."""

from __future__ import annotations

from app.lineup_model.pitcher_quality import matchup_difficulty_multiplier


def test_ace_suppresses_toward_floor() -> None:
    # ERA 2.5, WHIP 1.10 -> (2.5/4.5 + 1.10/1.45)/2 ~= 0.66 -> clamp 0.9
    assert matchup_difficulty_multiplier(era=2.5, whip=1.10) == 0.9


def test_weak_pitcher_lifts_toward_ceiling() -> None:
    # ERA 6.0, WHIP 1.70 -> (1.33 + 1.17)/2 = 1.25 -> clamp 1.1
    assert matchup_difficulty_multiplier(era=6.0, whip=1.70) == 1.1


def test_average_pitcher_is_near_one() -> None:
    assert abs(matchup_difficulty_multiplier(era=4.5, whip=1.45) - 1.0) < 1e-9


def test_missing_inputs_default_to_one() -> None:
    assert matchup_difficulty_multiplier(era=None, whip=None) == 1.0
    assert matchup_difficulty_multiplier(era=3.0, whip=None) == 1.0
    assert matchup_difficulty_multiplier(era=0.0, whip=1.2) == 1.0
