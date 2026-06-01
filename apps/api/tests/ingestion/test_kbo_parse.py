"""Tests for the KBO 투수유형별 (pitcher-type) split parser."""

from __future__ import annotations

from pathlib import Path

from app.ingestion.kbo_parse import (
    parse_hitter_risp,
    parse_pitcher_basic,
    parse_pitcher_type_splits,
)

_FIX = Path(__file__).resolve().parents[1] / "fixtures" / "sources" / "kbo"
_FIXTURE = _FIX / "hitter_situation_66108.html"


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


def test_parse_hitter_risp_returns_float() -> None:
    """RISP (득점권타율) is read from the hitter Basic season table."""
    html = (_FIX / "hitter_basic_66108.html").read_text(encoding="utf-8")
    risp = parse_hitter_risp(html)
    assert risp is not None and 0.0 <= risp <= 1.0
    assert risp == 0.235  # verbatim from the fixture season row


def test_parse_pitcher_basic_extracts_era_whip_so_tbf() -> None:
    """ERA/WHIP/SO/TBF are read from the pitcher Basic season tables."""
    html = (_FIX / "pitcher_basic_55322.html").read_text(encoding="utf-8")
    out = parse_pitcher_basic(html)
    assert out is not None
    assert out["era"] > 0 and out["whip"] > 0
    assert out["so"] >= 0 and out["tbf"] > 0
    assert out == {"era": 3.18, "whip": 1.59, "so": 14.0, "tbf": 52.0}


def test_parse_pitcher_basic_missing_returns_none() -> None:
    """Absent tables yield None so the caller can record a needs-review reason."""
    assert parse_pitcher_basic("<html></html>") is None


def test_value_by_header_ignores_game_log_total_footer() -> None:
    """ERA from the season table wins over a game-log 합계 header collision."""
    html = """
    <table>
      <tr><th>ERA</th><th>WHIP</th><th>TBF</th></tr>
      <tr><td>3.18</td><td>1.59</td><td>52</td></tr>
    </table>
    <table>
      <tr><th>일자</th><th>합계</th><th>ERA</th></tr>
      <tr><td>05.28</td><td></td><td>5.40</td></tr>
    </table>
    """
    out = parse_pitcher_basic(html)
    assert out is not None
    assert out["era"] == 3.18  # season table, not the 5.40 game-log footer


def test_value_by_header_prefers_total_row_for_traded_player() -> None:
    """Traded-player season tables expose per-team rows + a 합계 total row."""
    html = """
    <table>
      <tr><th>팀명</th><th>ERA</th><th>WHIP</th><th>TBF</th></tr>
      <tr><td>A</td><td>4.00</td><td>1.20</td><td>30</td></tr>
      <tr><td>B</td><td>6.00</td><td>1.80</td><td>20</td></tr>
      <tr><td>합계</td><td>5.00</td><td>1.50</td><td>50</td></tr>
    </table>
    """
    out = parse_pitcher_basic(html)
    assert out is not None
    assert out["era"] == 5.00  # 합계 total, not the first per-team row (4.00)
