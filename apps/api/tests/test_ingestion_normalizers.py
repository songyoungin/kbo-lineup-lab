"""Ingestion normalizer 및 player matcher 테스트.

검증 항목:
- player_matcher: external_id 매칭; (team, name) 폴백 → needs_review;
  ambiguous → needs_review; not_found
- schedule: JSON 파싱 → Game 생성; 멱등성; HTML → NotImplementedError
- roster: Player 행 생성; 멱등성; HTML → NotImplementedError
- player_stats: StatSnapshot + 행 생성; content_hash 멱등성; needs_review 이유 노출
- lineup: team_code 기준 홈/어웨이 선택; 멱등성 (natural key)
- box_score: gameStatus≠FINAL 페이로드 건너뜀; FINAL 페이로드 정규화
- 불변 조건: 모든 스냅샷 행이 raw 페이로드의 ingestion_run_id를 참조
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

import app.models  # noqa: F401 — Base.metadata에 모든 모델 등록
from app.db.base import Base
from app.ingestion.normalizers.box_score import normalize_box_score
from app.ingestion.normalizers.lineup import normalize_lineup
from app.ingestion.normalizers.player_stats import normalize_player_stats
from app.ingestion.normalizers.roster import normalize_roster
from app.ingestion.normalizers.schedule import normalize_schedule
from app.ingestion.player_matcher import MatchStatus, PlayerMatch, match_player
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
# 공통 픽스처
# ---------------------------------------------------------------------------

CONTENT_TYPE_JSON = "application/json; charset=utf-8"
CONTENT_TYPE_HTML = "text/html; charset=utf-8"


@pytest.fixture
def session() -> Iterator[Session]:
    """전체 스키마가 생성된 인메모리 SQLite 세션."""
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as s:
        yield s
    engine.dispose()


@pytest.fixture
def ingestion_run(session: Session) -> IngestionRun:
    """테스트용 최소 IngestionRun 행."""
    run = IngestionRun(source="test-normalizer", status="running")
    session.add(run)
    session.flush()
    return run


def _seed_team(session: Session, code: str, name: str) -> Team:
    """팀을 삽입하고 반환한다."""
    team = Team(code=code, name=name)
    session.add(team)
    session.flush()
    return team


def _seed_player(
    session: Session, team: Team, external_id: str, name: str, position: str = "CF"
) -> Player:
    """선수를 삽입하고 반환한다."""
    player = Player(
        team_id=team.id,
        external_id=external_id,
        name=name,
        position=position,
    )
    session.add(player)
    session.flush()
    return player


def _seed_game(
    session: Session,
    home_team: Team,
    away_team: Team,
    external_id: str = "20260415LGDOO",
) -> Game:
    """경기를 삽입하고 반환한다."""
    from datetime import date

    game = Game(
        external_id=external_id,
        home_team_id=home_team.id,
        away_team_id=away_team.id,
        game_date=date(2026, 4, 15),
        venue="잠실야구장",
    )
    session.add(game)
    session.flush()
    return game


def _make_raw_payload(
    session: Session,
    ingestion_run: IngestionRun,
    raw_body: str,
    category: str = "schedule",
    content_type: str = CONTENT_TYPE_JSON,
    source_url: str = "https://kbo.or.kr/test",
) -> RawIngestionPayload:
    """테스트용 RawIngestionPayload를 생성하고 반환한다."""
    import hashlib

    row = RawIngestionPayload(
        ingestion_run_id=ingestion_run.id,
        category=category,
        source_name="test_source",
        source_url=source_url,
        fetched_at=datetime(2026, 4, 15, 10, 0, 0, tzinfo=UTC),
        content_type=content_type,
        payload_hash=hashlib.sha256(raw_body.encode()).hexdigest(),
        raw_body=raw_body,
    )
    session.add(row)
    session.flush()
    return row


# ---------------------------------------------------------------------------
# player_matcher 테스트
# ---------------------------------------------------------------------------


def test_match_player_by_external_id(session: Session) -> None:
    """external_id가 일치하는 선수가 있으면 MATCHED를 반환해야 한다."""
    team = _seed_team(session, "LG", "LG 트윈스")
    player = _seed_player(session, team, "LG-P001", "홍길동")

    result = match_player(session, team_code="LG", external_id="LG-P001", name=None)

    assert result.status == MatchStatus.MATCHED
    assert result.player_id == player.id
    assert result.reason == ""
    assert result.is_matched is True


def test_match_player_fallback_name_needs_review(session: Session) -> None:
    """external_id가 없을 때 (team_code, name) 폴백 매칭이 NEEDS_REVIEW를 반환해야 한다."""
    team = _seed_team(session, "LG", "LG 트윈스")
    player = _seed_player(session, team, "LG-P001", "홍길동")

    result = match_player(session, team_code="LG", external_id=None, name="홍길동")

    assert result.status == MatchStatus.NEEDS_REVIEW
    assert result.player_id == player.id
    assert "fallback match" in result.reason
    assert result.is_matched is False


def test_match_player_ambiguous_needs_review(session: Session) -> None:
    """같은 이름의 선수가 여럿이면 NEEDS_REVIEW(ambiguous)를 반환해야 한다."""
    team = _seed_team(session, "LG", "LG 트윈스")
    _seed_player(session, team, "LG-P001", "홍길동")
    _seed_player(session, team, "LG-P002", "홍길동")

    result = match_player(session, team_code="LG", external_id=None, name="홍길동")

    assert result.status == MatchStatus.NEEDS_REVIEW
    assert result.player_id is None
    assert "ambiguous" in result.reason
    assert "2 matches" in result.reason


def test_match_player_external_id_on_different_team_needs_review(session: Session) -> None:
    """external_id 매칭된 선수의 team_id가 요청된 team_code와 다르면 NEEDS_REVIEW를 반환해야 한다.

    트레이드되었거나 소스의 external_id가 팀 간 모호한 경우를 방지한다.
    """
    lg = _seed_team(session, "LG", "LG 트윈스")
    doo = _seed_team(session, "DOO", "두산 베어스")
    # external_id LG-P001을 가진 선수가 DOO 팀에 등록되어 있음 (트레이드 시뮬레이션)
    moved_player = _seed_player(session, doo, "LG-P001", "홍길동")
    # LG 팀에는 다른 선수
    _seed_player(session, lg, "LG-P999", "이순신")

    result = match_player(session, team_code="LG", external_id="LG-P001", name=None)

    assert result.status == MatchStatus.NEEDS_REVIEW
    assert result.player_id == moved_player.id
    assert "different team" in result.reason


def test_match_player_external_id_matches_when_team_aligns(session: Session) -> None:
    """external_id 매칭과 team_code가 일치하면 정상적으로 MATCHED를 반환해야 한다."""
    lg = _seed_team(session, "LG", "LG 트윈스")
    player = _seed_player(session, lg, "LG-P001", "홍길동")

    result = match_player(session, team_code="LG", external_id="LG-P001", name=None)

    assert result.status == MatchStatus.MATCHED
    assert result.player_id == player.id


def test_match_player_not_found_when_no_match(session: Session) -> None:
    """매칭 결과가 없으면 NOT_FOUND를 반환해야 한다."""
    _seed_team(session, "LG", "LG 트윈스")

    result = match_player(session, team_code="LG", external_id=None, name="없는선수")

    assert result.status == MatchStatus.NOT_FOUND
    assert result.player_id is None


def test_match_player_not_found_unknown_team(session: Session) -> None:
    """알 수 없는 팀 코드이면 NOT_FOUND에 팀 설명이 포함되어야 한다."""
    result = match_player(session, team_code="GHOST", external_id=None, name="홍길동")

    assert result.status == MatchStatus.NOT_FOUND
    assert "unknown team_code" in result.reason


def test_match_player_not_found_no_id_and_no_name(session: Session) -> None:
    """external_id도 name도 없으면 NOT_FOUND를 반환해야 한다."""
    result = match_player(session, team_code="LG", external_id=None, name=None)

    assert result.status == MatchStatus.NOT_FOUND


def test_player_match_is_matched_property(session: Session) -> None:
    """PlayerMatch.is_matched 속성이 MATCHED 상태에서만 True여야 한다."""
    matched = PlayerMatch(status=MatchStatus.MATCHED, player_id=1, reason="")
    needs_review = PlayerMatch(status=MatchStatus.NEEDS_REVIEW, player_id=1, reason="r")
    not_found = PlayerMatch(status=MatchStatus.NOT_FOUND, player_id=None, reason="r")

    assert matched.is_matched is True
    assert needs_review.is_matched is False
    assert not_found.is_matched is False


# ---------------------------------------------------------------------------
# schedule normalizer 테스트
# ---------------------------------------------------------------------------


def test_normalize_schedule_creates_games(session: Session, ingestion_run: IngestionRun) -> None:
    """JSON 일정 페이로드를 파싱하여 Game 행을 생성해야 한다."""
    lg = _seed_team(session, "LG", "LG 트윈스")
    doo = _seed_team(session, "DOO", "두산 베어스")
    body = json.dumps(
        {
            "games": [
                {
                    "external_id": "20260415LGDOO",
                    "game_date": "2026-04-15",
                    "home_team_code": "LG",
                    "away_team_code": "DOO",
                    "venue": "잠실야구장",
                }
            ]
        }
    )
    raw = _make_raw_payload(session, ingestion_run, body, category="schedule")

    result = normalize_schedule(session, raw)

    assert result.games_created == 1
    assert result.games_existing == 0
    game = session.execute(select(Game).where(Game.external_id == "20260415LGDOO")).scalar_one()
    assert game.home_team_id == lg.id
    assert game.away_team_id == doo.id
    assert str(game.game_date) == "2026-04-15"
    assert game.venue == "잠실야구장"


def test_normalize_schedule_is_idempotent(session: Session, ingestion_run: IngestionRun) -> None:
    """같은 페이로드를 두 번 정규화해도 Game 행이 중복 생성되지 않아야 한다."""
    _seed_team(session, "LG", "LG 트윈스")
    _seed_team(session, "DOO", "두산 베어스")
    body = json.dumps(
        {
            "games": [
                {
                    "external_id": "20260415LGDOO",
                    "game_date": "2026-04-15",
                    "home_team_code": "LG",
                    "away_team_code": "DOO",
                    "venue": "잠실야구장",
                }
            ]
        }
    )
    raw = _make_raw_payload(session, ingestion_run, body, category="schedule")

    result1 = normalize_schedule(session, raw)
    result2 = normalize_schedule(session, raw)

    assert result1.games_created == 1
    assert result2.games_created == 0
    assert result2.games_existing == 1
    count = session.execute(select(Game)).scalars().all()
    assert len(count) == 1


def test_normalize_schedule_html_raises_not_implemented(
    session: Session, ingestion_run: IngestionRun
) -> None:
    """HTML content_type이면 NotImplementedError를 발생시켜야 한다."""
    raw = _make_raw_payload(
        session,
        ingestion_run,
        "<html>schedule</html>",
        category="schedule",
        content_type=CONTENT_TYPE_HTML,
    )

    with pytest.raises(NotImplementedError, match="HTML"):
        normalize_schedule(session, raw)


# ---------------------------------------------------------------------------
# roster normalizer 테스트
# ---------------------------------------------------------------------------


def test_normalize_roster_creates_players(session: Session, ingestion_run: IngestionRun) -> None:
    """JSON 로스터 페이로드를 파싱하여 Player 행을 생성해야 한다."""
    team = _seed_team(session, "LG", "LG 트윈스")
    body = json.dumps(
        {
            "team_code": "LG",
            "players": [
                {"external_id": "LG-P001", "name": "홍길동", "position": "CF"},
                {"external_id": "LG-P002", "name": "이순신", "position": "SS"},
            ],
        }
    )
    raw = _make_raw_payload(session, ingestion_run, body, category="roster")

    result = normalize_roster(session, raw)

    assert result.players_created == 2
    assert result.players_existing == 0
    players = session.execute(select(Player).where(Player.team_id == team.id)).scalars().all()
    assert len(players) == 2


def test_normalize_roster_is_idempotent(session: Session, ingestion_run: IngestionRun) -> None:
    """같은 페이로드를 두 번 정규화해도 Player 행이 중복 생성되지 않아야 한다."""
    _seed_team(session, "LG", "LG 트윈스")
    body = json.dumps(
        {
            "team_code": "LG",
            "players": [{"external_id": "LG-P001", "name": "홍길동", "position": "CF"}],
        }
    )
    raw = _make_raw_payload(session, ingestion_run, body, category="roster")

    result1 = normalize_roster(session, raw)
    result2 = normalize_roster(session, raw)

    assert result1.players_created == 1
    assert result2.players_created == 0
    assert result2.players_existing == 1
    count = session.execute(select(Player)).scalars().all()
    assert len(count) == 1


def test_normalize_roster_html_raises_not_implemented(
    session: Session, ingestion_run: IngestionRun
) -> None:
    """HTML content_type이면 NotImplementedError를 발생시켜야 한다."""
    raw = _make_raw_payload(
        session,
        ingestion_run,
        "<html>roster</html>",
        category="roster",
        content_type=CONTENT_TYPE_HTML,
    )

    with pytest.raises(NotImplementedError, match="HTML"):
        normalize_roster(session, raw)


# ---------------------------------------------------------------------------
# player_stats normalizer 테스트
# ---------------------------------------------------------------------------


def test_normalize_player_stats_creates_snapshot_and_rows(
    session: Session, ingestion_run: IngestionRun
) -> None:
    """StatSnapshot과 PlayerStatSnapshotRow가 생성되어야 한다."""
    team = _seed_team(session, "LG", "LG 트윈스")
    player = _seed_player(session, team, "LG-P001", "홍길동")
    body = json.dumps(
        {
            "team_code": "LG",
            "snapshot_at": "2026-04-15T16:00:00+09:00",
            "rows": [
                {
                    "player_external_id": "LG-P001",
                    "stats": {"OPS": 0.880, "OBP": 0.380, "SLG": 0.500},
                }
            ],
        }
    )
    raw = _make_raw_payload(session, ingestion_run, body, category="player_stats")

    result = normalize_player_stats(session, raw)

    assert result.rows_created == 1
    assert result.rows_skipped == 0
    snapshot = session.get(StatSnapshot, result.snapshot_id)
    assert snapshot is not None
    assert snapshot.ingestion_run_id == ingestion_run.id
    stat_row = session.execute(
        select(PlayerStatSnapshotRow).where(
            PlayerStatSnapshotRow.snapshot_id == result.snapshot_id,
            PlayerStatSnapshotRow.player_id == player.id,
        )
    ).scalar_one()
    assert stat_row.stats_json["OPS"] == pytest.approx(0.880)


def test_normalize_player_stats_idempotent_content_hash(
    session: Session, ingestion_run: IngestionRun
) -> None:
    """같은 페이로드를 두 번 정규화해도 StatSnapshot이 중복 생성되지 않아야 한다."""
    _seed_team(session, "LG", "LG 트윈스")
    _seed_player(
        session,
        session.execute(select(Team).where(Team.code == "LG")).scalar_one(),
        "LG-P001",
        "홍길동",
    )

    body = json.dumps(
        {
            "team_code": "LG",
            "snapshot_at": "2026-04-15T16:00:00+09:00",
            "rows": [{"player_external_id": "LG-P001", "stats": {"OPS": 0.880}}],
        }
    )
    raw = _make_raw_payload(session, ingestion_run, body, category="player_stats")

    result1 = normalize_player_stats(session, raw)
    result2 = normalize_player_stats(session, raw)

    assert result1.snapshot_id == result2.snapshot_id
    snapshots = session.execute(select(StatSnapshot)).scalars().all()
    assert len(snapshots) == 1


def test_normalize_player_stats_needs_review_reason_surfaced(
    session: Session, ingestion_run: IngestionRun
) -> None:
    """알 수 없는 선수는 건너뛰고 needs_review_reasons에 이유가 포함되어야 한다."""
    _seed_team(session, "LG", "LG 트윈스")
    body = json.dumps(
        {
            "team_code": "LG",
            "snapshot_at": "2026-04-15T16:00:00+09:00",
            "rows": [
                {"player_external_id": "LG-GHOST", "stats": {"OPS": 0.700}},
            ],
        }
    )
    raw = _make_raw_payload(session, ingestion_run, body, category="player_stats")

    result = normalize_player_stats(session, raw)

    assert result.rows_created == 0
    assert result.rows_skipped == 1
    assert len(result.needs_review_reasons) > 0


def test_normalize_player_stats_snapshot_references_ingestion_run(
    session: Session, ingestion_run: IngestionRun
) -> None:
    """StatSnapshot.ingestion_run_id가 raw 페이로드의 ingestion_run_id와 일치해야 한다."""
    _seed_team(session, "LG", "LG 트윈스")
    _seed_player(
        session,
        session.execute(select(Team).where(Team.code == "LG")).scalar_one(),
        "LG-P001",
        "홍길동",
    )

    body = json.dumps(
        {
            "team_code": "LG",
            "snapshot_at": "2026-04-15T16:00:00+09:00",
            "rows": [{"player_external_id": "LG-P001", "stats": {}}],
        }
    )
    raw = _make_raw_payload(session, ingestion_run, body, category="player_stats")

    result = normalize_player_stats(session, raw)

    snapshot = session.get(StatSnapshot, result.snapshot_id)
    assert snapshot is not None
    assert snapshot.ingestion_run_id == raw.ingestion_run_id


def test_normalize_player_stats_html_raises_not_implemented(
    session: Session, ingestion_run: IngestionRun
) -> None:
    """HTML content_type이면 NotImplementedError를 발생시켜야 한다."""
    raw = _make_raw_payload(
        session,
        ingestion_run,
        "<html>stats</html>",
        category="player_stats",
        content_type=CONTENT_TYPE_HTML,
    )

    with pytest.raises(NotImplementedError, match="HTML"):
        normalize_player_stats(session, raw)


# ---------------------------------------------------------------------------
# lineup normalizer 테스트
# ---------------------------------------------------------------------------


def test_normalize_lineup_lg_home(session: Session, ingestion_run: IngestionRun) -> None:
    """LG가 홈팀일 때 homeLineup 배열을 선택해야 한다."""
    lg = _seed_team(session, "LG", "LG 트윈스")
    doo = _seed_team(session, "DOO", "두산 베어스")
    game = _seed_game(session, home_team=lg, away_team=doo)
    player = _seed_player(session, lg, "LG-B001", "홍길동", "CF")

    body = json.dumps(
        {
            "game_external_id": game.external_id,
            "team_code": "LG",
            "announced_at": "2026-04-15T17:30:00+09:00",
            "homeLineup": [{"player_external_id": "LG-B001", "batting_order": 1, "position": "CF"}],
            "awayLineup": [],
        }
    )
    raw = _make_raw_payload(session, ingestion_run, body, category="lineup")

    result = normalize_lineup(session, raw)

    assert result.rows_created == 1
    assert result.rows_skipped == 0
    row = session.execute(
        select(ActualLineupSnapshotRow).where(
            ActualLineupSnapshotRow.snapshot_id == result.snapshot_id,
            ActualLineupSnapshotRow.player_id == player.id,
        )
    ).scalar_one()
    assert row.batting_order == 1
    assert row.position == "CF"


def test_normalize_lineup_lg_away(session: Session, ingestion_run: IngestionRun) -> None:
    """LG가 어웨이팀일 때 awayLineup 배열을 선택해야 한다."""
    lg = _seed_team(session, "LG", "LG 트윈스")
    doo = _seed_team(session, "DOO", "두산 베어스")
    # DOO가 홈, LG가 어웨이
    game = _seed_game(session, home_team=doo, away_team=lg, external_id="20260415DOОЛG")
    _seed_player(session, lg, "LG-B001", "홍길동", "CF")

    body = json.dumps(
        {
            "game_external_id": game.external_id,
            "team_code": "LG",
            "announced_at": "2026-04-15T17:30:00+09:00",
            "homeLineup": [],
            "awayLineup": [{"player_external_id": "LG-B001", "batting_order": 1, "position": "CF"}],
        }
    )
    raw = _make_raw_payload(session, ingestion_run, body, category="lineup")

    result = normalize_lineup(session, raw)

    assert result.rows_created == 1
    assert result.rows_skipped == 0


def test_normalize_lineup_is_idempotent(session: Session, ingestion_run: IngestionRun) -> None:
    """같은 (game_id, team_id, announced_at) 키로 두 번 정규화해도 스냅샷이 중복되지 않아야 한다."""
    lg = _seed_team(session, "LG", "LG 트윈스")
    doo = _seed_team(session, "DOO", "두산 베어스")
    game = _seed_game(session, home_team=lg, away_team=doo)
    _seed_player(session, lg, "LG-B001", "홍길동", "CF")

    body = json.dumps(
        {
            "game_external_id": game.external_id,
            "team_code": "LG",
            "announced_at": "2026-04-15T17:30:00+09:00",
            "homeLineup": [{"player_external_id": "LG-B001", "batting_order": 1, "position": "CF"}],
            "awayLineup": [],
        }
    )
    raw = _make_raw_payload(session, ingestion_run, body, category="lineup")

    result1 = normalize_lineup(session, raw)
    result2 = normalize_lineup(session, raw)

    assert result1.snapshot_id == result2.snapshot_id
    snapshots = session.execute(select(ActualLineupSnapshot)).scalars().all()
    assert len(snapshots) == 1


def test_normalize_lineup_snapshot_references_ingestion_run(
    session: Session, ingestion_run: IngestionRun
) -> None:
    """ActualLineupSnapshot.ingestion_run_id가 raw 페이로드의 ingestion_run_id와 일치해야 한다."""
    lg = _seed_team(session, "LG", "LG 트윈스")
    doo = _seed_team(session, "DOO", "두산 베어스")
    game = _seed_game(session, home_team=lg, away_team=doo)
    _seed_player(session, lg, "LG-B001", "홍길동", "CF")

    body = json.dumps(
        {
            "game_external_id": game.external_id,
            "team_code": "LG",
            "announced_at": "2026-04-15T17:30:00+09:00",
            "homeLineup": [{"player_external_id": "LG-B001", "batting_order": 1, "position": "CF"}],
            "awayLineup": [],
        }
    )
    raw = _make_raw_payload(session, ingestion_run, body, category="lineup")

    result = normalize_lineup(session, raw)

    snapshot = session.get(ActualLineupSnapshot, result.snapshot_id)
    assert snapshot is not None
    assert snapshot.ingestion_run_id == raw.ingestion_run_id


def test_normalize_lineup_html_raises_not_implemented(
    session: Session, ingestion_run: IngestionRun
) -> None:
    """HTML content_type이면 NotImplementedError를 발생시켜야 한다."""
    raw = _make_raw_payload(
        session,
        ingestion_run,
        "<html>lineup</html>",
        category="lineup",
        content_type=CONTENT_TYPE_HTML,
    )

    with pytest.raises(NotImplementedError, match="HTML"):
        normalize_lineup(session, raw)


# ---------------------------------------------------------------------------
# box_score normalizer 테스트
# ---------------------------------------------------------------------------


def test_normalize_box_score_skips_non_final(session: Session, ingestion_run: IngestionRun) -> None:
    """gameStatus가 FINAL이 아닌 페이로드는 스냅샷을 생성하지 않아야 한다."""
    body = json.dumps({"gameStatus": "IN_PROGRESS", "game_external_id": "20260415LGDOO"})
    raw = _make_raw_payload(session, ingestion_run, body, category="box_score")

    result = normalize_box_score(session, raw)

    assert result.skipped_not_final is True
    assert result.snapshot_id is None
    assert result.rows_created == 0


def test_normalize_box_score_skips_waiting_status(
    session: Session, ingestion_run: IngestionRun
) -> None:
    """gameStatus가 WAITING이면 스냅샷을 생성하지 않아야 한다."""
    body = json.dumps({"gameStatus": "WAITING", "game_external_id": "20260415LGDOO"})
    raw = _make_raw_payload(session, ingestion_run, body, category="box_score")

    result = normalize_box_score(session, raw)

    assert result.skipped_not_final is True
    assert result.snapshot_id is None


def test_normalize_box_score_creates_snapshot_and_rows(
    session: Session, ingestion_run: IngestionRun
) -> None:
    """FINAL 페이로드를 정규화하여 BoxScoreSnapshot과 BoxScoreRow를 생성해야 한다."""
    lg = _seed_team(session, "LG", "LG 트윈스")
    doo = _seed_team(session, "DOO", "두산 베어스")
    game = _seed_game(session, home_team=lg, away_team=doo)
    hitter = _seed_player(session, lg, "LG-B001", "홍길동", "1B")
    pitcher = _seed_player(session, doo, "DOO-P001", "김투수", "P")

    body = json.dumps(
        {
            "game_external_id": game.external_id,
            "taken_at": "2026-04-15T22:00:00+09:00",
            "gameStatus": "FINAL",
            "lg_hitters": [
                {
                    "player_external_id": "LG-B001",
                    "at_bats": 4,
                    "hits": 2,
                    "runs": 1,
                    "rbis": 1,
                    "extra_stats_json": {},
                }
            ],
            "opponent_pitchers": [
                {
                    "player_external_id": "DOO-P001",
                    "innings_pitched": 5.2,
                    "extra_stats_json": {},
                }
            ],
            "opponent_team_code": "DOO",
        }
    )
    raw = _make_raw_payload(session, ingestion_run, body, category="box_score")

    result = normalize_box_score(session, raw)

    assert result.skipped_not_final is False
    assert result.snapshot_id is not None
    assert result.rows_created == 2
    assert result.rows_skipped == 0

    hitter_row = session.execute(
        select(BoxScoreRow).where(
            BoxScoreRow.snapshot_id == result.snapshot_id,
            BoxScoreRow.player_id == hitter.id,
        )
    ).scalar_one()
    assert hitter_row.at_bats == 4
    assert hitter_row.hits == 2
    assert hitter_row.innings_pitched is None

    pitcher_row = session.execute(
        select(BoxScoreRow).where(
            BoxScoreRow.snapshot_id == result.snapshot_id,
            BoxScoreRow.player_id == pitcher.id,
        )
    ).scalar_one()
    assert pitcher_row.innings_pitched == pytest.approx(5.2)
    assert pitcher_row.at_bats is None


def test_normalize_box_score_is_idempotent(session: Session, ingestion_run: IngestionRun) -> None:
    """같은 페이로드를 두 번 정규화해도 BoxScoreSnapshot이 중복 생성되지 않아야 한다."""
    lg = _seed_team(session, "LG", "LG 트윈스")
    doo = _seed_team(session, "DOO", "두산 베어스")
    game = _seed_game(session, home_team=lg, away_team=doo)
    _seed_player(session, lg, "LG-B001", "홍길동", "1B")

    body = json.dumps(
        {
            "game_external_id": game.external_id,
            "taken_at": "2026-04-15T22:00:00+09:00",
            "gameStatus": "FINAL",
            "lg_hitters": [
                {"player_external_id": "LG-B001", "at_bats": 4, "hits": 2, "runs": 1, "rbis": 1}
            ],
            "opponent_pitchers": [],
        }
    )
    raw = _make_raw_payload(session, ingestion_run, body, category="box_score")

    result1 = normalize_box_score(session, raw)
    result2 = normalize_box_score(session, raw)

    assert result1.snapshot_id == result2.snapshot_id
    snapshots = session.execute(select(BoxScoreSnapshot)).scalars().all()
    assert len(snapshots) == 1


def test_normalize_box_score_snapshot_references_ingestion_run(
    session: Session, ingestion_run: IngestionRun
) -> None:
    """BoxScoreSnapshot.ingestion_run_id가 raw 페이로드의 ingestion_run_id와 일치해야 한다."""
    lg = _seed_team(session, "LG", "LG 트윈스")
    doo = _seed_team(session, "DOO", "두산 베어스")
    game = _seed_game(session, home_team=lg, away_team=doo)
    _seed_player(session, lg, "LG-B001", "홍길동", "CF")

    body = json.dumps(
        {
            "game_external_id": game.external_id,
            "taken_at": "2026-04-15T22:00:00+09:00",
            "gameStatus": "FINAL",
            "lg_hitters": [
                {"player_external_id": "LG-B001", "at_bats": 3, "hits": 1, "runs": 0, "rbis": 0}
            ],
            "opponent_pitchers": [],
        }
    )
    raw = _make_raw_payload(session, ingestion_run, body, category="box_score")

    result = normalize_box_score(session, raw)

    snapshot = session.get(BoxScoreSnapshot, result.snapshot_id)
    assert snapshot is not None
    assert snapshot.ingestion_run_id == raw.ingestion_run_id


def test_normalize_box_score_html_raises_not_implemented(
    session: Session, ingestion_run: IngestionRun
) -> None:
    """HTML content_type이면 NotImplementedError를 발생시켜야 한다."""
    raw = _make_raw_payload(
        session,
        ingestion_run,
        "<html>box_score</html>",
        category="box_score",
        content_type=CONTENT_TYPE_HTML,
    )

    with pytest.raises(NotImplementedError, match="HTML"):
        normalize_box_score(session, raw)


def test_normalize_box_score_missing_team_code_inferred_with_review_reason(
    session: Session, ingestion_run: IngestionRun
) -> None:
    """페이로드에 team_code가 없으면 게임으로부터 추론하고 needs_review_reasons에 기록해야 한다."""
    lg = _seed_team(session, "LG", "LG 트윈스")
    doo = _seed_team(session, "DOO", "두산 베어스")
    game = _seed_game(session, home_team=lg, away_team=doo)
    _seed_player(session, lg, "LG-B001", "홍길동", "1B")

    # team_code 필드 없음 — 게임 홈/어웨이로부터 LG를 추론해야 함
    body = json.dumps(
        {
            "game_external_id": game.external_id,
            "taken_at": "2026-04-15T22:00:00+09:00",
            "gameStatus": "FINAL",
            "lg_hitters": [
                {"player_external_id": "LG-B001", "at_bats": 4, "hits": 2, "runs": 1, "rbis": 1}
            ],
            "opponent_pitchers": [],
        }
    )
    raw = _make_raw_payload(session, ingestion_run, body, category="box_score")

    result = normalize_box_score(session, raw)

    # 추론된 team_code='LG'로 매칭이 성공해야 함
    assert result.rows_created == 1
    # 감사 추적용 reason이 기록되어야 함
    assert any("team_code not specified" in r for r in result.needs_review_reasons)


def test_normalize_box_score_missing_team_code_raises_when_lg_not_in_game(
    session: Session, ingestion_run: IngestionRun
) -> None:
    """team_code 없고 게임 양 팀 모두 LG가 아니면 ValueError를 발생시켜야 한다."""
    doo = _seed_team(session, "DOO", "두산 베어스")
    ssg = _seed_team(session, "SSG", "SSG 랜더스")
    game = _seed_game(session, home_team=doo, away_team=ssg, external_id="20260415DOOSSG")

    body = json.dumps(
        {
            "game_external_id": game.external_id,
            "taken_at": "2026-04-15T22:00:00+09:00",
            "gameStatus": "FINAL",
            "lg_hitters": [],
            "opponent_pitchers": [],
        }
    )
    raw = _make_raw_payload(session, ingestion_run, body, category="box_score")

    with pytest.raises(ValueError, match="missing 'team_code'"):
        normalize_box_score(session, raw)
