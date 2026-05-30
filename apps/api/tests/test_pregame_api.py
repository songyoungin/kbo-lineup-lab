"""Integration tests for the pregame evaluation API endpoints.

All tests use an in-memory SQLite database seeded with the LG fixture file.
A single pytest fixture provides a configured TestClient with the session
dependency overridden so no real database is touched.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401 — registers all models with Base.metadata
from app.api.deps import get_session
from app.db.base import Base
from app.main import app as fastapi_app
from app.models.evaluation import ModelVersion
from app.services.fixture_loader import load_fixture_file

FIXTURE_PATH = Path(__file__).parent.parent / "fixtures" / "lg_2026_sample.json"

# Cutoff safely after the fixture's lineup snapshot (announced_at=2026-04-15T17:30+09:00 = 08:30Z)
CUTOFF = datetime(2026, 4, 15, 9, 0, 0, tzinfo=UTC)

# ---------------------------------------------------------------------------
# Module-level shared state (set up once, reused by all tests in this module)
# ---------------------------------------------------------------------------

_shared_engine: Engine | None = None
_shared_session_factory: sessionmaker[Session] | None = None
_shared_model_version_id: int | None = None
_shared_game_id: int | None = None
_shared_team_id: int | None = None


def _get_shared_state() -> tuple[Engine, sessionmaker[Session], int, int, int]:
    """Initialise the shared in-memory database (idempotent)."""
    global _shared_engine, _shared_session_factory  # noqa: PLW0603
    global _shared_model_version_id, _shared_game_id, _shared_team_id  # noqa: PLW0603

    if _shared_engine is None:
        from sqlalchemy import select

        from app.models.game import Game
        from app.models.team import Team

        engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(engine)
        factory: sessionmaker[Session] = sessionmaker(bind=engine)

        # Seed ModelVersion
        with factory() as s:
            mv = ModelVersion(name="test-model", version="v1", model_id="anthropic/claude-test")
            s.add(mv)
            s.commit()
            mv_id = int(mv.id)

        # Load fixture
        with factory() as s:
            load_fixture_file(FIXTURE_PATH, s)

        # Read back IDs
        with factory() as s:
            game = s.execute(select(Game)).scalars().first()
            assert game is not None
            g_id = int(game.id)

            team = s.execute(select(Team).where(Team.code == "LG")).scalars().first()
            assert team is not None
            t_id = int(team.id)

        _shared_engine = engine
        _shared_session_factory = factory
        _shared_model_version_id = mv_id
        _shared_game_id = g_id
        _shared_team_id = t_id

    assert _shared_session_factory is not None
    assert _shared_model_version_id is not None
    assert _shared_game_id is not None
    assert _shared_team_id is not None
    return (
        _shared_engine,
        _shared_session_factory,
        _shared_model_version_id,
        _shared_game_id,
        _shared_team_id,
    )


# ---------------------------------------------------------------------------
# pytest fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def client() -> Iterator[TestClient]:
    """TestClient with session dependency overridden to use the in-memory DB."""
    _, factory, _, _, _ = _get_shared_state()

    def override_get_session() -> Iterator[Session]:
        with factory() as s:
            yield s

    fastapi_app.dependency_overrides[get_session] = override_get_session
    try:
        yield TestClient(fastapi_app)
    finally:
        fastapi_app.dependency_overrides.clear()


@pytest.fixture
def _game_id() -> int:
    _, _, _, g_id, _ = _get_shared_state()
    return g_id


@pytest.fixture
def _team_id() -> int:
    _, _, _, _, t_id = _get_shared_state()
    return t_id


@pytest.fixture
def _model_version_id() -> int:
    _, _, mv_id, _, _ = _get_shared_state()
    return mv_id


@pytest.fixture
def clean_env() -> Iterator[tuple[TestClient, int, int, int]]:
    """TestClient bootstrapped on a FRESH in-memory DB (no shared state).

    Yields (client, game_id, team_id, model_version_id) for a database that
    contains the LG fixture and a ModelVersion row but NO evaluation runs.
    This properly exercises the "no completed run" branch in build_pregame_view.
    """
    from sqlalchemy import select

    from app.models.game import Game
    from app.models.team import Team

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory: sessionmaker[Session] = sessionmaker(bind=engine)

    with factory() as s:
        mv = ModelVersion(name="test-model-clean", version="v1", model_id="anthropic/claude-test")
        s.add(mv)
        s.commit()
        mv_id = int(mv.id)

    with factory() as s:
        load_fixture_file(FIXTURE_PATH, s)

    with factory() as s:
        game = s.execute(select(Game)).scalars().first()
        assert game is not None
        g_id = int(game.id)
        team = s.execute(select(Team).where(Team.code == "LG")).scalars().first()
        assert team is not None
        t_id = int(team.id)

    def override_get_session() -> Iterator[Session]:
        with factory() as s:
            yield s

    fastapi_app.dependency_overrides[get_session] = override_get_session
    try:
        yield TestClient(fastapi_app), g_id, t_id, mv_id
    finally:
        fastapi_app.dependency_overrides.clear()
        engine.dispose()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _replay_body(game_id: int, team_id: int, model_version_id: int) -> dict[str, object]:
    return {
        "game_id": game_id,
        "team_id": team_id,
        "evaluation_cutoff_at": CUTOFF.isoformat(),
        "model_version_id": model_version_id,
    }


def _make_session_with_fixture() -> tuple[sessionmaker[Session], int, int, int]:
    """Create an isolated in-memory DB with the LG fixture and a ModelVersion.

    Returns (session_factory, game_id, team_id, model_version_id). Unlike the
    module-level shared state, each call is a fresh engine, so tests can insert
    box-score and postgame rows without leaking into other tests.
    """
    from sqlalchemy import select

    from app.models.game import Game
    from app.models.team import Team

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory: sessionmaker[Session] = sessionmaker(bind=engine)

    with factory() as s:
        mv = ModelVersion(name="test-model-status", version="v1", model_id="anthropic/claude-test")
        s.add(mv)
        s.commit()
        mv_id = int(mv.id)

    with factory() as s:
        load_fixture_file(FIXTURE_PATH, s)

    with factory() as s:
        game = s.execute(select(Game)).scalars().first()
        assert game is not None
        g_id = int(game.id)
        team = s.execute(select(Team).where(Team.code == "LG")).scalars().first()
        assert team is not None
        t_id = int(team.id)

    return factory, g_id, t_id, mv_id


# ---------------------------------------------------------------------------
# /health — regression: existing endpoint must still work
# ---------------------------------------------------------------------------


def test_health_still_works(client: TestClient) -> None:
    """Existing /health endpoint must return 200 after router integration."""
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


# ---------------------------------------------------------------------------
# GET /api/team/lg/home
# ---------------------------------------------------------------------------


def test_team_home_returns_200_with_today_game(client: TestClient, _game_id: int) -> None:
    """GET /api/team/lg/home returns 200 with the fixture game in today."""
    resp = client.get("/api/team/lg/home")
    assert resp.status_code == 200
    body = resp.json()
    assert body["team_code"] == "LG"
    today = body["today"]
    assert today is not None
    assert today["game_id"] == _game_id
    assert today["game_date"] == "2026-04-15"
    assert today["opponent_team_code"] == "DOO"
    assert today["venue"] == "Jamsil Baseball Stadium"
    assert isinstance(today["pipeline_status"], dict)
    assert "schedule" in today["pipeline_status"]


def test_team_home_recent_is_empty_list(client: TestClient) -> None:
    """recent is an empty list for MVP with no historical records."""
    resp = client.get("/api/team/lg/home")
    assert resp.status_code == 200
    assert resp.json()["recent"] == []


def test_team_home_box_status_reflects_snapshot() -> None:
    """pipeline_status['box'] is 'missing' with no box score, 'ok' once one exists."""
    from sqlalchemy import delete

    from app.models.snapshot import BoxScoreSnapshot, IngestionRun
    from app.services.pregame_views import build_team_home

    factory, g_id, _t_id, _mv_id = _make_session_with_fixture()

    # The fixture seeds a box score; remove it so we can test the 'missing' branch.
    with factory() as s:
        s.execute(delete(BoxScoreSnapshot).where(BoxScoreSnapshot.game_id == g_id))
        s.commit()

    with factory() as s:
        before = build_team_home(s, "LG")
        assert before.today is not None
        assert before.today.pipeline_status["box"] == "missing"

    with factory() as s:
        ingestion = IngestionRun(source="test-box", status="completed")
        s.add(ingestion)
        s.commit()
        box = BoxScoreSnapshot(
            game_id=g_id,
            ingestion_run_id=ingestion.id,
            taken_at=datetime(2026, 4, 15, 13, 0, tzinfo=UTC),
            content_hash="boxhash-1",
        )
        s.add(box)
        s.commit()

    with factory() as s:
        after = build_team_home(s, "LG")
        assert after.today is not None
        assert after.today.pipeline_status["box"] == "ok"


# ---------------------------------------------------------------------------
# GET /api/games/{id}/pregame — 404 before evaluation run exists
# ---------------------------------------------------------------------------


def test_pregame_returns_404_before_evaluation_run(
    clean_env: tuple[TestClient, int, int, int],
) -> None:
    """GET /api/games/{real_id}/pregame returns 404 when no run exists for that game.

    Uses a FRESH in-memory DB (clean_env) that contains the LG fixture but no
    evaluation runs, so the request hits build_pregame_view's "no completed
    evaluation run" branch — not the "Game not found" branch.
    """
    client, real_game_id, _, _ = clean_env
    resp = client.get(f"/api/games/{real_game_id}/pregame")
    assert resp.status_code == 404
    # Confirm we hit the no-run branch, not the unknown-game branch
    detail = resp.json()["detail"]
    assert "No completed evaluation run" in detail
    assert "not found" not in detail.lower() or "evaluation run" in detail.lower()


# ---------------------------------------------------------------------------
# POST /api/jobs/replay-evaluation
# ---------------------------------------------------------------------------


def test_replay_evaluation_creates_run(
    client: TestClient, _game_id: int, _team_id: int, _model_version_id: int
) -> None:
    """First POST /api/jobs/replay-evaluation creates a run (created=True)."""
    body = _replay_body(_game_id, _team_id, _model_version_id)
    resp = client.post("/api/jobs/replay-evaluation", json=body)
    assert resp.status_code == 200
    data = resp.json()
    assert data["created"] is True
    assert data["status"] == "completed"
    assert isinstance(data["evaluation_run_id"], int)


def test_replay_evaluation_idempotent(
    client: TestClient, _game_id: int, _team_id: int, _model_version_id: int
) -> None:
    """Second POST with identical body returns the same run id (created=False)."""
    body = _replay_body(_game_id, _team_id, _model_version_id)
    resp1 = client.post("/api/jobs/replay-evaluation", json=body)
    resp2 = client.post("/api/jobs/replay-evaluation", json=body)
    assert resp1.status_code == 200
    assert resp2.status_code == 200
    d1 = resp1.json()
    d2 = resp2.json()
    assert d1["evaluation_run_id"] == d2["evaluation_run_id"]
    assert d2["created"] is False


# ---------------------------------------------------------------------------
# GET /api/games/{id}/pregame — after evaluation run exists
# ---------------------------------------------------------------------------


def test_pregame_returns_200_after_replay(
    client: TestClient, _game_id: int, _team_id: int, _model_version_id: int
) -> None:
    """GET /api/games/{id}/pregame returns 200 with scores and 9-row tables."""
    # Ensure run exists
    body = _replay_body(_game_id, _team_id, _model_version_id)
    client.post("/api/jobs/replay-evaluation", json=body)

    resp = client.get(f"/api/games/{_game_id}/pregame")
    assert resp.status_code == 200
    data = resp.json()

    assert data["game_id"] == _game_id
    assert isinstance(data["actual_score"], float)
    assert isinstance(data["recommended_score"], float)
    assert isinstance(data["score_gap"], float)
    assert data["verdict"] in (
        "Nearly optimal",
        "Acceptable",
        "Questionable",
        "Low offensive efficiency",
    )
    assert len(data["actual_lineup"]) == 9
    assert len(data["recommended_lineup"]) == 9
    assert isinstance(data["differences"], list)
    assert isinstance(data["model_limitations"], list)


def test_pregame_score_gap_consistency(
    client: TestClient, _game_id: int, _team_id: int, _model_version_id: int
) -> None:
    """score_gap == actual_score - recommended_score."""
    body = _replay_body(_game_id, _team_id, _model_version_id)
    client.post("/api/jobs/replay-evaluation", json=body)

    resp = client.get(f"/api/games/{_game_id}/pregame")
    assert resp.status_code == 200
    data = resp.json()
    assert abs(data["score_gap"] - (data["actual_score"] - data["recommended_score"])) < 1e-9


# ---------------------------------------------------------------------------
# GET /api/games/{id}/lineup-comparison
# ---------------------------------------------------------------------------


def test_lineup_comparison_returns_9_rows(
    client: TestClient, _game_id: int, _team_id: int, _model_version_id: int
) -> None:
    """GET /api/games/{id}/lineup-comparison returns 9 rows."""
    body = _replay_body(_game_id, _team_id, _model_version_id)
    client.post("/api/jobs/replay-evaluation", json=body)

    resp = client.get(f"/api/games/{_game_id}/lineup-comparison")
    assert resp.status_code == 200
    data = resp.json()
    assert data["game_id"] == _game_id
    rows = data["rows"]
    assert len(rows) == 9


def test_lineup_comparison_difference_types_are_valid(
    client: TestClient, _game_id: int, _team_id: int, _model_version_id: int
) -> None:
    """Each row's difference_type is from the expected enumerated set."""
    valid_types = {
        "Same",
        "Player changed",
        "Position changed",
        "Batting order changed",
        "Player and order changed",
    }
    body = _replay_body(_game_id, _team_id, _model_version_id)
    client.post("/api/jobs/replay-evaluation", json=body)

    resp = client.get(f"/api/games/{_game_id}/lineup-comparison")
    assert resp.status_code == 200
    for row in resp.json()["rows"]:
        assert row["difference_type"] in valid_types


