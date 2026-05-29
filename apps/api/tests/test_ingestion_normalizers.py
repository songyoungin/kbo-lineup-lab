"""Ingestion normalizer 및 player matcher 테스트.

검증 항목:
- player_matcher: external_id 매칭; (team, name) 폴백 → needs_review;
  ambiguous → needs_review; not_found
- schedule: JSON 파싱 → Game 생성; 멱등성; HTML → NotImplementedError
- roster: Player 행 생성; 멱등성; HTML → NotImplementedError
- player_stats: StatSnapshot + 행 생성; content_hash 멱등성; needs_review 이유 노출
- lineup: team_code 기준 홈/어웨이 선택; 멱등성 (natural key)
- box_score: LG 박스스코어 부재 시 건너뜀; recordData 정규화
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
    """Naver schedule JSON payload is parsed and a Game row is created for the LG game."""
    lg = _seed_team(session, "LG", "LG Twins")
    ob = _seed_team(session, "OB", "Doosan Bears")
    body = json.dumps(
        {
            "result": {
                "games": [
                    {
                        "gameId": "20260415OBLG02026",
                        "gameDate": "2026-04-15",
                        "homeTeamCode": "LG",
                        "awayTeamCode": "OB",
                        "stadium": "Jamsil",
                    }
                ]
            }
        }
    )
    raw = _make_raw_payload(session, ingestion_run, body, category="schedule")

    result = normalize_schedule(session, raw)

    assert result.games_created == 1
    assert result.games_existing == 0
    game = session.execute(select(Game).where(Game.external_id == "20260415OBLG0")).scalar_one()
    assert game.home_team_id == lg.id
    assert game.away_team_id == ob.id
    assert str(game.game_date) == "2026-04-15"
    assert game.venue == "Jamsil"


def test_normalize_schedule_is_idempotent(session: Session, ingestion_run: IngestionRun) -> None:
    """Normalizing the same Naver schedule payload twice does not duplicate Game rows."""
    _seed_team(session, "LG", "LG Twins")
    _seed_team(session, "OB", "Doosan Bears")
    body = json.dumps(
        {
            "result": {
                "games": [
                    {
                        "gameId": "20260415OBLG02026",
                        "gameDate": "2026-04-15",
                        "homeTeamCode": "LG",
                        "awayTeamCode": "OB",
                        "stadium": "Jamsil",
                    }
                ]
            }
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
# player_stats normalizer tests
# ---------------------------------------------------------------------------


def _make_naver_preview_body(
    *,
    h_code: str = "LG",
    a_code: str = "OB",
    hitter_ext_id: str = "LG-H001",
    hitter_name: str = "홍길동",
    pitcher_ext_id: str = "LG-P001",
    pitcher_name: str = "김투수",
    gdate: int = 20260415,
    gtime: str = "18:30",
) -> str:
    """Build a minimal Naver preview body for player_stats normalizer tests."""
    return json.dumps(
        {
            "result": {
                "previewData": {
                    "gameInfo": {
                        "gdate": gdate,
                        "gtime": gtime,
                        "hCode": h_code,
                        "aCode": a_code,
                    },
                    "homeTopPlayer": {
                        "playerCode": hitter_ext_id,
                        "playerInfo": {"name": hitter_name, "pCode": hitter_ext_id},
                        "currentSeasonStats": {
                            "ab": 100,
                            "hit": 30,
                            "hra": "0.300",
                            "obp": 0.360,
                            "rbi": 12,
                            "hr": 3,
                        },
                    },
                    "homeStarter": {
                        "playerInfo": {"name": pitcher_name, "pCode": pitcher_ext_id},
                        "currentSeasonStats": {
                            "era": "3.50",
                            "whip": "1.20",
                            "w": 3,
                            "l": 2,
                            "kk": 40,
                            "bb": 15,
                            "inn": "40.0",
                        },
                    },
                }
            }
        }
    )


def test_normalize_player_stats_creates_snapshot_and_rows(
    session: Session, ingestion_run: IngestionRun
) -> None:
    """StatSnapshot and PlayerStatSnapshotRow rows are created for hitter and pitcher."""
    _seed_team(session, "LG", "LG 트윈스")
    _seed_team(session, "OB", "두산 베어스")
    hitter = _seed_player(
        session,
        session.execute(select(Team).where(Team.code == "LG")).scalar_one(),
        "LG-H001",
        "홍길동",
    )
    pitcher = _seed_player(
        session,
        session.execute(select(Team).where(Team.code == "LG")).scalar_one(),
        "LG-P001",
        "김투수",
    )
    body = _make_naver_preview_body()
    raw = _make_raw_payload(session, ingestion_run, body, category="player_stats")

    result = normalize_player_stats(session, raw)

    assert result.rows_created == 2
    assert result.rows_skipped == 0
    snapshot = session.get(StatSnapshot, result.snapshot_id)
    assert snapshot is not None
    assert snapshot.ingestion_run_id == ingestion_run.id

    hitter_row = session.execute(
        select(PlayerStatSnapshotRow).where(
            PlayerStatSnapshotRow.snapshot_id == result.snapshot_id,
            PlayerStatSnapshotRow.player_id == hitter.id,
        )
    ).scalar_one()
    assert "obp" in hitter_row.stats_json
    assert hitter_row.stats_json["role"] == "hitter"

    pitcher_row = session.execute(
        select(PlayerStatSnapshotRow).where(
            PlayerStatSnapshotRow.snapshot_id == result.snapshot_id,
            PlayerStatSnapshotRow.player_id == pitcher.id,
        )
    ).scalar_one()
    assert "era" in pitcher_row.stats_json
    assert pitcher_row.stats_json["role"] == "pitcher"


def test_normalize_player_stats_idempotent_content_hash(
    session: Session, ingestion_run: IngestionRun
) -> None:
    """Normalizing the same payload twice does not create duplicate StatSnapshot rows."""
    _seed_team(session, "LG", "LG 트윈스")
    _seed_team(session, "OB", "두산 베어스")
    _seed_player(
        session,
        session.execute(select(Team).where(Team.code == "LG")).scalar_one(),
        "LG-H001",
        "홍길동",
    )
    _seed_player(
        session,
        session.execute(select(Team).where(Team.code == "LG")).scalar_one(),
        "LG-P001",
        "김투수",
    )

    body = _make_naver_preview_body()
    raw = _make_raw_payload(session, ingestion_run, body, category="player_stats")

    result1 = normalize_player_stats(session, raw)
    result2 = normalize_player_stats(session, raw)

    assert result1.snapshot_id == result2.snapshot_id
    snapshots = session.execute(select(StatSnapshot)).scalars().all()
    assert len(snapshots) == 1


def test_normalize_player_stats_needs_review_reason_surfaced(
    session: Session, ingestion_run: IngestionRun
) -> None:
    """Unknown player is skipped and a reason is recorded in needs_review_reasons."""
    _seed_team(session, "LG", "LG 트윈스")
    _seed_team(session, "OB", "두산 베어스")
    # Only seed the hitter — pitcher will be NOT_FOUND.
    _seed_player(
        session,
        session.execute(select(Team).where(Team.code == "LG")).scalar_one(),
        "LG-H001",
        "홍길동",
    )

    body = _make_naver_preview_body(pitcher_ext_id="LG-GHOST", pitcher_name="없는선수")
    raw = _make_raw_payload(session, ingestion_run, body, category="player_stats")

    result = normalize_player_stats(session, raw)

    assert result.rows_created == 1
    assert result.rows_skipped == 1
    assert len(result.needs_review_reasons) > 0


def test_normalize_player_stats_snapshot_references_ingestion_run(
    session: Session, ingestion_run: IngestionRun
) -> None:
    """StatSnapshot.ingestion_run_id matches the raw payload's ingestion_run_id."""
    _seed_team(session, "LG", "LG 트윈스")
    _seed_team(session, "OB", "두산 베어스")
    _seed_player(
        session,
        session.execute(select(Team).where(Team.code == "LG")).scalar_one(),
        "LG-H001",
        "홍길동",
    )
    _seed_player(
        session,
        session.execute(select(Team).where(Team.code == "LG")).scalar_one(),
        "LG-P001",
        "김투수",
    )

    body = _make_naver_preview_body()
    raw = _make_raw_payload(session, ingestion_run, body, category="player_stats")

    result = normalize_player_stats(session, raw)

    snapshot = session.get(StatSnapshot, result.snapshot_id)
    assert snapshot is not None
    assert snapshot.ingestion_run_id == raw.ingestion_run_id


