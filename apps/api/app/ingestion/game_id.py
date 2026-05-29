"""KBO game_id parsing/formatting and KBO<->Naver conversion.

KBO G_ID format: YYYYMMDD + awayTeamCode(2) + homeTeamCode(2) + sequence(1),
e.g. "20250514WOLG0" = 2025-05-14, Kiwoom(WO) @ LG, game 0. Naver's gameId is
the same string with the 4-digit season year appended ("...WOLG02025").
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Final

__all__ = ["TEAM_CODES", "GameId", "parse_kbo_game_id", "kbo_to_naver", "naver_to_kbo"]

# Franchise-tied two-letter codes used in KBO game_ids.
TEAM_CODES: Final[dict[str, str]] = {
    "LG": "LG Twins",
    "OB": "Doosan Bears",
    "WO": "Kiwoom Heroes",
    "SK": "SSG Landers",
    "HT": "KIA Tigers",
    "SS": "Samsung Lions",
    "LT": "Lotte Giants",
    "HH": "Hanwha Eagles",
    "NC": "NC Dinos",
    "KT": "KT Wiz",
}

_KBO_LEN: Final = 13  # 8 date + 2 away + 2 home + 1 seq


@dataclass(frozen=True)
class GameId:
    """Decoded KBO game id."""

    date: date
    away: str
    home: str
    seq: str


def parse_kbo_game_id(game_id: str) -> GameId:
    """Decode a KBO G_ID string into its parts.

    Args:
        game_id: KBO G_ID string (e.g. "20250514WOLG0").

    Returns:
        The decoded :class:`GameId`.

    Raises:
        ValueError: If the id is not 13 characters or the date is unparseable.
    """
    if len(game_id) != _KBO_LEN:
        raise ValueError(f"KBO game_id must be {_KBO_LEN} chars: {game_id!r}")
    y, m, d = int(game_id[0:4]), int(game_id[4:6]), int(game_id[6:8])
    return GameId(date=date(y, m, d), away=game_id[8:10], home=game_id[10:12], seq=game_id[12])


def kbo_to_naver(kbo_game_id: str) -> str:
    """Append the season year to a KBO game_id to get the Naver gameId.

    Args:
        kbo_game_id: KBO G_ID string.

    Returns:
        The Naver gameId with the season year appended.

    Raises:
        ValueError: If kbo_game_id is not a valid KBO game_id.
    """
    g = parse_kbo_game_id(kbo_game_id)
    return f"{kbo_game_id}{g.date.year}"


def naver_to_kbo(naver_game_id: str) -> str:
    """Strip the trailing 4-digit season year from a Naver gameId.

    Args:
        naver_game_id: Naver gameId string (e.g. "20250514WOLG02025").

    Returns:
        The KBO G_ID string.

    Raises:
        ValueError: If naver_game_id is not 17 characters.
    """
    if len(naver_game_id) != _KBO_LEN + 4:
        raise ValueError(f"Naver gameId must be {_KBO_LEN + 4} chars: {naver_game_id!r}")
    return naver_game_id[:_KBO_LEN]
