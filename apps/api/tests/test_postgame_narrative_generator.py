"""Tests for generate_narrative: LLM path + graceful fallback to skeleton."""

from __future__ import annotations

from app.postgame.narrative.generator import generate_narrative
from app.postgame.narrative.skeleton import build_skeleton
from app.postgame.narrative.types import NarrativeFacts


def _facts() -> NarrativeFacts:
    return NarrativeFacts(
        gap_label="acceptable",
        pregame_actual_score=3.9,
        pregame_recommended_score=4.1,
        overperformers=(),
        underperformers=(),
        difference_reviews=(),
    )


class _OkProvider:
    def complete(self, *, system: str, user: str, schema: dict[str, object]) -> dict[str, object]:
        return {"narrative": "  멋진 이야기.  "}


class _RaisingProvider:
    def complete(self, *, system: str, user: str, schema: dict[str, object]) -> dict[str, object]:
        raise RuntimeError("boom")


class _EmptyProvider:
    def complete(self, *, system: str, user: str, schema: dict[str, object]) -> dict[str, object]:
        return {"narrative": "   "}


class _NonStringProvider:
    def complete(self, *, system: str, user: str, schema: dict[str, object]) -> dict[str, object]:
        return {"narrative": 42}


def test_none_provider_uses_skeleton() -> None:
    facts = _facts()
    text, source = generate_narrative(facts, None)
    assert source == "skeleton"
    assert text == build_skeleton(facts)


def test_ok_provider_returns_stripped_llm_text() -> None:
    text, source = generate_narrative(_facts(), _OkProvider())
    assert source == "llm"
    assert text == "멋진 이야기."


def test_raising_provider_falls_back() -> None:
    facts = _facts()
    text, source = generate_narrative(facts, _RaisingProvider())
    assert source == "skeleton"
    assert text == build_skeleton(facts)


def test_empty_string_falls_back() -> None:
    _, source = generate_narrative(_facts(), _EmptyProvider())
    assert source == "skeleton"


def test_non_string_falls_back() -> None:
    _, source = generate_narrative(_facts(), _NonStringProvider())
    assert source == "skeleton"