def test_normalize_player_stats_html_raises_not_implemented(
    session: Session, ingestion_run: IngestionRun
) -> None:
    """HTML content_type raises NotImplementedError."""
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


_PREVIEW_SOURCE_URL = "https://api-gw.sports.naver.com/schedule/games/20260415DOLG02026/preview"


def _make_preview_body(
    *,
    home_code: str,
    away_code: str,
    home_lineup: list[dict[str, object]],
    away_lineup: list[dict[str, object]],
    gdate: int = 20260415,
    gtime: str = "18:30",
) -> str:
    """Build a minimal Naver preview body for lineup normalizer tests."""
    return json.dumps(
        {
            "result": {
                "previewData": {
                    "gameInfo": {
                        "gdate": gdate,
                        "gtime": gtime,
                        "hCode": home_code,
                        "aCode": away_code,
                    },
                    "homeTeamLineUp": {"fullLineUp": home_lineup},
                    "awayTeamLineUp": {"fullLineUp": away_lineup},
                }
            }
        }
    )


_SAMPLE_BATTER = {
    "playerCode": "LG-B001",
    "playerName": "홍길동",
    "position": "8",
    "batorder": 1,
    "hitType": "우투좌타",
    "batsThrows": "좌타",
}


def test_normalize_lineup_lg_home(session: Session, ingestion_run: IngestionRun) -> None:
    """LG가 홈팀(hCode=LG)일 때 homeTeamLineUp을 선택해야 한다."""
    lg = _seed_team(session, "LG", "LG 트윈스")
    doo = _seed_team(session, "DOO", "두산 베어스")
    _seed_game(session, home_team=lg, away_team=doo, external_id="20260415DOLG0")

    body = _make_preview_body(
        home_code="LG", away_code="DO", home_lineup=[dict(_SAMPLE_BATTER)], away_lineup=[]
    )
    raw = _make_raw_payload(
        session, ingestion_run, body, category="lineup", source_url=_PREVIEW_SOURCE_URL
    )

    result = normalize_lineup(session, raw)

    assert result.rows_created == 1
    assert result.rows_skipped == 0
    player = session.execute(select(Player).where(Player.external_id == "LG-B001")).scalar_one()
    assert player.bats == "L"
    assert player.throws == "R"
    row = session.execute(
        select(ActualLineupSnapshotRow).where(
            ActualLineupSnapshotRow.snapshot_id == result.snapshot_id,
            ActualLineupSnapshotRow.player_id == player.id,
        )
    ).scalar_one()
    assert row.batting_order == 1
    assert row.position == "8"


