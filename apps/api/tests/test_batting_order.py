"""Unit tests for the LLM batting-order layer."""

from __future__ import annotations

from app.lineup_model.types import LineupSlot, Position


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
