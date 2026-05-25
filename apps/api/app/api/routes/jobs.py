"""Routes for background job triggers."""

from fastapi import APIRouter

from app.api.deps import SessionDep
from app.schemas.postgame import GeneratePostgameReviewRequest, GeneratePostgameReviewResponse
from app.schemas.pregame import ReplayEvaluationRequest, ReplayEvaluationResponse
from app.services.postgame_reviews import generate_postgame_review_for_request
from app.services.pregame_views import replay_evaluation

router = APIRouter()


@router.post("/replay-evaluation", response_model=ReplayEvaluationResponse)
def replay_evaluation_endpoint(
    req: ReplayEvaluationRequest,
    session: SessionDep,
) -> ReplayEvaluationResponse:
    """Trigger a lineup evaluation run (idempotent — returns existing run on repeated calls)."""
    return replay_evaluation(session, request=req)


@router.post("/generate-postgame-review", response_model=GeneratePostgameReviewResponse)
def generate_postgame_review_endpoint(
    req: GeneratePostgameReviewRequest,
    session: SessionDep,
) -> GeneratePostgameReviewResponse:
    """Trigger a postgame review run (idempotent — returns existing run on repeated calls)."""
    return generate_postgame_review_for_request(session, request=req)
