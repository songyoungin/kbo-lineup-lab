"""Routes for background job triggers."""

from fastapi import APIRouter

from app.api.deps import SessionDep
from app.schemas.pregame import ReplayEvaluationRequest, ReplayEvaluationResponse
from app.services.pregame_views import replay_evaluation

router = APIRouter()


@router.post("/replay-evaluation", response_model=ReplayEvaluationResponse)
def replay_evaluation_endpoint(
    req: ReplayEvaluationRequest,
    session: SessionDep,
) -> ReplayEvaluationResponse:
    """Trigger a lineup evaluation run (idempotent — returns existing run on repeated calls)."""
    return replay_evaluation(session, request=req)
