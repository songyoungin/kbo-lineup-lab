"""Routes for game-level pregame and postgame views."""

from fastapi import APIRouter, Query

from app.api.deps import SessionDep
from app.schemas.postgame import PostgameResponse
from app.schemas.pregame import (
    LineupComparisonResponse,
    LineupScoreRequest,
    LineupScoreResponse,
    PlayerComparisonResponse,
    PlayerScoreCardResponse,
    PregameResponse,
)
from app.services.postgame_reviews import build_postgame_view
from app.services.pregame_views import (
    build_lineup_comparison,
    build_player_comparison,
    build_player_score_card,
    build_pregame_view,
    score_custom_batting_order,
)

router = APIRouter()


@router.get("/{game_id}/pregame", response_model=PregameResponse)
def pregame(game_id: int, session: SessionDep) -> PregameResponse:
    """Return the pregame evaluation view for the LG lineup in a given game."""
    return build_pregame_view(session, game_id)


@router.get("/{game_id}/postgame", response_model=PostgameResponse)
def postgame(game_id: int, session: SessionDep, team_id: int | None = None) -> PostgameResponse:
    """Return the postgame review for the LG lineup in a given game."""
    return build_postgame_view(session, game_id, team_id=team_id)


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


@router.get(
    "/{game_id}/players/{player_id}/score-card",
    response_model=PlayerScoreCardResponse,
)
def player_score_card(game_id: int, player_id: int, session: SessionDep) -> PlayerScoreCardResponse:
    """Return the display score card (radar factors + OVR) for one player."""
    return build_player_score_card(session, game_id, player_id)


@router.post("/{game_id}/lineup-score", response_model=LineupScoreResponse)
def lineup_score(
    game_id: int,
    req: LineupScoreRequest,
    session: SessionDep,
) -> LineupScoreResponse:
    """Score a user-supplied batting order (simulator) via the deterministic model."""
    return score_custom_batting_order(session, game_id, req.player_ids)
