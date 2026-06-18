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
    """recent is empty when the team has only a single (today's) game."""
    resp = client.get("/api/team/lg/home")
    assert resp.status_code == 200
    assert resp.json()["recent"] == []


def test_team_home_recent_lists_past_games() -> None:
    """recent lists the team's earlier games (most-recent-first), excluding today.

    Seeds one older LG game beyond the fixture game and verifies build_team_home
    surfaces it under `recent` with the opponent code and a None verdict (no
    completed evaluation run exists for the older game).
    """
    from datetime import date

    from sqlalchemy import select

    from app.models.game import Game
    from app.models.team import Team
    from app.services.pregame_views import build_team_home

    factory, g_id, t_id, _mv_id = _make_session_with_fixture()

    with factory() as s:
        opponent = s.execute(select(Team).where(Team.code != "LG")).scalars().first()
        assert opponent is not None
        opponent_code = opponent.code
        older = Game(
            external_id="20260414OLDER0",
            home_team_id=t_id,
            away_team_id=opponent.id,
            game_date=date(2026, 4, 14),
            venue="Jamsil Baseball Stadium",
        )
        s.add(older)
        s.commit()
        older_id = int(older.id)

    with factory() as s:
        home = build_team_home(s, "LG")
        assert home.today is not None
        # The fixture game (2026-04-15) is the most recent → today.
        assert home.today.game_id == g_id
        # The older game (2026-04-14) appears under recent.
        assert [r.game_id for r in home.recent] == [older_id]
        recent_game = home.recent[0]
        assert recent_game.opponent_team_code == opponent_code
        assert recent_game.game_date == date(2026, 4, 14)
        assert recent_game.verdict is None


def test_team_home_lineup_status_reflects_snapshot() -> None:
    """pipeline_status['lineup'] is 'waiting' with no lineup, 'normalized' once one exists."""
    from sqlalchemy import delete

    from app.models.snapshot import ActualLineupSnapshot, IngestionRun
    from app.services.pregame_views import build_team_home

    factory, g_id, t_id, _mv_id = _make_session_with_fixture()

    # The fixture seeds a lineup snapshot; remove it so we can test the 'waiting' branch.
    with factory() as s:
        s.execute(
            delete(ActualLineupSnapshot).where(
                ActualLineupSnapshot.game_id == g_id,
                ActualLineupSnapshot.team_id == t_id,
            )
        )
        s.commit()

    with factory() as s:
        before = build_team_home(s, "LG")
        assert before.today is not None
        assert before.today.pipeline_status["lineup"] == "waiting"

    with factory() as s:
        ingestion = IngestionRun(source="test-lineup", status="completed")
        s.add(ingestion)
        s.commit()
        lineup = ActualLineupSnapshot(
            game_id=g_id,
            team_id=t_id,
            ingestion_run_id=ingestion.id,
            announced_at=datetime(2026, 4, 15, 8, 30, tzinfo=UTC),
            content_hash="lineuphash-1",
        )
        s.add(lineup)
        s.commit()

    with factory() as s:
        after = build_team_home(s, "LG")
        assert after.today is not None
        assert after.today.pipeline_status["lineup"] == "normalized"


def test_team_home_postgame_status_reflects_review_run() -> None:
    """pipeline_status['postgame'] flips to 'complete' once a completed review exists."""
    from sqlalchemy import select

    from app.models.evaluation import LineupEvaluationRun
    from app.models.postgame import PostgameReviewRun
    from app.models.snapshot import (
        ActualLineupSnapshot,
        BoxScoreSnapshot,
        IngestionRun,
        StatSnapshot,
    )
    from app.services.pregame_views import build_team_home

    factory, g_id, t_id, mv_id = _make_session_with_fixture()

    with factory() as s:
        before = build_team_home(s, "LG")
        assert before.today is not None
        assert before.today.pipeline_status["postgame"] == "waiting"

    with factory() as s:
        ingestion = IngestionRun(source="test-box", status="completed")
        s.add(ingestion)
        s.commit()
        box = BoxScoreSnapshot(
            game_id=g_id,
            ingestion_run_id=ingestion.id,
            taken_at=datetime(2026, 4, 15, 13, 0, tzinfo=UTC),
            content_hash="boxhash-2",
        )
        s.add(box)
        s.commit()
        # Reuse the fixture-seeded stat/lineup snapshots so the eval run references real rows.
        stat_snapshot_id = s.execute(select(StatSnapshot.id)).scalars().first()
        lineup_snapshot_id = s.execute(select(ActualLineupSnapshot.id)).scalars().first()
        assert stat_snapshot_id is not None
        assert lineup_snapshot_id is not None
        eval_run = LineupEvaluationRun(
            game_id=g_id,
            team_id=t_id,
            model_version_id=mv_id,
            stat_snapshot_id=stat_snapshot_id,
            lineup_snapshot_id=lineup_snapshot_id,
            evaluation_cutoff_at=CUTOFF,
            status="completed",
            finished_at=datetime(2026, 4, 15, 12, 0, tzinfo=UTC),
        )
        s.add(eval_run)
        s.commit()
        review = PostgameReviewRun(
            evaluation_run_id=eval_run.id,
            box_score_snapshot_id=box.id,
            model_version_id=mv_id,
            status="completed",
            finished_at=datetime(2026, 4, 15, 14, 0, tzinfo=UTC),
        )
        s.add(review)
        s.commit()

    with factory() as s:
        after = build_team_home(s, "LG")
        assert after.today is not None
        assert after.today.pipeline_status["postgame"] == "complete"


