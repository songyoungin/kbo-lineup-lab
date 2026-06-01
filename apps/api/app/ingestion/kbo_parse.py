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
