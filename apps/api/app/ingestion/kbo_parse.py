"""Pure parsers for KBO official server-rendered HTML record pages.

No I/O. Each parser locates a labelled table and returns plain dicts, or None
when the expected structure is absent (caller records a needs-review reason).
"""

from __future__ import annotations

from bs4 import BeautifulSoup

# Column order of the 투수유형별 stat cells AFTER the leading label cell.
# Verified against tests/fixtures/sources/kbo/hitter_situation_66108.html header.
_SPLIT_COLS = ("avg", "ab", "h", "2b", "3b", "hr", "rbi", "bb", "hbp", "so", "gdp")


def _to_int(text: str) -> int:
    text = (text or "").strip().replace(",", "")
    return int(text) if text.lstrip("-").isdigit() else 0


def _to_float(text: str) -> float | None:
    text = (text or "").strip().replace(",", "")
    try:
        return float(text)
    except ValueError:
        return None


_GAME_LOG_DATE_LABELS = ("일자", "일시")
_TOTAL_ROW_LABELS = frozenset({"합계", "계", ""})


def _value_by_header(soup: BeautifulSoup, header: str) -> float | None:
    """Return the float under ``header`` in the season-total row of its table.

    KBO Basic pages stack the season line across several tables (a frozen
    leading column set plus scrollable sets); each such table carries its own
    <th> header row and one or more <td> data rows (one per team for traded
    players, plus a 합계 total), so the header index aligns with the data cells
    within the same table. Two hazards are guarded:

    * Header collision: the per-game log table renders a 합계 footer with <th>
      tags, so ERA/SO/TBF also appear in its header set. Such tables are
      skipped (header set contains "합계", or the first header is a date label).
    * Traded players: among data rows, a 합계/계/blank-labelled total row is
      preferred over a partial per-team row; otherwise the first data row.

    Returns None when no season table exposes ``header``.

    Args:
        soup: Parsed Basic.aspx document.
        header: Exact column header text (e.g. "RISP", "ERA").

    Returns:
        The parsed float, or None when the header or a numeric cell is absent.
    """
    for table in soup.find_all("table"):
        headers = [th.get_text(strip=True) for th in table.find_all("th")]
        if header not in headers:
            continue
        if "합계" in headers or (headers and headers[0] in _GAME_LOG_DATE_LABELS):
            continue  # game-log table: skip to avoid header collision
        idx = headers.index(header)
        data_rows = [tds for tr in table.find_all("tr") if len(tds := tr.find_all("td")) > idx]
        if not data_rows:
            continue
        total_row = next(
            (r for r in data_rows if r[0].get_text(strip=True) in _TOTAL_ROW_LABELS),
            data_rows[0],
        )
        return _to_float(total_row[idx].get_text(strip=True))
    return None


def _row_cells_by_label(soup: BeautifulSoup, label: str) -> list[str] | None:
    for tr in soup.find_all("tr"):
        cells = tr.find_all(["td", "th"])
        if cells and cells[0].get_text(strip=True) == label:
            return [c.get_text(strip=True) for c in cells]
    return None


def parse_pitcher_type_splits(html: str) -> dict[str, dict[str, int]] | None:
    """Extract vs-left/right pitcher counting lines from the 투수유형별 table.

    The 언더투수 (submarine) row is folded into the right-handed totals.

    Args:
        html: Server-rendered HTML of a HitterDetail/Situation.aspx page.

    Returns:
        ``{"lhp": {...}, "rhp": {...}}`` of counting stats, or None when the
        table or both split rows are absent.
    """
    if "투수유형별" not in html:
        return None
    soup = BeautifulSoup(html, "html.parser")

    def counts(label: str) -> dict[str, int] | None:
        cells = _row_cells_by_label(soup, label)
        if cells is None:
            return None
        vals = cells[1 : 1 + len(_SPLIT_COLS)]
        return {col: _to_int(v) for col, v in zip(_SPLIT_COLS, vals, strict=False)}

    lhp = counts("좌투수")
    rhp = counts("우투수")
    if lhp is None and rhp is None:
        return None
    lhp = lhp or {c: 0 for c in _SPLIT_COLS}
    rhp = rhp or {c: 0 for c in _SPLIT_COLS}
    under = counts("언더투수")
    if under is not None:
        for c in _SPLIT_COLS:
            if c != "avg":
                rhp[c] += under.get(c, 0)
    return {"lhp": lhp, "rhp": rhp}


def parse_hitter_risp(html: str) -> float | None:
    """Extract the season RISP (득점권타율) from a HitterDetail/Basic.aspx page.

    Args:
        html: Server-rendered HTML of a hitter Basic page.

    Returns:
        The RISP batting average as a float, or None when the column is absent.
    """
    return _value_by_header(BeautifulSoup(html, "html.parser"), "RISP")


def parse_pitcher_basic(html: str) -> dict[str, float] | None:
    """Extract season ERA/WHIP/SO/TBF from a PitcherDetail/Basic.aspx page.

    ``k_pct`` is intentionally not derived here; the caller computes it from
    SO/TBF.

    Args:
        html: Server-rendered HTML of a pitcher Basic page.

    Returns:
        ``{"era", "whip", "so", "tbf"}`` of floats, or None when ERA, WHIP, or
        TBF is missing (or TBF is zero).
    """
    soup = BeautifulSoup(html, "html.parser")
    era = _value_by_header(soup, "ERA")
    whip = _value_by_header(soup, "WHIP")
    so = _value_by_header(soup, "SO")
    tbf = _value_by_header(soup, "TBF")
    if era is None or whip is None or tbf is None or tbf == 0:
        return None
    return {"era": era, "whip": whip, "so": so or 0.0, "tbf": tbf}