# ---------------------------------------------------------------------------
# GET /api/games/{id}/players/compare
# ---------------------------------------------------------------------------


def test_player_comparison_returns_two_players(
    client: TestClient, _game_id: int, _team_id: int, _model_version_id: int
) -> None:
    """GET /api/games/{id}/players/compare?batting_order=1 returns both players."""
    body = _replay_body(_game_id, _team_id, _model_version_id)
    client.post("/api/jobs/replay-evaluation", json=body)

    resp = client.get(f"/api/games/{_game_id}/players/compare?batting_order=1")
    assert resp.status_code == 200
    data = resp.json()
    assert data["batting_order"] == 1
    assert "actual" in data
    assert "recommended" in data
    assert isinstance(data["actual"]["player_name"], str)
    assert isinstance(data["recommended"]["player_name"], str)
    assert isinstance(data["judgment"], str)
    assert isinstance(data["unmodeled_factors"], list)
    assert len(data["unmodeled_factors"]) > 0


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_batting_order_out_of_range_low_returns_422(client: TestClient, _game_id: int) -> None:
    """batting_order=0 (below minimum) → 422 Unprocessable Entity."""
    resp = client.get(f"/api/games/{_game_id}/players/compare?batting_order=0")
    assert resp.status_code == 422


def test_batting_order_out_of_range_high_returns_422(client: TestClient, _game_id: int) -> None:
    """batting_order=10 (above maximum) → 422 Unprocessable Entity."""
    resp = client.get(f"/api/games/{_game_id}/players/compare?batting_order=10")
    assert resp.status_code == 422


