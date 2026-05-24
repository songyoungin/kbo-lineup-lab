from fastapi import FastAPI

from app import models as _models  # noqa: F401 — registers all ORM models with Base.metadata

app = FastAPI(title="KBO Lineup Lab API")


@app.get("/health")
def health() -> dict[str, str]:
    """Return service health status."""
    return {"status": "ok"}
