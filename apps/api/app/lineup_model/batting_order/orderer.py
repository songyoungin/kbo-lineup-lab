"""Batting-order orchestration: LLM call -> validate -> retry -> deterministic fallback."""

from __future__ import annotations

import logging

from app.lineup_model.batting_order.prompt import SYSTEM_PROMPT, build_user_prompt
from app.lineup_model.batting_order.schema import ORDER_JSON_SCHEMA, parse_and_validate
from app.lineup_model.batting_order.types import BattingOrderProvider, BattingOrderResult
from app.lineup_model.recommendation import _assign_batting_order
from app.lineup_model.types import Handedness, HitterStats, Position

logger = logging.getLogger(__name__)

_MAX_ATTEMPTS = 2


def _fallback(
    assigned: dict[Position, HitterStats],
    opp_handedness: Handedness,
) -> BattingOrderResult:
    """Build a result from the deterministic rule-based batting order."""
    slots = _assign_batting_order(assigned, opp_handedness)
    ordered = tuple(sorted(slots, key=lambda s: s.batting_order))
    rationale = {s.player_id: f"규칙 기반 배정: {s.batting_order}번" for s in ordered}
    summary = "LLM을 사용할 수 없어 규칙 기반 타순으로 구성했습니다."
    return BattingOrderResult(
        slots=ordered,
        rationale_ko_by_player=rationale,
        summary_ko=summary,
        source="fallback",
    )


def order(
    assigned: dict[Position, HitterStats],
    opp_handedness: Handedness,
    provider: BattingOrderProvider | None,
) -> BattingOrderResult:
    """Decide the batting order via the LLM, falling back deterministically.

    Args:
        assigned: Mapping of position to the assigned HitterStats (9 players).
        opp_handedness: Opposing starter's handedness.
        provider: Batting-order provider; None falls back immediately.

    Returns:
        A BattingOrderResult with slots, per-player rationale, summary, source.
    """
    if provider is None:
        return _fallback(assigned, opp_handedness)

    user_prompt = build_user_prompt(assigned, opp_handedness)
    for attempt in range(_MAX_ATTEMPTS):
        try:
            raw = provider.complete(
                system=SYSTEM_PROMPT, user=user_prompt, schema=ORDER_JSON_SCHEMA
            )
        except Exception as exc:  # noqa: BLE001 - any provider failure should fall back  # nosec B112
            logger.warning("LLM batting-order call failed (attempt %d): %s", attempt + 1, exc)
            continue

        validated = parse_and_validate(raw, assigned)
        if validated is not None:
            slots, rationale, summary = validated
            return BattingOrderResult(
                slots=slots,
                rationale_ko_by_player=rationale,
                summary_ko=summary,
                source="llm",
            )
        logger.warning("LLM batting-order output invalid (attempt %d)", attempt + 1)

    return _fallback(assigned, opp_handedness)
