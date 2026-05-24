from fastapi import FastAPI

app = FastAPI(title="KBO Lineup Lab API")


@app.get("/health")
def health() -> dict[str, str]:
    """Return service health status."""
    return {"status": "ok"}