def test_unknown_game_id_pregame_returns_404(client: TestClient) -> None:
    """Unknown game_id returns 404 for /pregame."""
    resp = client.get("/api/games/999999/pregame")
    assert resp.status_code == 404


def test_unknown_game_id_lineup_comparison_returns_404(client: TestClient) -> None:
    """Unknown game_id returns 404 for /lineup-comparison."""
    resp = client.get("/api/games/999999/lineup-comparison")
    assert resp.status_code == 404


def test_naive_cutoff_at_returns_422(
    client: TestClient, _game_id: int, _team_id: int, _model_version_id: int
) -> None:
    """Naive evaluation_cutoff_at (no tz) is rejected by Pydantic → 422."""
    body = {
        "game_id": _game_id,
        "team_id": _team_id,
        # No timezone offset → naive datetime string
        "evaluation_cutoff_at": "2026-04-15T09:00:00",
        "model_version_id": _model_version_id,
    }
    resp = client.post("/api/jobs/replay-evaluation", json=body)
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Additional GET /api/games/{id}/players/compare coverage (I2)
# ---------------------------------------------------------------------------


def test_player_comparison_batting_order_9(
    client: TestClient, _game_id: int, _team_id: int, _model_version_id: int
) -> None:
    """batting_order=9 (weakest slot) returns full response with both players."""
    body = _replay_body(_game_id, _team_id, _model_version_id)
    client.post("/api/jobs/replay-evaluation", json=body)

    resp = client.get(f"/api/games/{_game_id}/players/compare?batting_order=9")
    assert resp.status_code == 200
    data = resp.json()
    assert data["batting_order"] == 9
    # Both player payloads must have the documented shape
    for key in ("actual", "recommended"):
        block = data[key]
        assert isinstance(block["player_id"], int)
        assert isinstance(block["player_name"], str)
        assert isinstance(block["position"], str)
        assert isinstance(block["ops"], float)
        assert isinstance(block["obp"], float)
        assert isinstance(block["slg"], float)
    assert isinstance(data["judgment"], str)
    assert isinstance(data["unmodeled_factors"], list)