def test_team_home_box_status_reflects_snapshot() -> None:
    """pipeline_status['box'] is 'waiting' with no box score, 'normalized' once one exists."""
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
        assert before.today.pipeline_status["box"] == "waiting"

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
        assert after.today.pipeline_status["box"] == "normalized"


def test_team_home_pipeline_status_surfaces_failed_run() -> None:
    """A failed pregame ingestion run surfaces as 'failed' in team-home (canonical passthrough)."""
    from app.models.game import Game
    from app.models.snapshot import IngestionRun
    from app.services.pregame_views import build_team_home

    factory, g_id, _t_id, _mv_id = _make_session_with_fixture()

    with factory() as s:
        game = s.get(Game, g_id)
        assert game is not None
        ext_id = game.external_id
        # The lineup category keys off the pregame ingestion run; a failed run
        # must surface as 'failed' regardless of any seeded snapshot.
        s.add(
            IngestionRun(
                source=f"pipeline:ingest-pregame:{ext_id}",
                status="failed",
                error_message="boom",
            )
        )
        s.commit()

    with factory() as s:
        home = build_team_home(s, "LG")
        assert home.today is not None
        assert home.today.pipeline_status["lineup"] == "failed"


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


def test_lineup_comparison_main_reason_uses_recommended_rationale(
    client: TestClient, _game_id: int, _team_id: int, _model_version_id: int
) -> None:
    """The 사유 (main_reason) carries the recommended player's batting-order rationale.

    No LLM provider runs in tests, so the deterministic fallback rationale
    '규칙 기반 배정: N번' surfaces in main_reason for each slot.
    """
    body = _replay_body(_game_id, _team_id, _model_version_id)
    client.post("/api/jobs/replay-evaluation", json=body)

    rows = client.get(f"/api/games/{_game_id}/lineup-comparison").json()["rows"]
    for row in rows:
        assert row["main_reason"] == f"규칙 기반 배정: {row['batting_order']}번"


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


def test_player_comparison_includes_risp_avg_key(
    client: TestClient, _game_id: int, _team_id: int, _model_version_id: int
) -> None:
    """Both actual and recommended stat blocks expose a serialized risp_avg key.

    The fixture does not seed a RISP value, so risp_avg is null here; the test
    only asserts the KEY is present in the serialized model (backward-compatible
    new field), mirroring the other optional rate stats.
    """
    body = _replay_body(_game_id, _team_id, _model_version_id)
    client.post("/api/jobs/replay-evaluation", json=body)

    resp = client.get(f"/api/games/{_game_id}/players/compare?batting_order=1")
    assert resp.status_code == 200
    data = resp.json()
    for key in ("actual", "recommended"):
        assert "risp_avg" in data[key]
        assert data[key]["risp_avg"] is None


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

    Run-expectancy change: both come from compute_lineup_score, which now
    produces expected runs (~3-6) — the lineup's batting order run through the
    Markov model on season OBP/SLG, plus a small run-unit handedness penalty
    (weighted_player_score = raw expected runs, position_completeness = 0.0).

    The actual and recommended lineups draw from the same player pool here, so
    they differ only in batting order; their expected-run totals should stay
    within ~0.3 of each other — far closer than the previous scale-mismatch bug
    where the two scores lived in different numeric spaces.
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


# ---------------------------------------------------------------------------
# GET /api/games/{id}/pregame — opponent-starter quality block
# ---------------------------------------------------------------------------


def test_pregame_opponent_pitcher_is_null_without_data(
    client: TestClient, _game_id: int, _team_id: int, _model_version_id: int
) -> None:
    """opponent_pitcher is null when the run's key_insights carry no pitcher block.

    The LG fixture has no opponent starter, so evaluation produces no
    opponent_pitcher block; the field must serialize as null (backward
    compatible) rather than being absent.
    """
    body = _replay_body(_game_id, _team_id, _model_version_id)
    client.post("/api/jobs/replay-evaluation", json=body)

    resp = client.get(f"/api/games/{_game_id}/pregame")
    assert resp.status_code == 200
    data = resp.json()
    assert "opponent_pitcher" in data
    assert data["opponent_pitcher"] is None


