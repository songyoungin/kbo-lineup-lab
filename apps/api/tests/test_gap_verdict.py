"""Tests for the shared lineup-gap classifier.

Verifies the run-expectancy-scale thresholds and, crucially, that the pregame
verdict and postgame gap label agree on every band (no contradictory labels for
the same gap).
"""

from __future__ import annotations

from app.lineup_model.gap_verdict import (
    GAP_ACCEPTABLE,
    GAP_NEARLY_OPTIMAL,
    GAP_QUESTIONABLE,
    GapTier,
    classify_gap,
)
from app.postgame.review_generator import _pick_gap_label
from app.schemas.pregame import derive_verdict


def test_classify_gap_nearly_optimal() -> None:
    """Gap >= -0.15 (incl. batting-order-only diffs ~-0.10) is nearly optimal."""
    assert classify_gap(0.0) is GapTier.NEARLY_OPTIMAL
    assert classify_gap(-0.10) is GapTier.NEARLY_OPTIMAL
    assert classify_gap(GAP_NEARLY_OPTIMAL) is GapTier.NEARLY_OPTIMAL


def test_classify_gap_acceptable() -> None:
    """Gap in [-0.40, -0.15) is acceptable."""
    assert classify_gap(-0.20) is GapTier.ACCEPTABLE
    assert classify_gap(GAP_ACCEPTABLE) is GapTier.ACCEPTABLE


def test_classify_gap_questionable() -> None:
    """Gap in [-0.80, -0.40) is questionable (e.g. benching ~1-2 real bats)."""
    assert classify_gap(-0.66) is GapTier.QUESTIONABLE
    assert classify_gap(GAP_QUESTIONABLE) is GapTier.QUESTIONABLE


def test_classify_gap_low() -> None:
    """Gap < -0.80 is low offensive efficiency."""
    assert classify_gap(-0.81) is GapTier.LOW
    assert classify_gap(-1.12) is GapTier.LOW


def test_pregame_and_postgame_agree_on_every_band() -> None:
    """The same gap must map to the same tier in both layers (no contradiction)."""
    verdict_tier = {
        "Nearly optimal": GapTier.NEARLY_OPTIMAL,
        "Acceptable": GapTier.ACCEPTABLE,
        "Questionable": GapTier.QUESTIONABLE,
        "Low offensive efficiency": GapTier.LOW,
    }
    label_tier = {
        "nearly optimal": GapTier.NEARLY_OPTIMAL,
        "acceptable": GapTier.ACCEPTABLE,
        "questionable": GapTier.QUESTIONABLE,
        "low offensive efficiency": GapTier.LOW,
    }
    for gap in (0.0, -0.10, -0.15, -0.25, -0.40, -0.55, -0.80, -0.81, -1.5):
        expected = classify_gap(gap)
        assert verdict_tier[derive_verdict(gap)] is expected
        assert label_tier[_pick_gap_label(gap)] is expected
