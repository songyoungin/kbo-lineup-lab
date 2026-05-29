"""Verifies game_id parsing/formatting and KBO<->Naver conversion.
KBO G_ID = YYYYMMDD{away}{home}{seq}; Naver appends the season year."""

from __future__ import annotations

from datetime import date

import pytest

from app.ingestion.game_id import GameId, kbo_to_naver, naver_to_kbo, parse_kbo_game_id


def test_parse_kbo_game_id() -> None:
    g = parse_kbo_game_id("20250514WOLG0")
    assert g == GameId(date=date(2025, 5, 14), away="WO", home="LG", seq="0")


def test_parse_rejects_bad_length() -> None:
    with pytest.raises(ValueError):
        parse_kbo_game_id("2025")


def test_kbo_to_naver_appends_season() -> None:
    assert kbo_to_naver("20250514WOLG0") == "20250514WOLG02025"


def test_naver_to_kbo_strips_season() -> None:
    assert naver_to_kbo("20250514WOLG02025") == "20250514WOLG0"


def test_roundtrip() -> None:
    assert naver_to_kbo(kbo_to_naver("20250514WOLG0")) == "20250514WOLG0"
