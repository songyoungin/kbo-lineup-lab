"""어드민 수집 상태 API 통합 테스트.

모든 테스트는 인메모리 SQLite 데이터베이스를 사용한다.
- 공유 DB: fixture 파일만 로드한 기본 상태
- 격리 DB: 각 테스트가 별도 clean 환경에서 IngestionRun을 직접 삽입

테스트 목록:
  1. GET /api/admin/ingestion-runs — 런 없을 때 빈 목록 반환
  2. IngestionRun 삽입 후 목록에 표시
  3. GET /api/admin/games/999999/ingestion-status — 알 수 없는 game_id → 404
  4. fixture game에 대해 모든 카테고리가 waiting 상태 반환
  5. daily IngestionRun 삽입 후 schedule 카테고리가 반영
  6. failed IngestionRun이 에러 메시지와 함께 표시
  7. limit 파라미터가 준수됨 (10개 삽입 후 limit=5 요청 → 5개 반환)
  8. completed IngestionRun이 목록에 정상 표시
  9. game_id 기반 ingestion-status 응답 구조 검증
  10. limit 경계값(limit=1) 동작 검증
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
from app.models.snapshot import IngestionRun
from app.services.fixture_loader import load_fixture_file

FIXTURE_PATH = Path(__file__).parent.parent / "fixtures" / "lg_2026_sample.json"

# ---------------------------------------------------------------------------
# Module-level shared state (fixture game loaded once)
# ---------------------------------------------------------------------------

_shared_engine: Engine | None = None
_shared_session_factory: sessionmaker[Session] | None = None
_shared_game_id: int | None = None


def _get_shared_state() -> tuple[Engine, sessionmaker[Session], int]:
    """픽스처 파일이 로드된 공유 인메모리 DB를 초기화한다 (멱등)."""
    global _shared_engine, _shared_session_factory, _shared_game_id  # noqa: PLW0603

    if _shared_engine is None:
        from sqlalchemy import select

        from app.models.game import Game

        engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(engine)
        factory: sessionmaker[Session] = sessionmaker(bind=engine)

        with factory() as s:
            load_fixture_file(FIXTURE_PATH, s)

        with factory() as s:
            game = s.execute(select(Game)).scalars().first()
            assert game is not None
            g_id = int(game.id)

        _shared_engine = engine
        _shared_session_factory = factory
        _shared_game_id = g_id

    assert _shared_session_factory is not None
    assert _shared_game_id is not None
    return _shared_engine, _shared_session_factory, _shared_game_id


# ---------------------------------------------------------------------------
# pytest fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def client() -> Iterator[TestClient]:
    """공유 DB에 연결된 TestClient를 반환한다."""
    _, factory, _ = _get_shared_state()

    def override() -> Iterator[Session]:
        with factory() as s:
            yield s

    fastapi_app.dependency_overrides[get_session] = override
    try:
        yield TestClient(fastapi_app)
    finally:
        fastapi_app.dependency_overrides.clear()


@pytest.fixture
def _game_id() -> int:
    _, _, g_id = _get_shared_state()
    return g_id


@pytest.fixture
def clean_env() -> Iterator[tuple[TestClient, sessionmaker[Session], int]]:
    """픽스처만 로드된 FRESH 인메모리 DB와 TestClient를 반환한다.

    Yields:
        (client, session_factory, game_id) 튜플.
    """
    from sqlalchemy import select

    from app.models.game import Game

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory: sessionmaker[Session] = sessionmaker(bind=engine)

    with factory() as s:
        load_fixture_file(FIXTURE_PATH, s)

    with factory() as s:
        game = s.execute(select(Game)).scalars().first()
        assert game is not None
        g_id = int(game.id)

    def override() -> Iterator[Session]:
        with factory() as s:
            yield s

    fastapi_app.dependency_overrides[get_session] = override
    try:
        yield TestClient(fastapi_app), factory, g_id
    finally:
        fastapi_app.dependency_overrides.clear()
        engine.dispose()


@pytest.fixture
def empty_env() -> Iterator[tuple[TestClient, sessionmaker[Session]]]:
    """IngestionRun이 전혀 없는 FRESH 빈 DB와 TestClient를 반환한다.

    게임 데이터도 없는 순수 빈 DB. 수집 런 목록 테스트에 사용.

    Yields:
        (client, session_factory) 튜플.
    """
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory: sessionmaker[Session] = sessionmaker(bind=engine)

    def override() -> Iterator[Session]:
        with factory() as s:
            yield s

    fastapi_app.dependency_overrides[get_session] = override
    try:
        yield TestClient(fastapi_app), factory
    finally:
        fastapi_app.dependency_overrides.clear()
        engine.dispose()


# ---------------------------------------------------------------------------
# GET /api/admin/ingestion-runs
# ---------------------------------------------------------------------------


def test_ingestion_runs_empty_when_no_runs(
    empty_env: tuple[TestClient, sessionmaker[Session]],
) -> None:
    """런이 없을 때 빈 목록을 반환한다."""
    client, _ = empty_env
    resp = client.get("/api/admin/ingestion-runs")
    assert resp.status_code == 200
    body = resp.json()
    assert body["runs"] == []


def test_ingestion_runs_shows_inserted_run(
    empty_env: tuple[TestClient, sessionmaker[Session]],
) -> None:
    """IngestionRun 삽입 후 목록에 표시된다."""
    client, factory = empty_env

    with factory() as s:
        run = IngestionRun(
            source="pipeline:ingest-daily:2026-04-15",
            status="completed",
            started_at=datetime(2026, 4, 15, 1, 0, 0, tzinfo=UTC),
            finished_at=datetime(2026, 4, 15, 1, 5, 0, tzinfo=UTC),
        )
        s.add(run)
        s.commit()
        run_id = run.id

    resp = client.get("/api/admin/ingestion-runs")
    assert resp.status_code == 200
    runs = resp.json()["runs"]
    assert len(runs) == 1
    assert runs[0]["id"] == run_id
    assert runs[0]["source"] == "pipeline:ingest-daily:2026-04-15"
    assert runs[0]["status"] == "completed"
    assert runs[0]["error_message"] is None


def test_ingestion_runs_failed_run_has_error_message(
    empty_env: tuple[TestClient, sessionmaker[Session]],
) -> None:
    """failed 상태 run은 error_message가 포함된다."""
    client, factory = empty_env

    with factory() as s:
        run = IngestionRun(
            source="pipeline:ingest-daily:2026-04-16",
            status="failed",
            error_message="ConnectionError: timeout",
        )
        s.add(run)
        s.commit()

    resp = client.get("/api/admin/ingestion-runs")
    assert resp.status_code == 200
    runs = resp.json()["runs"]
    assert len(runs) == 1
    assert runs[0]["status"] == "failed"
    assert runs[0]["error_message"] == "ConnectionError: timeout"


def test_ingestion_runs_limit_respected(
    empty_env: tuple[TestClient, sessionmaker[Session]],
) -> None:
    """limit 파라미터가 준수된다 (10개 삽입 후 limit=5 → 5개 반환)."""
    client, factory = empty_env

    with factory() as s:
        for i in range(10):
            s.add(
                IngestionRun(
                    source=f"pipeline:ingest-daily:2026-04-{i + 1:02d}",
                    status="completed",
                )
            )
        s.commit()

    resp = client.get("/api/admin/ingestion-runs?limit=5")
    assert resp.status_code == 200
    assert len(resp.json()["runs"]) == 5


def test_ingestion_runs_limit_one(
    empty_env: tuple[TestClient, sessionmaker[Session]],
) -> None:
    """limit=1은 가장 최근 run 하나만 반환한다."""
    client, factory = empty_env

    with factory() as s:
        for i in range(3):
            s.add(
                IngestionRun(
                    source=f"pipeline:ingest-daily:2026-05-{i + 1:02d}",
                    status="completed",
                )
            )
        s.commit()

    resp = client.get("/api/admin/ingestion-runs?limit=1")
    assert resp.status_code == 200
    assert len(resp.json()["runs"]) == 1


# ---------------------------------------------------------------------------
# GET /api/admin/games/{game_id}/ingestion-status
# ---------------------------------------------------------------------------


def test_game_ingestion_status_unknown_game_returns_404(client: TestClient) -> None:
    """존재하지 않는 game_id는 404를 반환한다."""
    resp = client.get("/api/admin/games/999999/ingestion-status")
    assert resp.status_code == 404
    assert "not found" in resp.json()["detail"].lower()


def test_game_ingestion_status_all_waiting_when_no_runs(client: TestClient, _game_id: int) -> None:
    """IngestionRun이 없으면 모든 카테고리가 waiting 상태다."""
    resp = client.get(f"/api/admin/games/{_game_id}/ingestion-status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["game_id"] == _game_id
    categories = {c["category"]: c for c in body["categories"]}
    expected = {
        "schedule",
        "roster",
        "player_stats",
        "stat_snapshot",
        "lineup",
        "evaluation",
        "box_score",
        "postgame_review",
    }
    assert set(categories.keys()) == expected
    for cat_status in body["categories"]:
        assert cat_status["status"] == "waiting"


def test_game_ingestion_status_response_structure(client: TestClient, _game_id: int) -> None:
    """응답이 필수 필드를 모두 포함한다."""
    resp = client.get(f"/api/admin/games/{_game_id}/ingestion-status")
    assert resp.status_code == 200
    body = resp.json()
    # 최상위 필드
    assert "game_id" in body
    assert "game_external_id" in body
    assert "game_date" in body
    assert "categories" in body
    # 각 카테고리 필드
    for cat in body["categories"]:
        assert "category" in cat
        assert "status" in cat
        assert "raw_payload_id" in cat
        assert "snapshot_id" in cat
        assert "run_id" in cat
        assert "error_message" in cat


def test_game_ingestion_status_after_failed_daily_run(
    clean_env: tuple[TestClient, sessionmaker[Session], int],
) -> None:
    """daily IngestionRun이 failed 상태면 schedule 카테고리가 failed를 반환한다."""

    from app.models.game import Game

    client, factory, g_id = clean_env

    # game의 날짜 조회
    with factory() as s:
        game = s.get(Game, g_id)
        assert game is not None
        game_date = game.game_date

    with factory() as s:
        run = IngestionRun(
            source=f"pipeline:ingest-daily:{game_date.isoformat()}",
            status="failed",
            error_message="HTTPError: 503 Service Unavailable",
        )
        s.add(run)
        s.commit()

    resp = client.get(f"/api/admin/games/{g_id}/ingestion-status")
    assert resp.status_code == 200
    categories = {c["category"]: c for c in resp.json()["categories"]}
    schedule = categories["schedule"]
    assert schedule["status"] == "failed"
    assert schedule["error_message"] == "HTTPError: 503 Service Unavailable"
    assert schedule["run_id"] is not None
