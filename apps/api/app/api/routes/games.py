"""Routes for game-level pregame views."""

from fastapi import APIRouter, Query

from app.api.deps import SessionDep
from app.schemas.pregame import LineupComparisonResponse, PlayerComparisonResponse, PregameResponse
from app.services.pregame_views import (
    build_lineup_comparison,
    build_player_comparison,
    build_pregame_view,
)

router = APIRouter()


@router.get("/{game_id}/pregame", response_model=PregameResponse)
def pregame(game_id: int, session: SessionDep) -> PregameResponse:
    """Return the pregame evaluation view for the LG lineup in a given game."""
    return build_pregame_view(session, game_id)


@router.get("/{game_id}/lineup-comparison", response_model=LineupComparisonResponse)
def lineup_comparison(game_id: int, session: SessionDep) -> LineupComparisonResponse:
    """Return the per-slot actual vs recommended lineup comparison."""
    return build_lineup_comparison(session, game_id)


@router.get("/{game_id}/players/compare", response_model=PlayerComparisonResponse)
def compare_players(
    game_id: int,
    session: SessionDep,
    batting_order: int = Query(..., ge=1, le=9),
) -> PlayerComparisonResponse:
    """Return the head-to-head player comparison for a specific batting order slot."""
    return build_player_comparison(session, game_id, batting_order)