def test_normalize_lineup_lg_away(session: Session, ingestion_run: IngestionRun) -> None:
    """LG가 어웨이팀(aCode=LG)일 때 awayTeamLineUp을 선택해야 한다."""
    lg = _seed_team(session, "LG", "LG 트윈스")
    doo = _seed_team(session, "DOO", "두산 베어스")
    # DOO가 홈, LG가 어웨이
    _seed_game(session, home_team=doo, away_team=lg, external_id="20260415DOLG0")

    body = _make_preview_body(
        home_code="DO", away_code="LG", home_lineup=[], away_lineup=[dict(_SAMPLE_BATTER)]
    )
    raw = _make_raw_payload(
        session, ingestion_run, body, category="lineup", source_url=_PREVIEW_SOURCE_URL
    )

    result = normalize_lineup(session, raw)

    assert result.rows_created == 1
    assert result.rows_skipped == 0


def test_normalize_lineup_is_idempotent(session: Session, ingestion_run: IngestionRun) -> None:
    """같은 (game_id, team_id, announced_at) 키로 두 번 정규화해도 스냅샷이 중복되지 않아야 한다."""
    lg = _seed_team(session, "LG", "LG 트윈스")
    doo = _seed_team(session, "DOO", "두산 베어스")
    _seed_game(session, home_team=lg, away_team=doo, external_id="20260415DOLG0")

    body = _make_preview_body(
        home_code="LG", away_code="DO", home_lineup=[dict(_SAMPLE_BATTER)], away_lineup=[]
    )
    raw = _make_raw_payload(
        session, ingestion_run, body, category="lineup", source_url=_PREVIEW_SOURCE_URL
    )

    result1 = normalize_lineup(session, raw)
    result2 = normalize_lineup(session, raw)

    assert result1.snapshot_id == result2.snapshot_id
    assert result2.rows_created == 0
    snapshots = session.execute(select(ActualLineupSnapshot)).scalars().all()
    assert len(snapshots) == 1


