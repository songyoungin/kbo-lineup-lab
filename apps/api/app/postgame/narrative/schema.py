"""Forced JSON schema for the postgame narrative LLM response."""

from __future__ import annotations

from typing import Any

NARRATIVE_JSON_SCHEMA: dict[str, Any] = {
    "type": "json_schema",
    "json_schema": {
        "name": "postgame_narrative",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {"narrative": {"type": "string"}},
            "required": ["narrative"],
            "additionalProperties": False,
        },
    },
}
