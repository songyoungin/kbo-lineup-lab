"""Re-export all ORM models so that a single import registers them with Base.metadata."""

from app.models.evaluation import (
    LineupEvaluationRun,
    LineupEvaluationSummary,
    ModelVersion,
    RecommendedLineupRow,
)
from app.models.game import Game
from app.models.player import Player
from app.models.postgame import PostgameReviewRun, PostgameReviewSummary
from app.models.snapshot import (
    ActualLineupSnapshot,
    ActualLineupSnapshotRow,
    BoxScoreRow,
    BoxScoreSnapshot,
    IngestionRun,
    PlayerStatSnapshotRow,
    StatSnapshot,
)
from app.models.team import Team

__all__ = [
    "ActualLineupSnapshot",
    "ActualLineupSnapshotRow",
    "BoxScoreRow",
    "BoxScoreSnapshot",
    "Game",
    "IngestionRun",
    "LineupEvaluationRun",
    "LineupEvaluationSummary",
    "ModelVersion",
    "Player",
    "PlayerStatSnapshotRow",
    "PostgameReviewRun",
    "PostgameReviewSummary",
    "RecommendedLineupRow",
    "StatSnapshot",
    "Team",
]
