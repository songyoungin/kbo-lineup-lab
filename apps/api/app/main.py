from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import models as _models  # noqa: F401 — registers all ORM models with Base.metadata
from app.api.routes import admin, games, jobs, team

app = FastAPI(title="KBO Lineup Lab API")

# Allow the local web dev server (and its IPv4/IPv6 variants) to call the API
# from the browser. Client components fetch the API cross-origin (web :3000 ->
# api :8000), which the browser blocks without these headers.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict[str, str]:
    """Return service health status."""
    return {"status": "ok"}


api_v1 = APIRouter(prefix="/api")
api_v1.include_router(team.router, prefix="/team", tags=["team"])
api_v1.include_router(games.router, prefix="/games", tags=["games"])
api_v1.include_router(jobs.router, prefix="/jobs", tags=["jobs"])
api_v1.include_router(admin.router, prefix="/admin", tags=["admin"])
app.include_router(api_v1)
