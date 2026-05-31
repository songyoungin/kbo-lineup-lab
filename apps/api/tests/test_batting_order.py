"""Unit tests for the LLM batting-order layer."""

from __future__ import annotations

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
