"""픽스처 JSON 파일을 데이터베이스에 멱등하게 로드하는 서비스."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.game import Game
from app.models.player import Player
from app.models.snapshot import (
    ActualLineupSnapshot,
    ActualLineupSnapshotRow,
    BoxScoreRow,
    BoxScoreSnapshot,
    IngestionRun,
    PlayerStatSnapshotRow,
    StatSnapshot,
)
from app.models.team import Team
from app.schemas.fixtures import (
    ActualLineupSnapshotFixture,
    BoxScoreSnapshotFixture,
    GameFixture,
    IngestionFixture,
    LineupLabFixture,
    PlayerFixture,
    StatSnapshotFixture,
    TeamFixture,
)


@dataclass
class LoadStats:
    """픽스처 로드 결과: 삽입된 행 수와 건너뛴 행 수를 테이블별로 기록한다."""

    inserted: dict[str, int] = field(default_factory=dict)
    skipped: dict[str, int] = field(default_factory=dict)

    def _inc(self, mapping: dict[str, int], table: str, count: int = 1) -> None:
        mapping[table] = mapping.get(table, 0) + count

    def record_insert(self, table: str, count: int = 1) -> None:
        """삽입 카운트를 기록한다."""
        self._inc(self.inserted, table, count)

    def record_skip(self, table: str, count: int = 1) -> None:
        """스킵 카운트를 기록한다."""
        self._inc(self.skipped, table, count)


def _hash_payload(payload: object) -> str:
    """객체를 정규화된 JSON으로 직렬화한 뒤 SHA-256 해시를 반환한다.

    호출 측에서 Pydantic 모델을 `mode="json"`으로 dump하여 전달하며, 모든
    datetime 필드는 스키마 단계의 field_validator를 통해 이미 UTC ISO 문자열로
    정규화되어 있다. 따라서 동일한 시점을 다른 tz 표현으로 받더라도 동일한
    해시를 산출한다.
    """
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


def _upsert_teams(
    session: Session,
    teams: list[TeamFixture],
    stats: LoadStats,
) -> dict[str, int]:
    """팀을 조회하고 없으면 삽입한다. code -> id 매핑을 반환한다."""
    team_id_by_code: dict[str, int] = {}
    for t in teams:
        existing = session.execute(select(Team).where(Team.code == t.code)).scalar_one_or_none()
        if existing is not None:
            team_id_by_code[t.code] = existing.id
            stats.record_skip("teams")
        else:
            new_team = Team(code=t.code, name=t.name)
            session.add(new_team)
            session.flush()
            team_id_by_code[t.code] = new_team.id
            stats.record_insert("teams")
    return team_id_by_code


def _upsert_players(
    session: Session,
    players: list[PlayerFixture],
    team_id_by_code: dict[str, int],
    stats: LoadStats,
) -> dict[str, int]:
    """선수를 조회하고 없으면 삽입한다. external_id -> id 매핑을 반환한다."""
    player_id_by_external: dict[str, int] = {}
    for p in players:
        existing = session.execute(
            select(Player).where(Player.external_id == p.external_id)
        ).scalar_one_or_none()
        if existing is not None:
            player_id_by_external[p.external_id] = existing.id
            stats.record_skip("players")
        else:
            new_player = Player(
                external_id=p.external_id,
                team_id=team_id_by_code[p.team_code],
                name=p.name,
                position=p.position,
            )
            session.add(new_player)
            session.flush()
            player_id_by_external[p.external_id] = new_player.id
            stats.record_insert("players")
    return player_id_by_external


def _upsert_game(
    session: Session,
    game: GameFixture,
    team_id_by_code: dict[str, int],
    stats: LoadStats,
) -> int:
    """경기를 조회하고 없으면 삽입한다. game id를 반환한다."""
    existing = session.execute(
        select(Game).where(Game.external_id == game.external_id)
    ).scalar_one_or_none()
    if existing is not None:
        stats.record_skip("games")
        return existing.id

    new_game = Game(
        external_id=game.external_id,
        home_team_id=team_id_by_code[game.home_team_code],
        away_team_id=team_id_by_code[game.away_team_code],
        game_date=game.game_date,
        venue=game.venue,
    )
    session.add(new_game)
    session.flush()
    stats.record_insert("games")
    return new_game.id


def _upsert_ingestion_run(
    session: Session,
    ingestion: IngestionFixture,
    stats: LoadStats,
) -> int:
    """인제스트 실행 센티넬 행을 조회하고 없으면 삽입한다. ingestion_run id를 반환한다."""
    existing = session.execute(
        select(IngestionRun).where(IngestionRun.source == ingestion.source)
    ).scalar_one_or_none()
    if existing is not None:
        stats.record_skip("ingestion_runs")
        return existing.id

    new_run = IngestionRun(
        source=ingestion.source,
        status="completed",
        started_at=ingestion.started_at,
        finished_at=ingestion.finished_at,
    )
    session.add(new_run)
    session.flush()
    stats.record_insert("ingestion_runs")
    return new_run.id


def _upsert_stat_snapshot(
    session: Session,
    stat_snapshot: StatSnapshotFixture,
    ingestion_run_id: int,
    player_id_by_external: dict[str, int],
    stats: LoadStats,
) -> None:
    """스탯 스냅샷과 행들을 조회하고 없으면 삽입한다."""
    payload = stat_snapshot.model_dump(mode="json")
    content_hash = _hash_payload(payload)

    existing = session.execute(
        select(StatSnapshot).where(StatSnapshot.content_hash == content_hash)
    ).scalar_one_or_none()

    if existing is not None:
        stats.record_skip("stat_snapshots")
        snapshot_id = existing.id
        # Count existing rows as skipped
        stats.record_skip("player_stat_snapshot_rows", len(stat_snapshot.rows))
    else:
        new_snap = StatSnapshot(
            ingestion_run_id=ingestion_run_id,
            snapshot_at=stat_snapshot.snapshot_at,
            content_hash=content_hash,
        )
        session.add(new_snap)
        session.flush()
        stats.record_insert("stat_snapshots")
        snapshot_id = new_snap.id

        for row in stat_snapshot.rows:
            player_id = player_id_by_external[row.player_external_id]
            existing_row = session.execute(
                select(PlayerStatSnapshotRow).where(
                    PlayerStatSnapshotRow.snapshot_id == snapshot_id,
                    PlayerStatSnapshotRow.player_id == player_id,
                )
            ).scalar_one_or_none()
            if existing_row is not None:
                stats.record_skip("player_stat_snapshot_rows")
            else:
                session.add(
                    PlayerStatSnapshotRow(
                        snapshot_id=snapshot_id,
                        player_id=player_id,
                        stats_json=dict(row.stats),
                    )
                )
                stats.record_insert("player_stat_snapshot_rows")


def _upsert_lineup_snapshot(
    session: Session,
    lineup: ActualLineupSnapshotFixture,
    game_id: int,
    team_id_by_code: dict[str, int],
    ingestion_run_id: int,
    player_id_by_external: dict[str, int],
    stats: LoadStats,
) -> None:
    """실제 라인업 스냅샷과 행들을 조회하고 없으면 삽입한다."""
    team_id = team_id_by_code[lineup.team_code]
    payload = lineup.model_dump(mode="json")
    content_hash = _hash_payload(payload)

    existing = session.execute(
        select(ActualLineupSnapshot).where(
            ActualLineupSnapshot.game_id == game_id,
            ActualLineupSnapshot.team_id == team_id,
            ActualLineupSnapshot.announced_at == lineup.announced_at,
        )
    ).scalar_one_or_none()

    if existing is not None:
        stats.record_skip("actual_lineup_snapshots")
        snapshot_id = existing.id
        stats.record_skip("actual_lineup_snapshot_rows", len(lineup.rows))
    else:
        new_snap = ActualLineupSnapshot(
            game_id=game_id,
            team_id=team_id,
            ingestion_run_id=ingestion_run_id,
            announced_at=lineup.announced_at,
            content_hash=content_hash,
        )
        session.add(new_snap)
        session.flush()
        stats.record_insert("actual_lineup_snapshots")
        snapshot_id = new_snap.id

        for row in lineup.rows:
            player_id = player_id_by_external[row.player_external_id]
            existing_row = session.execute(
                select(ActualLineupSnapshotRow).where(
                    ActualLineupSnapshotRow.snapshot_id == snapshot_id,
                    ActualLineupSnapshotRow.player_id == player_id,
                )
            ).scalar_one_or_none()
            if existing_row is not None:
                stats.record_skip("actual_lineup_snapshot_rows")
            else:
                session.add(
                    ActualLineupSnapshotRow(
                        snapshot_id=snapshot_id,
                        player_id=player_id,
                        batting_order=row.batting_order,
                        position=row.position,
                    )
                )
                stats.record_insert("actual_lineup_snapshot_rows")


def _upsert_box_score_snapshot(
    session: Session,
    box_score: BoxScoreSnapshotFixture,
    game_id: int,
    ingestion_run_id: int,
    player_id_by_external: dict[str, int],
    stats: LoadStats,
) -> None:
    """박스스코어 스냅샷과 행들을 조회하고 없으면 삽입한다."""
    payload = box_score.model_dump(mode="json")
    content_hash = _hash_payload(payload)

    existing = session.execute(
        select(BoxScoreSnapshot).where(BoxScoreSnapshot.content_hash == content_hash)
    ).scalar_one_or_none()

    if existing is not None:
        stats.record_skip("box_score_snapshots")
        snapshot_id = existing.id
        stats.record_skip("box_score_rows", len(box_score.rows))
    else:
        new_snap = BoxScoreSnapshot(
            game_id=game_id,
            ingestion_run_id=ingestion_run_id,
            taken_at=box_score.taken_at,
            content_hash=content_hash,
        )
        session.add(new_snap)
        session.flush()
        stats.record_insert("box_score_snapshots")
        snapshot_id = new_snap.id

        for row in box_score.rows:
            player_id = player_id_by_external[row.player_external_id]
            existing_row = session.execute(
                select(BoxScoreRow).where(
                    BoxScoreRow.snapshot_id == snapshot_id,
                    BoxScoreRow.player_id == player_id,
                )
            ).scalar_one_or_none()
            if existing_row is not None:
                stats.record_skip("box_score_rows")
            else:
                session.add(
                    BoxScoreRow(
                        snapshot_id=snapshot_id,
                        player_id=player_id,
                        at_bats=row.at_bats,
                        hits=row.hits,
                        runs=row.runs,
                        rbis=row.rbis,
                        extra_stats_json=row.extra_stats_json,
                        innings_pitched=row.innings_pitched,
                    )
                )
                stats.record_insert("box_score_rows")


def load_fixture_file(path: Path, session: Session) -> LoadStats:
    """픽스처 JSON 파일을 데이터베이스에 멱등하게 로드한다.

    Args:
        path: 픽스처 JSON 파일 경로.
        session: SQLAlchemy 세션 (커밋은 이 함수에서 수행).

    Returns:
        테이블별 삽입/스킵 카운트가 담긴 LoadStats.
    """
    fixture = LineupLabFixture.model_validate_json(path.read_text())
    stats = LoadStats()

    team_id_by_code = _upsert_teams(session, fixture.teams, stats)
    player_id_by_external = _upsert_players(session, fixture.players, team_id_by_code, stats)
    game_id = _upsert_game(session, fixture.game, team_id_by_code, stats)
    ingestion_run_id = _upsert_ingestion_run(session, fixture.ingestion, stats)
    _upsert_stat_snapshot(
        session, fixture.stat_snapshot, ingestion_run_id, player_id_by_external, stats
    )
    _upsert_lineup_snapshot(
        session,
        fixture.actual_lineup_snapshot,
        game_id,
        team_id_by_code,
        ingestion_run_id,
        player_id_by_external,
        stats,
    )
    _upsert_box_score_snapshot(
        session, fixture.box_score_snapshot, game_id, ingestion_run_id, player_id_by_external, stats
    )
    session.commit()
    return stats
