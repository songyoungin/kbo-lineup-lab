"""Tests for env-driven app configuration (CORS origins)."""

import pytest
from fastapi.testclient import TestClient

from app.main import app, cors_origins

client = TestClient(app)


def test_cors_origins_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without KBO_CORS_ORIGINS, the local dev origins are returned."""
    monkeypatch.delenv("KBO_CORS_ORIGINS", raising=False)
    assert cors_origins() == [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ]


def test_cors_origins_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """KBO_CORS_ORIGINS is split on commas and whitespace-stripped."""
    monkeypatch.setenv("KBO_CORS_ORIGINS", "https://app.vercel.app, https://other.app ,")
    assert cors_origins() == ["https://app.vercel.app", "https://other.app"]


def test_cors_header_for_default_origin() -> None:
    """The middleware echoes an allowed origin (default localhost set)."""
    res = client.get("/health", headers={"Origin": "http://localhost:3000"})
    assert res.headers.get("access-control-allow-origin") == "http://localhost:3000"
