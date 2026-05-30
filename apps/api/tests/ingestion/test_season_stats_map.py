"""Verifies the season-stat mapper produces the evaluator stats_json schema:
numeric OPS/OBP/SLG, handedness, and a canonical primary_position. Prefers the
source's own OBP/SLG/OPS, derives SLG/OPS from counts when absent, and coerces
numeric strings to float so the evaluator never receives a string rate."""

from __future__ import annotations

from app.ingestion.season_stats_map import map_season_stats


def test_prefers_source_slg_ops_when_present() -> None:
    # The real Naver record.season rows carry obp/slg/ops as native numbers.
    raw = {
        "ab": 442,
        "hit": 122,
        "h2": 18,
        "h3": 2,
        "hr": 3,
        "obp": 0.379,
        "slg": 0.346,
        "ops": 0.725,
    }
    out = map_season_stats(raw, bats="L", position="CF")
    assert isinstance(out["OBP"], float) and abs(out["OBP"] - 0.379) < 1e-9
    assert isinstance(out["SLG"], float) and abs(out["SLG"] - 0.346) < 1e-9
    assert isinstance(out["OPS"], float) and abs(out["OPS"] - 0.725) < 1e-9
    assert out["handedness"] == "L"
    assert out["primary_position"] == "CF"


def test_derives_slg_and_ops_from_counts_when_absent() -> None:
    # SLG/OPS missing; OBP present. total_bases = (29-6-1-0) + 2*6 + 3*1 + 4*0 = 22+12+3 = 37
    raw = {"ab": 103, "hit": 29, "h2": 6, "h3": 1, "hr": 0, "obp": 0.348}
    out = map_season_stats(raw, bats="R", position="LF")
    assert abs(out["SLG"] - 37 / 103) < 1e-6
    assert abs(out["OPS"] - (0.348 + 37 / 103)) < 1e-6


def test_coerces_numeric_strings_to_float() -> None:
    raw = {"ab": "50", "hit": "20", "obp": "0.380", "slg": "0.500", "ops": "0.880"}
    out = map_season_stats(raw, bats="S", position="SS")
    assert isinstance(out["OPS"], float) and abs(out["OPS"] - 0.880) < 1e-9
    assert isinstance(out["SLG"], float) and abs(out["SLG"] - 0.500) < 1e-9
    assert out["handedness"] == "S"


def test_zero_ab_does_not_divide_by_zero() -> None:
    raw = {"ab": 0, "hit": 0, "obp": 0.0}
    out = map_season_stats(raw, bats=None, position=None)
    assert out["SLG"] == 0.0 and out["OPS"] == out["OBP"]
    assert out["handedness"] == "R"  # None bats -> default R
    assert out["primary_position"] == "DH"  # None position -> DH


def test_invalid_bats_falls_back_to_right() -> None:
    raw = {"ab": 10, "hit": 3, "obp": 0.3, "slg": 0.4, "ops": 0.7}
    out = map_season_stats(raw, bats="X", position="2B")
    assert out["handedness"] == "R"
