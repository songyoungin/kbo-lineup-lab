"""파이프라인 잡 오케스트레이션 테스트.

검증 항목:
- daily_pipeline: 정상 실행; 멱등성; 예외 시 failed 마킹
- pregame_pipeline: WAITING 라인업 → failed; COLLECTED → eval 실행; 멱등성
- postgame_pipeline: WAITING 박스스코어 → failed; COLLECTED → review 실행; 멱등성
- CLI: typer.CliRunner를 통한 각 명령어 연기 검증
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager
from datetime import UTC, date, datetime
from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from typer.testing import CliRunner

import app.models  # noqa: F401 — Base.metadata에 모든 모델 등록
from app.cli import app as cli_app
from app.db.base import Base
from app.ingestion.collectors.box_score import BoxScoreCollectionResult, BoxScoreStatus
from app.ingestion.collectors.lineup import LineupCollectionResult, LineupStatus
from app.ingestion.http_client import HttpClient
from app.ingestion.normalizers.box_score import BoxScoreNormalizeResult
from app.ingestion.normalizers.lineup import LineupNormalizeResult
from app.jobs.daily_pipeline import DailyPipelineResult, run_daily_pipeline
from app.jobs.postgame_pipeline import PostgamePipelineResult, run_postgame_pipeline
from app.jobs.pregame_pipeline import PregamePipelineResult, run_pregame_pipeline
from app.models.evaluation import LineupEvaluationRun, ModelVersion
from app.models.game import Game
from app.models.player import Player
from app.models.snapshot import (
    ActualLineupSnapshot,
    ActualLineupSnapshotRow,
    BoxScoreRow,
    BoxScoreSnapshot,
    IngestionRun,
    PlayerStatSnapshotRow,
    RawIngestionPayload,
    StatSnapshot,
)
from app.models.team import Team

# ---------------------------------------------------------------------------
# 공통 상수 및 타입 별칭
# ---------------------------------------------------------------------------

CONTENT_TYPE_JSON = "application/json; charset=utf-8"
CONTENT_TYPE_HTML = "text/html; charset=utf-8"
GAME_EXTERNAL_ID = "20260415LGDOO"

# 파이프라인 주입용 세션 팩토리 타입 별칭
SessionFactory = Callable[[], AbstractContextManager[Session]]


# ---------------------------------------------------------------------------
# 공통 픽스처
# ---------------------------------------------------------------------------


@pytest.fixture
def session() -> Iterator[Session]:
    """전체 스키마가 생성된 인메모리 SQLite 세션."""
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    with SessionLocal() as s:
        yield s
    engine.dispose()


@pytest.fixture
def session_factory(
    session: Session,
) -> Callable[[], AbstractContextManager[Session]]:
    """테스트에서 파이프라인에 주입할 세션 팩토리를 반환한다.

    단일 세션을 재사용하는 컨텍스트 매니저를 흉내낸 팩토리를 반환한다.
    """

    class _ContextSession(AbstractContextManager[Session]):
        """파이프라인이 `with session_factory() as s:` 구문으로 사용하는 컨텍스트 매니저."""

        def __enter__(self) -> Session:
            return session

        def __exit__(self, *args: object) -> None:
            pass

    class _Factory:
        def __call__(self) -> _ContextSession:
            return _ContextSession()

    return _Factory()


# ---------------------------------------------------------------------------
# 도우미: 테스트 데이터 시드
# ---------------------------------------------------------------------------


def _seed_lg_team(session: Session) -> Team:
    team = Team(code="LG", name="LG 트윈스")
    session.add(team)
    session.flush()
    return team


def _seed_opponent_team(session: Session) -> Team:
    team = Team(code="DOO", name="두산 베어스")
    session.add(team)
    session.flush()
    return team


def _seed_game(session: Session, home_team: Team, away_team: Team) -> Game:
    game = Game(
        external_id=GAME_EXTERNAL_ID,
        home_team_id=home_team.id,
        away_team_id=away_team.id,
        game_date=date(2026, 4, 15),
        venue="잠실야구장",
    )
    session.add(game)
    session.flush()
    return game


def _seed_player(
    session: Session, team: Team, external_id: str, name: str, position: str = "CF"
) -> Player:
    player = Player(team_id=team.id, external_id=external_id, name=name, position=position)
    session.add(player)
    session.flush()
    return player


def _seed_ingestion_run(session: Session, source: str = "seed-run") -> IngestionRun:
    run = IngestionRun(source=source, status="completed")
    session.add(run)
    session.flush()
    return run


def _seed_stat_snapshot(
    session: Session, ingestion_run: IngestionRun, player: Player
) -> StatSnapshot:
    snapshot_at = datetime(2026, 4, 15, 9, 0, 0, tzinfo=UTC)
    content_hash = hashlib.sha256(f"stats-{player.id}".encode()).hexdigest()
    snapshot = StatSnapshot(
        ingestion_run_id=ingestion_run.id,
        snapshot_at=snapshot_at,
        content_hash=content_hash,
    )
    session.add(snapshot)
    session.flush()
    row = PlayerStatSnapshotRow(
        snapshot_id=snapshot.id,
        player_id=player.id,
        stats_json={"OPS": 0.800, "OBP": 0.350, "SLG": 0.450},
    )
    session.add(row)
    session.flush()
    return snapshot


def _seed_lineup_snapshot(
    session: Session,
    game: Game,
    team: Team,
    ingestion_run: IngestionRun,
    player: Player,
) -> ActualLineupSnapshot:
    announced_at = datetime(2026, 4, 15, 9, 30, 0, tzinfo=UTC)
    content_hash = hashlib.sha256(f"lineup-{game.id}-{team.id}".encode()).hexdigest()
    snapshot = ActualLineupSnapshot(
        game_id=game.id,
        team_id=team.id,
        ingestion_run_id=ingestion_run.id,
        announced_at=announced_at,
        content_hash=content_hash,
    )
    session.add(snapshot)
    session.flush()
    row = ActualLineupSnapshotRow(
        snapshot_id=snapshot.id,
        player_id=player.id,
        batting_order=1,
        position="CF",
    )
    session.add(row)
    session.flush()
    return snapshot


def _seed_box_score_snapshot(
    session: Session,
    game: Game,
    ingestion_run: IngestionRun,
    player: Player,
) -> BoxScoreSnapshot:
    taken_at = datetime(2026, 4, 15, 22, 0, 0, tzinfo=UTC)
    content_hash = hashlib.sha256(f"box-{game.id}".encode()).hexdigest()
    snapshot = BoxScoreSnapshot(
        game_id=game.id,
        ingestion_run_id=ingestion_run.id,
        taken_at=taken_at,
        content_hash=content_hash,
    )
    session.add(snapshot)
    session.flush()
    row = BoxScoreRow(
        snapshot_id=snapshot.id,
        player_id=player.id,
        at_bats=4,
        hits=2,
        runs=1,
        rbis=1,
        extra_stats_json={},
        innings_pitched=None,
    )
    session.add(row)
    session.flush()
    return snapshot


def _seed_model_version(session: Session) -> ModelVersion:
    mv = ModelVersion(name="scoring-v1", version="1.0.0", model_id="rule-based")
    session.add(mv)
    session.flush()
    return mv


def _seed_completed_eval_run(
    session: Session,
    game: Game,
    team: Team,
    stat_snapshot: StatSnapshot,
    lineup_snapshot: ActualLineupSnapshot,
    model_version: ModelVersion,
) -> LineupEvaluationRun:
    cutoff = datetime(2026, 4, 15, 9, 30, 0, tzinfo=UTC)
    manifest: dict[str, object] = {
        "game_id": game.id,
        "team_id": team.id,
        "evaluation_cutoff_at": cutoff.isoformat(),
        "stat_snapshot_id": stat_snapshot.id,
        "lineup_snapshot_id": lineup_snapshot.id,
        "model_version_id": model_version.id,
    }
    run = LineupEvaluationRun(
        game_id=game.id,
        team_id=team.id,
        model_version_id=model_version.id,
        stat_snapshot_id=stat_snapshot.id,
        lineup_snapshot_id=lineup_snapshot.id,
        evaluation_cutoff_at=cutoff,
        status="completed",
        input_manifest_json=manifest,
        input_hash=hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest(),
        finished_at=datetime(2026, 4, 15, 10, 0, 0, tzinfo=UTC),
    )
    session.add(run)
    session.flush()
    return run


# ---------------------------------------------------------------------------
# Fake HTTP Client
# ---------------------------------------------------------------------------


def _make_mock_http(responses: dict[str, tuple[int, str, str]]) -> HttpClient:
    """canned 응답으로 구성된 MockTransport 기반 HttpClient를 반환한다."""

    def handler(request: httpx.Request) -> httpx.Response:
        url_str = str(request.url)
        for url_key, (status, body, ctype) in responses.items():
            if url_key in url_str:
                return httpx.Response(status, text=body, headers={"content-type": ctype})
        return httpx.Response(404, text="not found")

    transport = httpx.MockTransport(handler)
    inner = httpx.Client(transport=transport)
    return HttpClient(client=inner, retry_backoff=(0.0,))


def _html_body(text: str = "page") -> tuple[int, str, str]:
    return (200, f"<html>{text}</html>", CONTENT_TYPE_HTML)


# ---------------------------------------------------------------------------
# daily_pipeline 테스트
# ---------------------------------------------------------------------------


def test_daily_pipeline_happy_path(session: Session, session_factory: SessionFactory) -> None:
    """모든 컬렉터 성공 시 IngestionRun.status가 'completed'이어야 한다."""
    http = _make_mock_http(
        {
            "Schedule": _html_body("schedule"),
            "Player/Search": _html_body("roster"),
            "statiz.sporki.com/team": _html_body("stats"),
        }
    )

    result = run_daily_pipeline(
        target_date=date(2026, 5, 25),
        session_factory=session_factory,
        http=http,
    )

    assert result.status == "completed"
    assert result.ingestion_run_id > 0

    run = session.get(IngestionRun, result.ingestion_run_id)
    assert run is not None
    assert run.status == "completed"
    assert run.finished_at is not None


def test_daily_pipeline_is_idempotent(session: Session, session_factory: SessionFactory) -> None:
    """동일 날짜 재실행 시 기존 run_id를 반환하고 새 수집을 하지 않아야 한다."""
    http = _make_mock_http(
        {
            "Schedule": _html_body("schedule"),
            "Player/Search": _html_body("roster"),
            "statiz.sporki.com/team": _html_body("stats"),
        }
    )

    result1 = run_daily_pipeline(
        target_date=date(2026, 5, 25),
        session_factory=session_factory,
        http=http,
    )
    result2 = run_daily_pipeline(
        target_date=date(2026, 5, 25),
        session_factory=session_factory,
        http=http,
    )

    assert result1.ingestion_run_id == result2.ingestion_run_id
    assert result2.status == "completed"
    assert not result2.schedule_created
    assert not result2.roster_created
    assert not result2.season_stats_created
    assert result2.recent_stats_payloads_created == 0


def test_daily_pipeline_marks_failed_on_exception(
    session: Session, session_factory: SessionFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """컬렉터 예외 발생 시 IngestionRun.status가 'failed'이고 error_message가 설정되어야 한다."""

    def _raise(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("schedule fetch failed")

    monkeypatch.setattr(
        "app.jobs.daily_pipeline.collect_lg_schedule",
        _raise,
    )

    http = _make_mock_http({})
    result = run_daily_pipeline(
        target_date=date(2026, 5, 25),
        session_factory=session_factory,
        http=http,
    )

    assert result.status == "failed"
    assert result.error_message is not None
    assert "RuntimeError" in result.error_message

    run = session.get(IngestionRun, result.ingestion_run_id)
    assert run is not None
    assert run.status == "failed"
    assert run.error_message is not None
    assert run.finished_at is not None


# ---------------------------------------------------------------------------
# pregame_pipeline 테스트
# ---------------------------------------------------------------------------


def test_pregame_pipeline_waiting(
    session: Session, session_factory: SessionFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """라인업 미발표 시 status='failed', error_message='lineup not announced'이어야 한다."""

    def _waiting(*args: Any, **kwargs: Any) -> LineupCollectionResult:
        return LineupCollectionResult(
            status=LineupStatus.WAITING,
            raw_payload=None,
            fetched_at=datetime.now(UTC),
            announced_at=None,
            created=False,
        )

    monkeypatch.setattr("app.jobs.pregame_pipeline.collect_lg_lineup", _waiting)

    http = _make_mock_http({})
    result = run_pregame_pipeline(
        game_id=GAME_EXTERNAL_ID,
        session_factory=session_factory,
        http=http,
    )

    assert result.status == "failed"
    assert result.lineup_status == "waiting"
    assert result.error_message == "lineup not announced"

    run = session.get(IngestionRun, result.ingestion_run_id)
    assert run is not None
    assert run.status == "failed"
    assert run.error_message == "lineup not announced"


def test_pregame_pipeline_collected_runs_eval(
    session: Session, session_factory: SessionFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """라인업 수집 성공 시 평가 실행이 생성되고 status='completed'이어야 한다."""
    lg_team = _seed_lg_team(session)
    opp_team = _seed_opponent_team(session)
    game = _seed_game(session, home_team=lg_team, away_team=opp_team)
    player = _seed_player(session, lg_team, "LG-P001", "홍길동")
    ingestion_run = _seed_ingestion_run(session, source="seed-for-pregame")
    _seed_stat_snapshot(session, ingestion_run, player)
    session.commit()

    fetch_time = datetime(2026, 4, 15, 17, 30, 0, tzinfo=UTC)

    # ActualLineupSnapshot을 미리 시드하여 normalize mock이 반환할 수 있도록 준비
    announced_at = datetime(2026, 4, 15, 9, 30, 0, tzinfo=UTC)
    content_hash = hashlib.sha256(f"lineup-{game.id}-{lg_team.id}".encode()).hexdigest()
    lineup_snapshot = ActualLineupSnapshot(
        game_id=game.id,
        team_id=lg_team.id,
        ingestion_run_id=ingestion_run.id,
        announced_at=announced_at,
        content_hash=content_hash,
    )
    session.add(lineup_snapshot)
    session.flush()
    lineup_row = ActualLineupSnapshotRow(
        snapshot_id=lineup_snapshot.id,
        player_id=player.id,
        batting_order=1,
        position="CF",
    )
    session.add(lineup_row)
    session.flush()

    # RawIngestionPayload mock (collect_lg_lineup 반환값)
    fake_payload = MagicMock(spec=RawIngestionPayload)
    fake_payload.ingestion_run_id = ingestion_run.id

    def _collected(*args: Any, **kwargs: Any) -> LineupCollectionResult:
        return LineupCollectionResult(
            status=LineupStatus.COLLECTED,
            raw_payload=fake_payload,
            fetched_at=fetch_time,
            announced_at=fetch_time,
            created=True,
        )

    snapshot_id = lineup_snapshot.id

    def _normalize(*args: Any, **kwargs: Any) -> LineupNormalizeResult:
        return LineupNormalizeResult(
            snapshot_id=snapshot_id,
            rows_created=1,
            rows_skipped=0,
            needs_review_reasons=(),
        )

    # evaluate_lineup_for_run은 선수 9명이 필요하여 1명 픽스처에서 실패하므로 mock 처리
    def _fake_evaluate(
        session_arg: Session, *, run: LineupEvaluationRun, **kwargs: Any
    ) -> LineupEvaluationRun:
        run.status = "completed"
        run.finished_at = datetime(2026, 4, 15, 10, 0, 0, tzinfo=UTC)
        session_arg.flush()
        return run

    monkeypatch.setattr("app.jobs.pregame_pipeline.collect_lg_lineup", _collected)
    monkeypatch.setattr("app.jobs.pregame_pipeline.normalize_lineup", _normalize)
    monkeypatch.setattr("app.jobs.pregame_pipeline.evaluate_lineup_for_run", _fake_evaluate)

    http = _make_mock_http({})
    result = run_pregame_pipeline(
        game_id=GAME_EXTERNAL_ID,
        session_factory=session_factory,
        http=http,
    )

    assert result.status == "completed"
    assert result.lineup_status == "collected"
    assert result.evaluation_run_id is not None

    eval_run = session.get(LineupEvaluationRun, result.evaluation_run_id)
    assert eval_run is not None
    assert eval_run.status == "completed"


def test_pregame_pipeline_is_idempotent(
    session: Session, session_factory: SessionFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """동일 game_id 재실행 시 기존 run_id를 반환해야 한다."""

    def _waiting(*args: Any, **kwargs: Any) -> LineupCollectionResult:
        return LineupCollectionResult(
            status=LineupStatus.WAITING,
            raw_payload=None,
            fetched_at=datetime.now(UTC),
            announced_at=None,
            created=False,
        )

    monkeypatch.setattr("app.jobs.pregame_pipeline.collect_lg_lineup", _waiting)

    http = _make_mock_http({})
    result1 = run_pregame_pipeline(
        game_id=GAME_EXTERNAL_ID,
        session_factory=session_factory,
        http=http,
    )
    # 두 번째 실행을 위해 completed로 상태를 변경
    run = session.get(IngestionRun, result1.ingestion_run_id)
    assert run is not None
    run.status = "completed"
    session.commit()

    result2 = run_pregame_pipeline(
        game_id=GAME_EXTERNAL_ID,
        session_factory=session_factory,
        http=http,
    )

    assert result1.ingestion_run_id == result2.ingestion_run_id
    assert result2.status == "completed"
    assert result2.lineup_status == "skipped_existing"


# ---------------------------------------------------------------------------
# postgame_pipeline 테스트
# ---------------------------------------------------------------------------


def test_postgame_pipeline_waiting(
    session: Session, session_factory: SessionFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """박스스코어가 아직 final이 아닌 경우 status='failed'이어야 한다."""

    def _waiting(*args: Any, **kwargs: Any) -> BoxScoreCollectionResult:
        return BoxScoreCollectionResult(
            status=BoxScoreStatus.WAITING,
            raw_payload=None,
            fetched_at=datetime.now(UTC),
            final_score=None,
            created=False,
        )

    monkeypatch.setattr("app.jobs.postgame_pipeline.collect_lg_box_score", _waiting)

    http = _make_mock_http({})
    result = run_postgame_pipeline(
        game_id=GAME_EXTERNAL_ID,
        session_factory=session_factory,
        http=http,
    )

    assert result.status == "failed"
    assert result.box_score_status == "waiting"
    assert result.error_message == "box score not final"

    run = session.get(IngestionRun, result.ingestion_run_id)
    assert run is not None
    assert run.status == "failed"


def test_postgame_pipeline_collected_runs_review(
    session: Session, session_factory: SessionFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """박스스코어 수집 성공 시 포스트게임 리뷰가 생성되어야 한다."""
    lg_team = _seed_lg_team(session)
    opp_team = _seed_opponent_team(session)
    game = _seed_game(session, home_team=lg_team, away_team=opp_team)
    player = _seed_player(session, lg_team, "LG-P001", "홍길동")
    ingestion_run = _seed_ingestion_run(session, source="seed-for-postgame")
    stat_snapshot = _seed_stat_snapshot(session, ingestion_run, player)
    lineup_snapshot = _seed_lineup_snapshot(session, game, lg_team, ingestion_run, player)
    model_version = _seed_model_version(session)
    _seed_completed_eval_run(session, game, lg_team, stat_snapshot, lineup_snapshot, model_version)
    session.commit()

    box_snapshot = _seed_box_score_snapshot(session, game, ingestion_run, player)
    session.commit()

    fake_payload = MagicMock(spec=RawIngestionPayload)
    fake_payload.ingestion_run_id = ingestion_run.id

    from app.ingestion.collectors.box_score import FinalScore

    box_snapshot_id = box_snapshot.id

    def _collected(*args: Any, **kwargs: Any) -> BoxScoreCollectionResult:
        return BoxScoreCollectionResult(
            status=BoxScoreStatus.COLLECTED,
            raw_payload=fake_payload,
            fetched_at=datetime.now(UTC),
            final_score=FinalScore(home_runs=5, away_runs=3),
            created=True,
        )

    def _normalize(*args: Any, **kwargs: Any) -> BoxScoreNormalizeResult:
        return BoxScoreNormalizeResult(
            snapshot_id=box_snapshot_id,
            rows_created=1,
            rows_skipped=0,
            skipped_not_final=False,
            needs_review_reasons=(),
        )

    monkeypatch.setattr("app.jobs.postgame_pipeline.collect_lg_box_score", _collected)
    monkeypatch.setattr("app.jobs.postgame_pipeline.normalize_box_score", _normalize)

    http = _make_mock_http({})
    result = run_postgame_pipeline(
        game_id=GAME_EXTERNAL_ID,
        session_factory=session_factory,
        http=http,
    )

    assert result.status == "completed"
    assert result.box_score_status == "collected"
    assert result.postgame_review_run_id is not None


def test_postgame_pipeline_is_idempotent(
    session: Session, session_factory: SessionFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """동일 game_id 재실행 시 기존 run_id를 반환해야 한다."""

    def _waiting(*args: Any, **kwargs: Any) -> BoxScoreCollectionResult:
        return BoxScoreCollectionResult(
            status=BoxScoreStatus.WAITING,
            raw_payload=None,
            fetched_at=datetime.now(UTC),
            final_score=None,
            created=False,
        )

    monkeypatch.setattr("app.jobs.postgame_pipeline.collect_lg_box_score", _waiting)

    http = _make_mock_http({})
    result1 = run_postgame_pipeline(
        game_id=GAME_EXTERNAL_ID,
        session_factory=session_factory,
        http=http,
    )
    run = session.get(IngestionRun, result1.ingestion_run_id)
    assert run is not None
    run.status = "completed"
    session.commit()

    result2 = run_postgame_pipeline(
        game_id=GAME_EXTERNAL_ID,
        session_factory=session_factory,
        http=http,
    )

    assert result1.ingestion_run_id == result2.ingestion_run_id
    assert result2.status == "completed"
    assert result2.box_score_status == "skipped_existing"


# ---------------------------------------------------------------------------
# CLI 테스트
# ---------------------------------------------------------------------------


def test_cli_ingest_daily_invokes_pipeline(monkeypatch: pytest.MonkeyPatch) -> None:
    """CLI ingest-daily 명령어가 run_daily_pipeline을 올바른 인자로 호출해야 한다."""
    called_with: dict[str, object] = {}

    def _fake_pipeline(**kwargs: Any) -> DailyPipelineResult:
        called_with.update(kwargs)
        return DailyPipelineResult(
            ingestion_run_id=42,
            status="completed",
            schedule_created=True,
            roster_created=True,
            season_stats_created=True,
            recent_stats_payloads_created=2,
        )

    monkeypatch.setattr("app.cli.run_daily_pipeline", _fake_pipeline)

    runner = CliRunner()
    result = runner.invoke(cli_app, ["ingest-daily", "--date", "2026-05-25"])

    assert result.exit_code == 0
    assert "daily pipeline run 42" in result.output
    assert "completed" in result.output
    target = called_with.get("target_date")
    assert target == date(2026, 5, 25)


def test_cli_ingest_pregame_invokes_pipeline(monkeypatch: pytest.MonkeyPatch) -> None:
    """CLI ingest-pregame 명령어가 run_pregame_pipeline을 올바른 인자로 호출해야 한다."""
    called_with: dict[str, object] = {}

    def _fake_pipeline(**kwargs: Any) -> PregamePipelineResult:
        called_with.update(kwargs)
        return PregamePipelineResult(
            ingestion_run_id=10,
            status="completed",
            lineup_status="collected",
            evaluation_run_id=5,
        )

    monkeypatch.setattr("app.cli.run_pregame_pipeline", _fake_pipeline)

    runner = CliRunner()
    result = runner.invoke(cli_app, ["ingest-pregame", "--game-id", GAME_EXTERNAL_ID])

    assert result.exit_code == 0
    assert "pregame pipeline run 10" in result.output
    assert called_with.get("game_id") == GAME_EXTERNAL_ID


def test_cli_ingest_postgame_invokes_pipeline(monkeypatch: pytest.MonkeyPatch) -> None:
    """CLI ingest-postgame 명령어가 run_postgame_pipeline을 올바른 인자로 호출해야 한다."""
    called_with: dict[str, object] = {}

    def _fake_pipeline(**kwargs: Any) -> PostgamePipelineResult:
        called_with.update(kwargs)
        return PostgamePipelineResult(
            ingestion_run_id=20,
            status="completed",
            box_score_status="collected",
            postgame_review_run_id=7,
        )

    monkeypatch.setattr("app.cli.run_postgame_pipeline", _fake_pipeline)

    runner = CliRunner()
    result = runner.invoke(cli_app, ["ingest-postgame", "--game-id", GAME_EXTERNAL_ID])

    assert result.exit_code == 0
    assert "postgame pipeline run 20" in result.output
    assert called_with.get("game_id") == GAME_EXTERNAL_ID


def test_daily_pipeline_preserves_started_at_on_retry(
    session: Session,
    session_factory: SessionFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """크래시 후 재실행 시 ``started_at``이 최초 시점을 유지해야 한다."""
    original_started = datetime(2026, 4, 15, 1, 0, 0, tzinfo=UTC)
    source = "pipeline:ingest-daily:2026-05-25"
    pre_existing = IngestionRun(
        source=source,
        status="running",
        started_at=original_started,
    )
    session.add(pre_existing)
    session.commit()

    http = _make_mock_http(
        {
            "Schedule": _html_body("schedule"),
            "Player/Search": _html_body("roster"),
            "statiz.sporki.com/team": _html_body("stats"),
        }
    )

    result = run_daily_pipeline(
        target_date=date(2026, 5, 25),
        session_factory=session_factory,
        http=http,
    )

    assert result.status == "completed"
    run = session.get(IngestionRun, result.ingestion_run_id)
    assert run is not None
    # SQLite는 tz 정보를 보존하지 않으므로 naive datetime을 반환한다; UTC 가정하에 비교
    assert run.started_at is not None
    stored_naive = (
        run.started_at.replace(tzinfo=UTC) if run.started_at.tzinfo is None else run.started_at
    )
    assert stored_naive == original_started, (
        "started_at이 최초 실행 시점을 유지해야 함 (감사 로그 보존)"
    )


def test_kbo_lab_script_is_installed() -> None:
    """`[project.scripts]` 엔트리 포인트가 실제 실행 파일로 설치되어야 한다.

    `[build-system]`이 없으면 uv sync가 console script를 생성하지 않아 CLI가 운영
    환경에서 동작하지 않는다. 이 테스트는 회귀 방지용 스모크 테스트다.
    """
    import shutil

    assert shutil.which("kbo-lab") is not None, (
        "kbo-lab script not installed; check [build-system] in apps/api/pyproject.toml"
    )
