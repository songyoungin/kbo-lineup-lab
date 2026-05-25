"""Payload category enumeration for raw ingestion payloads."""

from enum import StrEnum

__all__ = ["PayloadCategory"]


class PayloadCategory(StrEnum):
    """Five KBO data payload categories that collectors may fetch."""

    SCHEDULE = "schedule"
    ROSTER = "roster"
    PLAYER_STATS = "player_stats"
    LINEUP = "lineup"
    BOX_SCORE = "box_score"