def test_normalize_lineup_snapshot_references_ingestion_run(
    session: Session, ingestion_run: IngestionRun
) -> None:
    """ActualLineupSnapshot.ingestion_run_id가 raw 페이로드의 ingestion_run_id와 일치해야 한다."""
    lg = _seed_team(session, "LG", "LG 트윈스")
    doo = _seed_team(session, "DOO", "두산 베어스")
    _seed_game(session, home_team=lg, away_team=doo, external_id="20260415DOLG0")

    body = _make_preview_body(
        home_code="LG", away_code="DO", home_lineup=[dict(_SAMPLE_BATTER)], away_lineup=[]
    )
    raw = _make_raw_payload(
        session, ingestion_run, body, category="lineup", source_url=_PREVIEW_SOURCE_URL
    )

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


_RECORD_GAME_ID = "20260415DOLG0"  # away DO, home LG
_RECORD_SOURCE_URL = "https://api-gw.sports.naver.com/schedule/games/20260415DOLG02026/record"


def _make_record_body(
    *,
    home_code: str,
    away_code: str,
    home_batters: list[dict[str, object]],
    away_batters: list[dict[str, object]],
    gdate: int = 20260415,
    gtime: str = "18:30",
) -> str:
    """Build a minimal Naver record body for box_score normalizer tests."""
    return json.dumps(
        {
            "result": {
                "recordData": {
                    "gameInfo": {
                        "gdate": gdate,
                        "gtime": gtime,
                        "hCode": home_code,
                        "aCode": away_code,
                    },
                    "battersBoxscore": {"home": home_batters, "away": away_batters},
                }
            }
        }
    )


_SAMPLE_BOX_BATTER = {
    "playerCode": "LG-B001",
    "name": "홍길동",
    "batOrder": 1,
    "pos": "중",
    "ab": 4,
    "hit": 2,
    "run": 1,
    "rbi": 1,
    "hr": 0,
    "bb": 1,
    "kk": 0,
    "sb": 0,
    "hra": "0.300",
}


def test_normalize_box_score_skips_non_final(session: Session, ingestion_run: IngestionRun) -> None:
    """LG batters 리스트가 비어 있으면 스냅샷을 생성하지 않아야 한다."""
    lg = _seed_team(session, "LG", "LG 트윈스")
    doo = _seed_team(session, "DOO", "두산 베어스")
    _seed_game(session, home_team=lg, away_team=doo, external_id=_RECORD_GAME_ID)
    body = _make_record_body(home_code="LG", away_code="DO", home_batters=[], away_batters=[])
    raw = _make_raw_payload(
        session, ingestion_run, body, category="box_score", source_url=_RECORD_SOURCE_URL
    )

    result = normalize_box_score(session, raw)

    assert result.skipped_not_final is True
    assert result.snapshot_id is None
    assert result.rows_created == 0


def test_normalize_box_score_creates_snapshot_and_rows(
    session: Session, ingestion_run: IngestionRun
) -> None:
    """LG 박스스코어를 정규화하여 BoxScoreSnapshot과 BoxScoreRow를 생성해야 한다."""
    lg = _seed_team(session, "LG", "LG 트윈스")
    doo = _seed_team(session, "DOO", "두산 베어스")
    _seed_game(session, home_team=lg, away_team=doo, external_id=_RECORD_GAME_ID)
    hitter = _seed_player(session, lg, "LG-B001", "홍길동", "1B")

    body = _make_record_body(
        home_code="LG",
        away_code="DO",
        home_batters=[dict(_SAMPLE_BOX_BATTER)],
        away_batters=[],
    )
    raw = _make_raw_payload(
        session, ingestion_run, body, category="box_score", source_url=_RECORD_SOURCE_URL
    )

    result = normalize_box_score(session, raw)

    assert result.skipped_not_final is False
    assert result.snapshot_id is not None
    assert result.rows_created == 1
    assert result.rows_skipped == 0

    hitter_row = session.execute(
        select(BoxScoreRow).where(
            BoxScoreRow.snapshot_id == result.snapshot_id,
            BoxScoreRow.player_id == hitter.id,
        )
    ).scalar_one()
    assert hitter_row.at_bats == 4
    assert hitter_row.hits == 2
    assert hitter_row.runs == 1
    assert hitter_row.rbis == 1
    assert hitter_row.innings_pitched is None
    assert hitter_row.extra_stats_json is not None
    assert hitter_row.extra_stats_json["hr"] == 0
    assert hitter_row.extra_stats_json["bb"] == 1


