"""Tests for the KBO 투수유형별 (pitcher-type) split parser."""

from __future__ import annotations

from pathlib import Path

from app.ingestion.kbo_parse import parse_pitcher_type_splits

_FIXTURE = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "sources"
    / "kbo"
    / "hitter_situation_66108.html"
)


def test_parse_pitcher_type_splits_reads_lhp_from_fixture() -> None:
    """좌투수 counting line is read verbatim from the fixture table."""
    out = parse_pitcher_type_splits(_FIXTURE.read_text(encoding="utf-8"))

    assert out is not None
    assert out["lhp"]["ab"] == 54
    assert out["lhp"]["h"] == 15
    assert out["lhp"]["bb"] == 10
    assert out["lhp"]["hbp"] == 2


def test_parse_pitcher_type_splits_folds_under_into_rhp() -> None:
    """우투수 totals absorb the 언더투수 (submarine) row exactly."""
    out = parse_pitcher_type_splits(_FIXTURE.read_text(encoding="utf-8"))

    assert out is not None
    assert out["rhp"]["ab"] == 111  # 우투수 109 + 언더 2
    assert out["rhp"]["bb"] == 29  # 우투수 29 + 언더 0
    assert out["rhp"]["gdp"] == 3  # 우투수 2 + 언더 1
    assert out["lhp"]["ab"] == 54  # lhp unaffected by the fold


def test_parse_pitcher_type_splits_returns_none_without_table() -> None:
    """Absent table yields None so the caller can record a needs-review reason."""
    assert parse_pitcher_type_splits("<html><body>no table</body></html>") is None
