"""Routes for team-level views."""

from fastapi import APIRouter

from app.api.deps import SessionDep
from app.schemas.pregame import TeamHomeResponse
from app.services.pregame_views import build_team_home

router = APIRouter()


@router.get("/lg/home", response_model=TeamHomeResponse)
def lg_home(session: SessionDep) -> TeamHomeResponse:
    """Return the LG Twins team home page payload."""
    return build_team_home(session, "LG")