def test_normalize_box_score_is_idempotent(session: Session, ingestion_run: IngestionRun) -> None:
    """같은 페이로드를 두 번 정규화해도 BoxScoreSnapshot이 중복 생성되지 않아야 한다."""
    lg = _seed_team(session, "LG", "LG 트윈스")
    doo = _seed_team(session, "DOO", "두산 베어스")
    _seed_game(session, home_team=lg, away_team=doo, external_id=_RECORD_GAME_ID)
    _seed_player(session, lg, "LG-B001", "홍길동", "1B")

    body = _make_record_body(
        home_code="LG",
        away_code="DO",
        home_batters=[dict(_SAMPLE_BOX_BATTER)],
        away_batters=[],
    )
    raw = _make_raw_payload(
        session, ingestion_run, body, category="box_score", source_url=_RECORD_SOURCE_URL
    )

    result1 = normalize_box_score(session, raw)
    result2 = normalize_box_score(session, raw)

    assert result1.snapshot_id == result2.snapshot_id
    assert result2.rows_created == 0
    snapshots = session.execute(select(BoxScoreSnapshot)).scalars().all()
    assert len(snapshots) == 1


def test_normalize_box_score_snapshot_references_ingestion_run(
    session: Session, ingestion_run: IngestionRun
) -> None:
    """BoxScoreSnapshot.ingestion_run_id가 raw 페이로드의 ingestion_run_id와 일치해야 한다."""
    lg = _seed_team(session, "LG", "LG 트윈스")
    doo = _seed_team(session, "DOO", "두산 베어스")
    _seed_game(session, home_team=lg, away_team=doo, external_id=_RECORD_GAME_ID)
    _seed_player(session, lg, "LG-B001", "홍길동", "CF")

    body = _make_record_body(
        home_code="LG",
        away_code="DO",
        home_batters=[dict(_SAMPLE_BOX_BATTER)],
        away_batters=[],
    )
    raw = _make_raw_payload(
        session, ingestion_run, body, category="box_score", source_url=_RECORD_SOURCE_URL
    )

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


def test_normalize_box_score_skips_unknown_players(
    session: Session, ingestion_run: IngestionRun
) -> None:
    """선수를 찾지 못하면 행을 건너뛰고 스냅샷은 생성하며 needs_review를 기록해야 한다."""
    lg = _seed_team(session, "LG", "LG 트윈스")
    doo = _seed_team(session, "DOO", "두산 베어스")
    _seed_game(session, home_team=lg, away_team=doo, external_id=_RECORD_GAME_ID)
    # No players seeded.

    body = _make_record_body(
        home_code="LG",
        away_code="DO",
        home_batters=[dict(_SAMPLE_BOX_BATTER)],
        away_batters=[],
    )
    raw = _make_raw_payload(
        session, ingestion_run, body, category="box_score", source_url=_RECORD_SOURCE_URL
    )

    result = normalize_box_score(session, raw)

    assert result.rows_created == 0
    assert result.rows_skipped == 1
    assert result.needs_review_reasons
    assert result.snapshot_id is not None


def test_normalize_box_score_raises_when_lg_not_in_game(
    session: Session, ingestion_run: IngestionRun
) -> None:
    """게임 양 팀 모두 LG가 아니면 ValueError를 발생시켜야 한다."""
    doo = _seed_team(session, "DOO", "두산 베어스")
    ssg = _seed_team(session, "SSG", "SSG 랜더스")
    _seed_game(session, home_team=doo, away_team=ssg, external_id=_RECORD_GAME_ID)

    body = _make_record_body(
        home_code="DO",
        away_code="SS",
        home_batters=[dict(_SAMPLE_BOX_BATTER)],
        away_batters=[],
    )
    raw = _make_raw_payload(
        session, ingestion_run, body, category="box_score", source_url=_RECORD_SOURCE_URL
    )

    with pytest.raises(ValueError, match="LG not in game"):
        normalize_box_score(session, raw)
