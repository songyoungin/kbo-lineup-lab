import os

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import models as _models  # noqa: F401 — registers all ORM models with Base.metadata
from app.api.routes import admin, games, jobs, team

# Local web dev server origins (IPv4/IPv6 variants). Client components fetch
# the API cross-origin, which the browser blocks without CORS headers.
_DEFAULT_CORS_ORIGINS = "http://localhost:3000,http://127.0.0.1:3000"


def cors_origins() -> list[str]:
    """Return browser origins allowed by CORS.

    Reads KBO_CORS_ORIGINS (comma-separated) or falls back to the local dev defaults.
    """
    raw = os.environ.get("KBO_CORS_ORIGINS") or _DEFAULT_CORS_ORIGINS
    return [origin.strip() for origin in raw.split(",") if origin.strip()]


app = FastAPI(title="KBO Lineup Lab API")

# Origins are read once at startup; changing KBO_CORS_ORIGINS requires a restart.
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins(),
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
