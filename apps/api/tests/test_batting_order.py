"""Unit tests for the LLM batting-order layer."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from app.lineup_model.types import Handedness, HitterStats, LineupSlot, Position


def test_batting_order_result_is_frozen_and_typed() -> None:
    """BattingOrderResult holds slots, rationale, summary, source and is immutable."""
    from app.lineup_model.batting_order.types import BattingOrderResult

    result = BattingOrderResult(
        slots=(LineupSlot(batting_order=1, player_id=7, position=Position.C),),
        rationale_ko_by_player={7: "출루율이 높아 1번"},
        summary_ko="요약",
        source="llm",
    )
    assert result.source == "llm"
    assert result.rationale_ko_by_player[7] == "출루율이 높아 1번"


def _assigned_three() -> dict[Position, HitterStats]:
    """Build a 3-position assignment for exercising validation logic."""
    return {
        Position.C: HitterStats(
            player_id=1,
            handedness=Handedness.RIGHT,
            primary_position=Position.C,
            ops=0.800,
            obp=0.350,
            slg=0.450,
        ),
        Position.FIRST: HitterStats(
            player_id=2,
            handedness=Handedness.LEFT,
            primary_position=Position.FIRST,
            ops=0.800,
            obp=0.350,
            slg=0.450,
        ),
        Position.DH: HitterStats(
            player_id=3,
            handedness=Handedness.RIGHT,
            primary_position=Position.DH,
            ops=0.800,
            obp=0.350,
            slg=0.450,
        ),
    }


def test_parse_and_validate_accepts_valid_permutation() -> None:
    """Valid output matching the assigned players parses into slots/rationale/summary."""
    from app.lineup_model.batting_order.schema import parse_and_validate

    assigned = _assigned_three()
    raw = {
        "batting_order": [
            {"batting_order": 2, "player_id": 1, "rationale_ko": "근거1"},
            {"batting_order": 1, "player_id": 2, "rationale_ko": "근거2"},
            {"batting_order": 3, "player_id": 3, "rationale_ko": "근거3"},
        ],
        "lineup_summary_ko": "요약",
    }
    out = parse_and_validate(raw, assigned)
    assert out is not None
    slots, rationale, summary = out
    assert [s.batting_order for s in slots] == [1, 2, 3]
    assert slots[0].player_id == 2 and slots[0].position == Position.FIRST
    assert rationale[1] == "근거1"
    assert summary == "요약"


def test_parse_and_validate_rejects_unknown_player() -> None:
    """A player_id not in the assignment is rejected (returns None)."""
    from app.lineup_model.batting_order.schema import parse_and_validate

    assigned = _assigned_three()
    raw = {
        "batting_order": [
            {"batting_order": 1, "player_id": 99, "rationale_ko": "x"},
            {"batting_order": 2, "player_id": 2, "rationale_ko": "y"},
            {"batting_order": 3, "player_id": 3, "rationale_ko": "z"},
        ],
        "lineup_summary_ko": "요약",
    }
    assert parse_and_validate(raw, assigned) is None


def test_parse_and_validate_rejects_duplicate_order() -> None:
    """batting_order values that are not a 1..N permutation are rejected."""
    from app.lineup_model.batting_order.schema import parse_and_validate

    assigned = _assigned_three()
    raw = {
        "batting_order": [
            {"batting_order": 1, "player_id": 1, "rationale_ko": "x"},
            {"batting_order": 1, "player_id": 2, "rationale_ko": "y"},
            {"batting_order": 3, "player_id": 3, "rationale_ko": "z"},
        ],
        "lineup_summary_ko": "요약",
    }
    assert parse_and_validate(raw, assigned) is None


def test_system_prompt_states_team_run_objective() -> None:
    """System prompt encodes the team-run-production objective and Korean-output rule."""
    from app.lineup_model.batting_order.prompt import SYSTEM_PROMPT

    assert "득점" in SYSTEM_PROMPT
    assert "한국어" in SYSTEM_PROMPT


def test_build_user_prompt_lists_all_assigned_players() -> None:
    """User prompt includes every assigned player_id and the opponent handedness."""
    from app.lineup_model.batting_order.prompt import build_user_prompt

    assigned = _assigned_three()
    text = build_user_prompt(assigned, Handedness.LEFT)
    for pid in (1, 2, 3):
        assert f"player_id={pid}" in text
    assert "L" in text


def test_build_provider_returns_none_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """build_provider returns None when LINEUP_LLM_ENABLED is unset/false."""
    from app.lineup_model.batting_order.provider import build_provider

    monkeypatch.delenv("LINEUP_LLM_ENABLED", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    assert build_provider() is None


def test_build_provider_returns_none_without_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """build_provider returns None when enabled but no API key is present."""
    from app.lineup_model.batting_order.provider import build_provider

    monkeypatch.setenv("LINEUP_LLM_ENABLED", "true")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert build_provider() is None


@patch("app.lineup_model.batting_order.provider.OpenAI")
def test_openai_provider_parses_json_content(mock_openai_cls: MagicMock) -> None:
    """OpenAIProvider.complete parses the response content (JSON string) into a dict."""
    from app.lineup_model.batting_order.provider import OpenAIProvider

    mock_client = MagicMock()
    mock_openai_cls.return_value = mock_client
    message = MagicMock()
    message.content = json.dumps({"lineup_summary_ko": "ok", "batting_order": []})
    mock_client.chat.completions.create.return_value.choices = [MagicMock(message=message)]

    provider = OpenAIProvider(api_key="sk-test", model="gpt-4.1", timeout_s=5.0)
    out = provider.complete(system="sys", user="usr", schema={"name": "x"})
    assert out == {"lineup_summary_ko": "ok", "batting_order": []}
    mock_client.chat.completions.create.assert_called_once()