def test_player_comparison_slot_with_different_players_exercises_judgment(
    client: TestClient, _game_id: int, _team_id: int, _model_version_id: int
) -> None:
    """Find a slot where actual != recommended and verify judgment + factors."""
    body = _replay_body(_game_id, _team_id, _model_version_id)
    client.post("/api/jobs/replay-evaluation", json=body)

    # Scan slots to find one where actual_player_id != recommended_player_id
    cmp_resp = client.get(f"/api/games/{_game_id}/lineup-comparison")
    assert cmp_resp.status_code == 200
    rows = cmp_resp.json()["rows"]
    target_order: int | None = None
    for row in rows:
        if row["actual_player_id"] != row["recommended_player_id"]:
            target_order = row["batting_order"]
            break
    assert target_order is not None, (
        "Fixture should produce at least one slot where actual != recommended"
    )

    resp = client.get(f"/api/games/{_game_id}/players/compare?batting_order={target_order}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["batting_order"] == target_order
    assert data["actual"]["player_id"] != data["recommended"]["player_id"]
    # judgment text must mention one of the two player names
    assert (
        data["actual"]["player_name"] in data["judgment"]
        or data["recommended"]["player_name"] in data["judgment"]
    )
    # Documented unmodeled factors must be non-empty list of strings
    assert all(isinstance(f, str) for f in data["unmodeled_factors"])
    assert len(data["unmodeled_factors"]) >= 4


# ---------------------------------------------------------------------------
# Critical: actual_score and recommended_score must be on the same scale
# ---------------------------------------------------------------------------


def test_actual_and_recommended_scores_on_same_scale(
    client: TestClient, _game_id: int, _team_id: int, _model_version_id: int
) -> None:
    """actual_score must be in the same numeric band as recommended_score.

    Both come from compute_lineup_score, which produces values in the
    OPS-rate-stat space (~0.6–1.0) plus a position_completeness bonus
    (0 or +0.05) and a handedness_balance_penalty (0, -1, or -2).

    If the actual lineup share the same handedness composition as the
    recommended (likely for this fixture since both use the same player
    pool), both scores should be within ~0.3 of each other — far closer
    than the previous bug where actual was a raw 0.7+ mean and recommended
    was -1.1 (mean + handedness penalty).
    """
    body = _replay_body(_game_id, _team_id, _model_version_id)
    client.post("/api/jobs/replay-evaluation", json=body)

    resp = client.get(f"/api/games/{_game_id}/pregame")
    assert resp.status_code == 200
    data = resp.json()

    actual = data["actual_score"]
    recommended = data["recommended_score"]

    # The gap must be small on this fixture (both lineups use LG players
    # with identical handedness composition by construction).
    assert abs(actual - recommended) < 0.3, (
        f"actual={actual} and recommended={recommended} should be on the same "
        "numeric scale (both produced by compute_lineup_score). A large gap "
        "indicates the scale-mismatch regression has returned."
    )
    # And the gap reported in the response must match
    assert abs(data["score_gap"] - (actual - recommended)) < 1e-9


def test_pregame_model_limitations_contains_actual_score_method_note(
    client: TestClient, _game_id: int, _team_id: int, _model_version_id: int
) -> None:
    """model_limitations must include the actual-score method note."""
    from app.services.pregame_views import ACTUAL_SCORE_METHOD_NOTE

    body = _replay_body(_game_id, _team_id, _model_version_id)
    client.post("/api/jobs/replay-evaluation", json=body)

    resp = client.get(f"/api/games/{_game_id}/pregame")
    assert resp.status_code == 200
    limitations = resp.json()["model_limitations"]
    assert ACTUAL_SCORE_METHOD_NOTE in limitations