def test_pregame_opponent_pitcher_maps_key_insights_block() -> None:
    """build_pregame_view maps a key_insights opponent_pitcher block into the response.

    Seeds a completed evaluation run whose summary carries an opponent_pitcher
    block (era/whip/k_pct/multiplier) and asserts the values are surfaced on the
    PregameResponse. k_pct is a FRACTION as written by the evaluator.
    """
    from sqlalchemy import select

    from app.models.evaluation import LineupEvaluationRun, LineupEvaluationSummary
    from app.models.snapshot import ActualLineupSnapshot, StatSnapshot
    from app.services.pregame_views import build_pregame_view

    factory, g_id, t_id, mv_id = _make_session_with_fixture()

    with factory() as s:
        stat_snapshot_id = s.execute(select(StatSnapshot.id)).scalars().first()
        lineup_snapshot_id = s.execute(select(ActualLineupSnapshot.id)).scalars().first()
        assert stat_snapshot_id is not None
        assert lineup_snapshot_id is not None
        eval_run = LineupEvaluationRun(
            game_id=g_id,
            team_id=t_id,
            model_version_id=mv_id,
            stat_snapshot_id=stat_snapshot_id,
            lineup_snapshot_id=lineup_snapshot_id,
            evaluation_cutoff_at=CUTOFF,
            status="completed",
            finished_at=datetime(2026, 4, 15, 12, 0, tzinfo=UTC),
        )
        s.add(eval_run)
        s.commit()
        s.add(
            LineupEvaluationSummary(
                evaluation_run_id=eval_run.id,
                summary_text="seeded",
                key_insights_json={
                    "recommended_total_score": 0.8,
                    "actual_total_score": 0.78,
                    "opponent_pitcher": {
                        "era": 3.18,
                        "whip": 1.59,
                        "k_pct": 14.0 / 52.0,
                        "multiplier": 0.95,
                    },
                },
            )
        )
        s.commit()

    with factory() as s:
        view = build_pregame_view(s, g_id, team_id=t_id)
        assert view.opponent_pitcher is not None
        assert view.opponent_pitcher.era == pytest.approx(3.18)
        assert view.opponent_pitcher.whip == pytest.approx(1.59)
        assert view.opponent_pitcher.k_pct == pytest.approx(14.0 / 52.0)
        assert view.opponent_pitcher.multiplier == pytest.approx(0.95)


# ---------------------------------------------------------------------------
# GET /api/games/{id}/players/{player_id}/score-card
# ---------------------------------------------------------------------------


def _recommended_player_id(client: TestClient, game_id: int, batting_order: int) -> int:
    """Read a recommended player_id from the compare endpoint for a slot."""
    resp = client.get(f"/api/games/{game_id}/players/compare?batting_order={batting_order}")
    assert resp.status_code == 200
    return int(resp.json()["recommended"]["player_id"])


def test_score_card_returns_five_factors(
    client: TestClient, _game_id: int, _team_id: int, _model_version_id: int
) -> None:
    """The score-card returns exactly the five scoring components in fixed order."""
    body = _replay_body(_game_id, _team_id, _model_version_id)
    client.post("/api/jobs/replay-evaluation", json=body)
    player_id = _recommended_player_id(client, _game_id, 1)

    resp = client.get(f"/api/games/{_game_id}/players/{player_id}/score-card")
    assert resp.status_code == 200
    data = resp.json()
    assert data["player_id"] == player_id
    components = [f["component"] for f in data["factors"]]
    assert components == [
        "season_offense",
        "recent_form",
        "matchup",
        "position_fit",
        "start_rhythm",
    ]


def test_score_card_axis_scores_in_range(
    client: TestClient, _game_id: int, _team_id: int, _model_version_id: int
) -> None:
    """Every radar axis_score is clamped to [0, 100] and OVR to [0, 99]."""
    body = _replay_body(_game_id, _team_id, _model_version_id)
    client.post("/api/jobs/replay-evaluation", json=body)
    player_id = _recommended_player_id(client, _game_id, 3)

    resp = client.get(f"/api/games/{_game_id}/players/{player_id}/score-card")
    assert resp.status_code == 200
    data = resp.json()
    assert 0 <= data["overall"] <= 99
    for f in data["factors"]:
        assert 0.0 <= f["axis_score"] <= 100.0
    assert data["form_badge"] in ("HOT", "COLD", "NEUTRAL")


def test_score_card_unknown_player_returns_404(
    client: TestClient, _game_id: int, _team_id: int, _model_version_id: int
) -> None:
    """A player_id not in this game's lineups yields 404."""
    body = _replay_body(_game_id, _team_id, _model_version_id)
    client.post("/api/jobs/replay-evaluation", json=body)

    resp = client.get(f"/api/games/{_game_id}/players/99999999/score-card")
    assert resp.status_code == 404


def test_score_card_no_run_returns_404(clean_env: tuple[TestClient, int, int, int]) -> None:
    """With no completed evaluation run, the score-card is 404 (no snapshot to score)."""
    client, game_id, _team_id, _mv_id = clean_env
    resp = client.get(f"/api/games/{game_id}/players/1/score-card")
    assert resp.status_code == 404
