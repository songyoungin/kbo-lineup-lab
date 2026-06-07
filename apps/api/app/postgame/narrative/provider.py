"""Environment-based factory for the postgame narrative LLM provider.

Reuses the generic OpenAI wrapper from the batting-order layer (its `complete`
is schema-agnostic) so there is a single OpenAI client implementation.
"""

from __future__ import annotations

import os

from app.lineup_model.batting_order.provider import OpenAIProvider
from app.postgame.narrative.types import NarrativeProvider

_DEFAULT_MODEL = "gpt-5.5"
_DEFAULT_TIMEOUT_S = 60.0


def build_narrative_provider() -> NarrativeProvider | None:
    """Build a narrative provider from env (None when disabled or no API key)."""
    if os.environ.get("POSTGAME_NARRATIVE_ENABLED", "false").lower() != "true":
        return None
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return None
    model = os.environ.get("POSTGAME_NARRATIVE_MODEL", _DEFAULT_MODEL)
    timeout_s = float(os.environ.get("POSTGAME_NARRATIVE_TIMEOUT_S", str(_DEFAULT_TIMEOUT_S)))
    return OpenAIProvider(api_key=api_key, model=model, timeout_s=timeout_s)
