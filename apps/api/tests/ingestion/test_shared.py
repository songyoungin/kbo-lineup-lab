"""Tests for shared normalizer helpers.

Covers:
- to_position maps Naver lineup numeric codes and box-score `pos` tokens to
  canonical Position values, defaulting to "DH" for unknown/substitution tokens.
"""

from __future__ import annotations

import pytest

from app.ingestion.normalizers._shared import to_position


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # Lineup numeric codes.
        ("8", "CF"),
        ("1", "P"),
        ("0", "DH"),
        # Box-score Korean / Sino-Korean tokens.
        ("중", "CF"),
        ("三", "3B"),
        ("지", "DH"),
        ("포", "C"),
        # Messy substitution / multi-position tokens fall back to DH.
        ("타우", "DH"),
        ("주", "DH"),
        # Missing / empty / whitespace handling.
        (None, "DH"),
        ("", "DH"),
        (" 8 ", "CF"),
    ],
)
def test_to_position(raw: object, expected: str) -> None:
    assert to_position(raw) == expected
