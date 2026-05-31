"""OpenAI batting-order provider and an environment-based factory."""

from __future__ import annotations

import json
import os

from openai import OpenAI

from app.lineup_model.batting_order.types import BattingOrderProvider

_DEFAULT_MODEL = "gpt-4.1"
_DEFAULT_TIMEOUT_S = 20.0


class OpenAIProvider:
    """Calls OpenAI Chat Completions with a forced structured-output schema."""

    def __init__(self, api_key: str, model: str, timeout_s: float) -> None:
        self._client = OpenAI(api_key=api_key, timeout=timeout_s)
        self._model = model

    def complete(self, *, system: str, user: str, schema: dict[str, object]) -> dict[str, object]:
        """Call the model with the schema enforced and parse the JSON response.

        Args:
            system: Static system prompt.
            user: Dynamic user prompt.
            schema: json_schema definition for response_format.

        Returns:
            The parsed JSON object.

        Raises:
            ValueError: If the response is not a JSON object.
        """
        # Reasoning models (gpt-5 family, o-series) reject a non-default
        # temperature; only models that accept it get temperature=0 pinned.
        if self._model.startswith(("gpt-5", "o1", "o3", "o4")):
            response = self._client.chat.completions.create(
                model=self._model,
                seed=0,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                response_format={"type": "json_schema", "json_schema": schema},
            )
        else:
            response = self._client.chat.completions.create(
                model=self._model,
                temperature=0,
                seed=0,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                response_format={"type": "json_schema", "json_schema": schema},
            )
        content = response.choices[0].message.content or "{}"
        parsed = json.loads(content)
        if not isinstance(parsed, dict):
            raise ValueError("LLM returned non-object JSON")
        return parsed


def build_provider() -> BattingOrderProvider | None:
    """Build a provider from environment variables (None if disabled/no key).

    Returns:
        An OpenAIProvider, or None when the feature is disabled or no API key
        is configured.
    """
    if os.environ.get("LINEUP_LLM_ENABLED", "false").lower() != "true":
        return None
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return None
    model = os.environ.get("LINEUP_LLM_MODEL", _DEFAULT_MODEL)
    timeout_s = float(os.environ.get("LINEUP_LLM_TIMEOUT_S", str(_DEFAULT_TIMEOUT_S)))
    return OpenAIProvider(api_key=api_key, model=model, timeout_s=timeout_s)
