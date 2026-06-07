"""Forced JSON schema for the postgame narrative LLM response."""

from __future__ import annotations

# Passed to OpenAI Chat Completions as the inner response_format json_schema
# (OpenAIProvider.complete adds the {"type": "json_schema", "json_schema": ...} wrapper).
NARRATIVE_JSON_SCHEMA: dict[str, object] = {
    "name": "postgame_narrative",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {"narrative": {"type": "string"}},
        "required": ["narrative"],
        "additionalProperties": False,
    },
}
