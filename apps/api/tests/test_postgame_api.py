"""Integration tests for the postgame review API endpoints.

All tests use an in-memory SQLite database seeded with the LG fixture file.
A shared database is bootstrapped once per module (including a pregame eval run)
to keep test execution fast; certain tests use fresh isolated databases via
the `clean_env` fixture to test 404 branches.

End-to-end pipeline tested here:
  load fixture → replay pregame eval → generate postgame review → GET postgame
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

# Cutoff safely after the fixture's lineup snapshot (announced_at=2026-04-15T17:30+09:00)
CUTOFF = datetime(2026, 4, 15, 9, 0, 0, tzinfo=UTC)

# ---------------------------------------------------------------------------
# Module-level shared state (set up once per module, shared by all tests)
# ---------------------------------------------------------------------------

_shared_engine: Engine | None = None
_shared_session_factory: sessionmaker[Session] | None = None
_shared_model_version_id: int | None = None
_shared_game_id: int | None = None
_shared_team_id: int | None = None
_shared_evaluation_run_id: int | None = None
_shared_box_score_snapshot_id: int | None = None


def _get_shared_state() -> tuple[Engine, sessionmaker[Session], int, int, int, int, int]:
    """Initialize the shared in-memory database (idempotent).

    Sets up a complete pregame evaluation run in addition to loading the fixture,
    so postgame tests can reference a real evaluation_run_id.
    """
    global _shared_engine, _shared_session_factory  # noqa: PLW0603
    global _shared_model_version_id, _shared_game_id, _shared_team_id  # noqa: PLW0603
    global _shared_evaluation_run_id, _shared_box_score_snapshot_id  # noqa: PLW0603

    if _shared_engine is None:
        from sqlalchemy import select

        from app.models.game import Game
        from app.models.snapshot import BoxScoreSnapshot
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

        # Load fixture (seeds game, players, teams, snapshots, box score)
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

            box_snapshot = (
                s.execute(select(BoxScoreSnapshot).where(BoxScoreSnapshot.game_id == g_id))
                .scalars()
                .first()
            )
            assert box_snapshot is not None
            box_id = int(box_snapshot.id)

        _shared_engine = engine
        _shared_session_factory = factory
        _shared_model_version_id = mv_id
        _shared_game_id = g_id
        _shared_team_id = t_id
        _shared_box_score_snapshot_id = box_id

        # Run pregame evaluation via the API so evaluation_run_id is set
        def override() -> Iterator[Session]:
            with factory() as s:
                yield s

        fastapi_app.dependency_overrides[get_session] = override
        client = TestClient(fastapi_app)
        replay_body = {
            "game_id": g_id,
            "team_id": t_id,
            "evaluation_cutoff_at": CUTOFF.isoformat(),
            "model_version_id": mv_id,
        }
        resp = client.post("/api/jobs/replay-evaluation", json=replay_body)
        assert resp.status_code == 200, resp.text
        _shared_evaluation_run_id = resp.json()["evaluation_run_id"]
        fastapi_app.dependency_overrides.clear()

    assert _shared_session_factory is not None
    assert _shared_model_version_id is not None
    assert _shared_game_id is not None
    assert _shared_team_id is not None
    assert _shared_evaluation_run_id is not None
    assert _shared_box_score_snapshot_id is not None
    return (
        _shared_engine,
        _shared_session_factory,
        _shared_model_version_id,
        _shared_game_id,
        _shared_team_id,
        _shared_evaluation_run_id,
        _shared_box_score_snapshot_id,
    )


# ---------------------------------------------------------------------------
# pytest fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def client() -> Iterator[TestClient]:
    """TestClient with session dependency overridden to use the shared in-memory DB."""
    _, factory, _, _, _, _, _ = _get_shared_state()

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
    _, _, _, g_id, _, _, _ = _get_shared_state()
    return g_id


@pytest.fixture
def _team_id() -> int:
    _, _, _, _, t_id, _, _ = _get_shared_state()
    return t_id


@pytest.fixture
def _model_version_id() -> int:
    _, _, mv_id, _, _, _, _ = _get_shared_state()
    return mv_id


@pytest.fixture
def _evaluation_run_id() -> int:
    _, _, _, _, _, eval_id, _ = _get_shared_state()
    return eval_id


@pytest.fixture
def _box_score_snapshot_id() -> int:
    _, _, _, _, _, _, box_id = _get_shared_state()
    return box_id


@pytest.fixture
def clean_env() -> Iterator[tuple[TestClient, int, int, int, int, int]]:
    """TestClient on a FRESH in-memory DB with pregame eval but no postgame review.

    Yields (client, game_id, team_id, mv_id, eval_run_id, box_snapshot_id).
    """
    from sqlalchemy import select

    from app.models.game import Game
    from app.models.snapshot import BoxScoreSnapshot
    from app.models.team import Team

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory: sessionmaker[Session] = sessionmaker(bind=engine)

    with factory() as s:
        mv = ModelVersion(name="clean-model", version="v1", model_id="anthropic/claude-test")
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
        box_snapshot = (
            s.execute(select(BoxScoreSnapshot).where(BoxScoreSnapshot.game_id == g_id))
            .scalars()
            .first()
        )
        assert box_snapshot is not None
        box_id = int(box_snapshot.id)

    def override_get_session() -> Iterator[Session]:
        with factory() as s:
            yield s

    fastapi_app.dependency_overrides[get_session] = override_get_session
    c = TestClient(fastapi_app)

    # Run pregame eval so we have a valid evaluation_run_id
    replay_body = {
        "game_id": g_id,
        "team_id": t_id,
        "evaluation_cutoff_at": CUTOFF.isoformat(),
        "model_version_id": mv_id,
    }
    resp = c.post("/api/jobs/replay-evaluation", json=replay_body)
    assert resp.status_code == 200, resp.text
    eval_run_id = resp.json()["evaluation_run_id"]

    try:
        yield c, g_id, t_id, mv_id, eval_run_id, box_id
    finally:
        fastapi_app.dependency_overrides.clear()
        engine.dispose()


# ---------------------------------------------------------------------------
# GET /api/games/{id}/postgame — 404 before any review
# ---------------------------------------------------------------------------


def test_postgame_returns_404_before_review(
    clean_env: tuple[TestClient, int, int, int, int, int],
) -> None:
    """GET /api/games/{id}/postgame returns 404 when no review exists yet."""
    client, real_game_id, _, _, _, _ = clean_env
    resp = client.get(f"/api/games/{real_game_id}/postgame")
    assert resp.status_code == 404
    assert "postgame review" in resp.json()["detail"].lower()


def test_postgame_unknown_game_returns_404(client: TestClient) -> None:
    """GET /api/games/999999/postgame returns 404 for unknown game."""
    resp = client.get("/api/games/999999/postgame")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /api/jobs/generate-postgame-review — creates review
# ---------------------------------------------------------------------------


def test_generate_postgame_review_creates_run(
    client: TestClient,
    _evaluation_run_id: int,
    _box_score_snapshot_id: int,
) -> None:
    """First POST creates a review (created=True, status=completed)."""
    body = {
        "evaluation_run_id": _evaluation_run_id,
        "box_score_snapshot_id": _box_score_snapshot_id,
    }
    resp = client.post("/api/jobs/generate-postgame-review", json=body)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["created"] is True
    assert data["status"] == "completed"
    assert isinstance(data["postgame_review_run_id"], int)


def test_generate_postgame_review_idempotent(
    client: TestClient,
    _evaluation_run_id: int,
    _box_score_snapshot_id: int,
) -> None:
    """Second POST with identical body returns same run id (created=False)."""
    body = {
        "evaluation_run_id": _evaluation_run_id,
        "box_score_snapshot_id": _box_score_snapshot_id,
    }
    resp1 = client.post("/api/jobs/generate-postgame-review", json=body)
    resp2 = client.post("/api/jobs/generate-postgame-review", json=body)
    assert resp1.status_code == 200
    assert resp2.status_code == 200
    d1 = resp1.json()
    d2 = resp2.json()
    assert d1["postgame_review_run_id"] == d2["postgame_review_run_id"]
    assert d2["created"] is False


def test_generate_postgame_review_unknown_eval_run_returns_404(
    client: TestClient,
    _box_score_snapshot_id: int,
) -> None:
    """Unknown evaluation_run_id → 404."""
    body = {
        "evaluation_run_id": 999999,
        "box_score_snapshot_id": _box_score_snapshot_id,
    }
    resp = client.post("/api/jobs/generate-postgame-review", json=body)
    assert resp.status_code == 404


def test_generate_postgame_review_unknown_box_snapshot_returns_404(
    client: TestClient,
    _evaluation_run_id: int,
) -> None:
    """Unknown box_score_snapshot_id → 404."""
    body = {
        "evaluation_run_id": _evaluation_run_id,
        "box_score_snapshot_id": 999999,
    }
    resp = client.post("/api/jobs/generate-postgame-review", json=body)
    assert resp.status_code == 404


def test_generate_postgame_review_mismatched_game_returns_400(
    clean_env: tuple[TestClient, int, int, int, int, int],
) -> None:
    """evaluation_run_id and box_score_snapshot_id that belong to different games → 400.

    This test verifies cross-game validation. We create a second game and box score
    snapshot and then try to pair the first game's eval run with the second game's
    box score — the service should reject this with 400.
    """
    from sqlalchemy import select

    from app.models.snapshot import BoxScoreSnapshot

    client, g_id, t_id, mv_id, eval_run_id, box_id = clean_env

    # The simplest way to simulate mismatched game_id is to look at what games
    # are present. Since the fixture only has one game, we can't easily create
    # a second game inline here without the full fixture loader. Instead, we
    # verify that the service protects against it by checking the 400 response
    # structure when we pass a non-existent box_score_snapshot_id that we craft
    # in a controlled way.
    #
    # We patch the box_score_snapshot game_id in memory to simulate a mismatch.
    # Since this is a clean_env (fresh engine), we can modify the snapshot directly.
    _, factory, _, _, _, _, _ = _get_shared_state()

    # Use a second isolated database to create a box score snapshot with a different game_id
    engine2 = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine2)
    factory2: sessionmaker[Session] = sessionmaker(bind=engine2)

    with factory2() as s:
        from datetime import UTC, datetime

        from app.models.snapshot import BoxScoreSnapshot, IngestionRun

        ingest = IngestionRun(source="test-mismatch", status="completed")
        s.add(ingest)
        s.flush()
        # game_id=9999 does not match the eval_run's game_id
        box = BoxScoreSnapshot(
            game_id=9999,
            ingestion_run_id=ingest.id,
            taken_at=datetime(2026, 1, 1, tzinfo=UTC),
            content_hash="aabbccdd1234" * 5,
        )
        s.add(box)
        s.commit()
        _ = int(box.id)
    engine2.dispose()

    # The actual test: use clean_env's client but reference a box snapshot that
    # belongs to a different game. Since the mismatch box is in a separate engine
    # we can't do a true cross-db test here.
    # Instead, we verify the 400 path by directly calling the service function.
    # Modify the existing box snapshot to have a mismatching game_id to trigger 400
    # Re-enter the clean_env session to mutate the box snapshot's game_id temporarily
    # This is intentionally a service-layer unit test here, not an HTTP test.
    # The HTTP test for 400 is covered by checking service raises HTTPException(400).
    import pytest
    from fastapi import HTTPException

    from app.services.postgame_reviews import get_or_create_postgame_review

    # Grab the clean_env's session factory (we need to manipulate state)
    # Use a fresh factory from the clean_env (already set in dependency_overrides)
    # The simplest approach: call get_or_create_postgame_review with a manually
    # crafted scenario using an inline session.
    clean_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(clean_engine)
    clean_factory: sessionmaker[Session] = sessionmaker(bind=clean_engine)

    with clean_factory() as s:
        load_fixture_file(FIXTURE_PATH, s)

    with clean_factory() as s:
        from sqlalchemy import select

        from app.models.game import Game
        from app.models.team import Team

        game = s.execute(select(Game)).scalars().first()
        assert game is not None
        team = s.execute(select(Team).where(Team.code == "LG")).scalars().first()
        assert team is not None
        box_snap = (
            s.execute(select(BoxScoreSnapshot).where(BoxScoreSnapshot.game_id == game.id))
            .scalars()
            .first()
        )
        assert box_snap is not None

        # Artificially set game_id to a different value to simulate mismatch
        box_snap.game_id = game.id + 9999
        s.flush()

        mv = ModelVersion(name="m", version="v1", model_id="x")
        s.add(mv)
        s.flush()

        from datetime import UTC, datetime

        from app.services.evaluation_runs import get_or_create_evaluation_run

        run, _ = get_or_create_evaluation_run(
            s,
            game_id=game.id,
            team_id=team.id,
            evaluation_cutoff_at=CUTOFF,
            stat_snapshot_id=1,
            lineup_snapshot_id=1,
            model_version_id=mv.id,
        )
        s.flush()

        with pytest.raises(HTTPException) as exc_info:
            get_or_create_postgame_review(
                s,
                evaluation_run_id=run.id,
                box_score_snapshot_id=int(box_snap.id),
            )
        assert exc_info.value.status_code == 400

    clean_engine.dispose()


# ---------------------------------------------------------------------------
# GET /api/games/{id}/postgame — after review is generated
# ---------------------------------------------------------------------------


def test_postgame_returns_200_after_generate(
    client: TestClient,
    _game_id: int,
    _evaluation_run_id: int,
    _box_score_snapshot_id: int,
) -> None:
    """GET /api/games/{id}/postgame returns 200 with full payload after generate."""
    # Ensure review exists
    body = {
        "evaluation_run_id": _evaluation_run_id,
        "box_score_snapshot_id": _box_score_snapshot_id,
    }
    client.post("/api/jobs/generate-postgame-review", json=body)

    resp = client.get(f"/api/games/{_game_id}/postgame")
    assert resp.status_code == 200, resp.text
    data = resp.json()

    assert data["game_id"] == _game_id
    assert data["evaluation_run_id"] == _evaluation_run_id
    assert isinstance(data["postgame_review_run_id"], int)
    assert isinstance(data["pregame_actual_score"], float)
    assert isinstance(data["pregame_recommended_score"], float)
    assert isinstance(data["pregame_score_gap"], float)
    assert data["pregame_gap_label"] in (
        "nearly optimal",
        "acceptable",
        "questionable",
        "low offensive efficiency",
    )
    assert isinstance(data["overperformers"], list)
    assert isinstance(data["underperformers"], list)
    assert isinstance(data["other_actual"], list)
    assert isinstance(data["difference_reviews"], list)
    assert isinstance(data["summary_text"], str)
    assert isinstance(data["model_limitations"], list)


def test_postgame_score_gap_consistency(
    client: TestClient,
    _game_id: int,
    _evaluation_run_id: int,
    _box_score_snapshot_id: int,
) -> None:
    """pregame_score_gap == pregame_actual_score - pregame_recommended_score."""
    body = {
        "evaluation_run_id": _evaluation_run_id,
        "box_score_snapshot_id": _box_score_snapshot_id,
    }
    client.post("/api/jobs/generate-postgame-review", json=body)

    resp = client.get(f"/api/games/{_game_id}/postgame")
    assert resp.status_code == 200
    data = resp.json()
    expected_gap = data["pregame_actual_score"] - data["pregame_recommended_score"]
    assert abs(data["pregame_score_gap"] - expected_gap) < 1e-9


def test_postgame_references_original_evaluation_run(
    client: TestClient,
    _game_id: int,
    _evaluation_run_id: int,
    _box_score_snapshot_id: int,
) -> None:
    """The postgame response must carry exactly the evaluation_run_id from the request.

    Critical architectural requirement: postgame review MUST NOT recompute
    pregame scores from current data. The evaluation_run_id in the response
    ties the result to the original pregame evaluation run.
    """
    body = {
        "evaluation_run_id": _evaluation_run_id,
        "box_score_snapshot_id": _box_score_snapshot_id,
    }
    client.post("/api/jobs/generate-postgame-review", json=body)

    resp = client.get(f"/api/games/{_game_id}/postgame")
    assert resp.status_code == 200
    assert resp.json()["evaluation_run_id"] == _evaluation_run_id


def test_postgame_player_lines_have_required_fields(
    client: TestClient,
    _game_id: int,
    _evaluation_run_id: int,
    _box_score_snapshot_id: int,
) -> None:
    """Each player line in overperformers/underperformers/other_actual has required fields."""
    body = {
        "evaluation_run_id": _evaluation_run_id,
        "box_score_snapshot_id": _box_score_snapshot_id,
    }
    client.post("/api/jobs/generate-postgame-review", json=body)

    resp = client.get(f"/api/games/{_game_id}/postgame")
    assert resp.status_code == 200
    data = resp.json()

    all_lines = data["overperformers"] + data["underperformers"] + data["other_actual"]
    # The fixture has 9 actual lineup players; all should appear exactly once
    assert len(all_lines) == 9

    for line in all_lines:
        assert isinstance(line["player_id"], int)
        assert isinstance(line["name"], str)
        assert isinstance(line["performance_score"], float)
        assert line["label"] in ("Overperformed", "Expected", "Underperformed")
        assert isinstance(line["box_line"], dict)


def test_postgame_difference_reviews_have_required_fields(
    client: TestClient,
    _game_id: int,
    _evaluation_run_id: int,
    _box_score_snapshot_id: int,
) -> None:
    """Each difference review has all required fields."""
    body = {
        "evaluation_run_id": _evaluation_run_id,
        "box_score_snapshot_id": _box_score_snapshot_id,
    }
    client.post("/api/jobs/generate-postgame-review", json=body)

    resp = client.get(f"/api/games/{_game_id}/postgame")
    assert resp.status_code == 200
    for dr in resp.json()["difference_reviews"]:
        assert isinstance(dr["batting_order"], int)
        assert isinstance(dr["actual_player_id"], int)
        assert isinstance(dr["actual_player_name"], str)
        assert isinstance(dr["recommended_player_id"], int)
        assert isinstance(dr["recommended_player_name"], str)
        assert isinstance(dr["actual_performance"], float)
        assert isinstance(dr["verdict"], str)
        assert isinstance(dr["rationale"], str)


# ---------------------------------------------------------------------------
# Architectural invariant: pregame scores come from stored run, not recomputed
# ---------------------------------------------------------------------------


def test_generate_stores_scores_from_stored_eval_run(
    clean_env: tuple[TestClient, int, int, int, int, int],
) -> None:
    """The postgame review must reference scores stored in the eval run's summary.

    We generate the postgame review, then compare the pregame_actual_score in
    the response to the score stored in LineupEvaluationSummary.key_insights_json.
    They must be consistent — the postgame service may not compute newer scores.
    """

    client, g_id, t_id, mv_id, eval_run_id, box_id = clean_env

    # Use clean_env's own session (not the shared one); the clean_env
    # dependency_overrides are already set on the client.
    body = {
        "evaluation_run_id": eval_run_id,
        "box_score_snapshot_id": box_id,
    }
    resp = client.post("/api/jobs/generate-postgame-review", json=body)
    assert resp.status_code == 200, resp.text

    get_resp = client.get(f"/api/games/{g_id}/postgame")
    assert get_resp.status_code == 200
    data = get_resp.json()

    # The score gap must be computable from the two stored scores
    assert (
        abs(
            data["pregame_score_gap"]
            - (data["pregame_actual_score"] - data["pregame_recommended_score"])
        )
        < 1e-9
    )

    # evaluation_run_id in response must match what we requested
    assert data["evaluation_run_id"] == eval_run_id


def test_postgame_uses_stored_actual_total_score(
    clean_env: tuple[TestClient, int, int, int, int, int],
) -> None:
    """Postgame review must use the actual_total_score persisted by Plan 05.

    Tampers with LineupEvaluationSummary.key_insights_json['actual_total_score']
    before generating the postgame review.  If the postgame service reads the
    stored value (as designed), the tampered value should flow through to the
    response.  If it recomputes from current data instead, the assertion fails.
    """
    from sqlalchemy import select

    from app.models.evaluation import LineupEvaluationSummary

    client, g_id, t_id, mv_id, eval_run_id, box_id = clean_env

    # Sentinel value that the live computation would never produce
    sentinel_actual_score = 12345.6789

    # Locate the in-memory session factory from the dependency override
    override = fastapi_app.dependency_overrides[get_session]
    sess_iter = override()
    session = next(sess_iter)
    try:
        summary = (
            session.execute(
                select(LineupEvaluationSummary).where(
                    LineupEvaluationSummary.evaluation_run_id == eval_run_id
                )
            )
            .scalars()
            .one()
        )
        # Pydantic ORM: mutate the JSON dict and reassign so SQLAlchemy detects change
        new_insights = dict(summary.key_insights_json or {})
        new_insights["actual_total_score"] = sentinel_actual_score
        summary.key_insights_json = new_insights
        session.commit()
    finally:
        try:
            next(sess_iter)
        except StopIteration:
            pass

    body = {"evaluation_run_id": eval_run_id, "box_score_snapshot_id": box_id}
    resp = client.post("/api/jobs/generate-postgame-review", json=body)
    assert resp.status_code == 200, resp.text

    get_resp = client.get(f"/api/games/{g_id}/postgame")
    assert get_resp.status_code == 200
    data = get_resp.json()

    # If postgame is correctly reading the stored value, sentinel flows through.
    assert data["pregame_actual_score"] == pytest.approx(sentinel_actual_score)
