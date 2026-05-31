"""OpenAI structured-output schema and output validation for batting order."""

from __future__ import annotations

from collections.abc import Mapping

from app.lineup_model.types import HitterStats, LineupSlot, Position

# Passed to OpenAI Chat Completions as response_format json_schema.
ORDER_JSON_SCHEMA: dict[str, object] = {
    "name": "batting_order",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "batting_order": {
                "type": "array",
                "minItems": 9,
                "maxItems": 9,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "batting_order": {"type": "integer", "minimum": 1, "maximum": 9},
                        "player_id": {"type": "integer"},
                        "rationale_ko": {"type": "string"},
                    },
                    "required": ["batting_order", "player_id", "rationale_ko"],
                },
            },
            "lineup_summary_ko": {"type": "string"},
        },
        "required": ["batting_order", "lineup_summary_ko"],
    },
}


def parse_and_validate(
    raw: Mapping[str, object],
    assigned: dict[Position, HitterStats],
) -> tuple[tuple[LineupSlot, ...], dict[int, str], str] | None:
    """Validate raw LLM output against the assigned set of players.

    Returns (slots ordered by batting_order, player_id -> rationale, summary)
    only when the output references exactly the assigned players, the
    batting_order values form a 1..N permutation, and all Korean text fields
    are non-empty. Otherwise returns None.

    Args:
        raw: JSON object returned by the LLM.
        assigned: Mapping of position to the assigned HitterStats.

    Returns:
        The validated (slots, rationale_by_player, summary) tuple, or None.
    """
    position_by_player = {stats.player_id: pos for pos, stats in assigned.items()}
    assigned_ids = set(position_by_player)
    expected_count = len(assigned_ids)

    entries = raw.get("batting_order")
    summary = raw.get("lineup_summary_ko")
    if not isinstance(entries, list) or len(entries) != expected_count:
        return None
    if not isinstance(summary, str) or not summary.strip():
        return None

    slots: list[LineupSlot] = []
    rationale: dict[int, str] = {}
    seen_orders: set[int] = set()
    seen_players: set[int] = set()

    for entry in entries:
        if not isinstance(entry, dict):
            return None
        order = entry.get("batting_order")
        pid = entry.get("player_id")
        note = entry.get("rationale_ko")
        if not isinstance(order, int) or not isinstance(pid, int):
            return None
        if not isinstance(note, str) or not note.strip():
            return None
        if order < 1 or order > expected_count or order in seen_orders:
            return None
        if pid not in assigned_ids or pid in seen_players:
            return None
        seen_orders.add(order)
        seen_players.add(pid)
        slots.append(
            LineupSlot(batting_order=order, player_id=pid, position=position_by_player[pid])
        )
        rationale[pid] = note.strip()

    if seen_players != assigned_ids:
        return None

    ordered = tuple(sorted(slots, key=lambda s: s.batting_order))
    return ordered, rationale, summary.strip()
