"""Input/output types and the provider interface for the batting-order layer."""

from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel, ConfigDict

from app.lineup_model.types import LineupSlot


class BattingOrderResult(BaseModel):
    """Batting order produced by the LLM (or the deterministic fallback)."""

    model_config = ConfigDict(frozen=True)

    slots: tuple[LineupSlot, ...]
    rationale_ko_by_player: dict[int, str]
    summary_ko: str
    source: str  # "llm" or "fallback"


class BattingOrderProvider(Protocol):
    """Abstraction over a batting-order LLM call."""

    def complete(self, *, system: str, user: str, schema: dict[str, object]) -> dict[str, object]:
        """Return a parsed JSON object conforming to the given schema."""
        ...
