from fastapi import APIRouter, FastAPI

from app import models as _models  # noqa: F401 — registers all ORM models with Base.metadata
from app.api.routes import games, jobs, team

app = FastAPI(title="KBO Lineup Lab API")


@app.get("/health")
def health() -> dict[str, str]:
    """Return service health status."""
    return {"status": "ok"}


api_v1 = APIRouter(prefix="/api")
api_v1.include_router(team.router, prefix="/team", tags=["team"])
api_v1.include_router(games.router, prefix="/games", tags=["games"])
api_v1.include_router(jobs.router, prefix="/jobs", tags=["jobs"])
app.include_router(api_v1)
