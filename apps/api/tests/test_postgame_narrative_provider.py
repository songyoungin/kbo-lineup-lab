"""Tests for build_narrative_provider environment gating."""

from __future__ import annotations

from unittest.mock import patch

from app.postgame.narrative.provider import build_narrative_provider


@patch.dict("os.environ", {"POSTGAME_NARRATIVE_ENABLED": "false"}, clear=True)
def test_disabled_returns_none() -> None:
    assert build_narrative_provider() is None


@patch.dict("os.environ", {"POSTGAME_NARRATIVE_ENABLED": "true"}, clear=True)
def test_enabled_without_key_returns_none() -> None:
    assert build_narrative_provider() is None


@patch.dict(
    "os.environ",
    {"POSTGAME_NARRATIVE_ENABLED": "true", "OPENAI_API_KEY": "sk-test"},  # pragma: allowlist secret
    clear=True,
)
def test_enabled_with_key_returns_provider() -> None:
    provider = build_narrative_provider()
    assert provider is not None
    assert hasattr(provider, "complete")
