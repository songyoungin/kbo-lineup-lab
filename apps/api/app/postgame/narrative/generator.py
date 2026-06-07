"""Narrative orchestration: LLM call -> validate -> deterministic skeleton fallback."""

from __future__ import annotations

import logging

from app.postgame.narrative.prompt import SYSTEM_PROMPT, build_user_prompt
from app.postgame.narrative.schema import NARRATIVE_JSON_SCHEMA
from app.postgame.narrative.skeleton import build_skeleton
from app.postgame.narrative.types import NarrativeFacts, NarrativeProvider

logger = logging.getLogger(__name__)


def generate_narrative(
    facts: NarrativeFacts, provider: NarrativeProvider | None
) -> tuple[str, str]:
    """Return (narrative_text, source) where source is "llm" or "skeleton".

    Any provider failure, timeout, or malformed/empty result falls back to the
    deterministic Korean skeleton. This function never raises.
    """
    if provider is None:
        return build_skeleton(facts), "skeleton"

    try:
        raw = provider.complete(
            system=SYSTEM_PROMPT, user=build_user_prompt(facts), schema=NARRATIVE_JSON_SCHEMA
        )
    except Exception as exc:  # noqa: BLE001 - any provider failure should fall back
        logger.warning("LLM postgame-narrative call failed: %s", exc)
        return build_skeleton(facts), "skeleton"

    text = raw.get("narrative")
    if isinstance(text, str) and text.strip():
        return text.strip(), "llm"

    logger.warning("LLM postgame-narrative output invalid: %r", text)
    return build_skeleton(facts), "skeleton"
