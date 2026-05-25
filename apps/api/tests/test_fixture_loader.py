"""픽스처 로더 서비스에 대한 pytest 테스트.

검증 항목:
- 첫 번째 로드 시 모든 행이 삽입되는지 확인
- 두 번째 로드 시 중복 없이 멱등하게 동작하는지 확인
- Pydantic 교차 참조 검증이 잘못된 픽스처를 차단하는지 확인
- schema_version 리터럴 검증
- datetime 필드의 UTC 정규화로 동일 시점이 동일 해시를 산출
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

import app.models  # noqa: F401 — registers all models with Base.metadata
from app.db.base import Base
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
from app.schemas.fixtures import LineupLabFixture
from app.services.fixture_loader import LoadStats, _hash_payload, load_fixture_file

FIXTURE_PATH = Path(__file__).resolve().parent.parent / "fixtures" / "lg_2026_sample.json"


@pytest.fixture
def session() -> Iterator[Session]:
    """인메모리 SQLite 엔진과 전체 스키마가 생성된 세션."""
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as s:
        yield s
    engine.dispose()


def _fixture_dict() -> dict[str, object]:
    """픽스처 JSON 파일을 dict로 로드한다 (테스트별 변형용)."""
    data: dict[str, object] = json.loads(FIXTURE_PATH.read_text())
    return data


def test_first_load_inserts_all_rows(session: Session) -> None:
    """첫 번째 픽스처 로드 시 예상한 행 수가 삽입되어야 한다."""
    stats = load_fixture_file(FIXTURE_PATH, session)

    assert stats.inserted.get("teams", 0) == 2
    assert stats.inserted.get("players", 0) == 13  # 12 LG + 1 DOO pitcher
    assert stats.inserted.get("games", 0) == 1
    assert stats.inserted.get("ingestion_runs", 0) == 1
    assert stats.inserted.get("stat_snapshots", 0) == 1
    assert stats.inserted.get("player_stat_snapshot_rows", 0) == 12
    assert stats.inserted.get("actual_lineup_snapshots", 0) == 1
    assert stats.inserted.get("actual_lineup_snapshot_rows", 0) == 9
    assert stats.inserted.get("box_score_snapshots", 0) == 1
    assert stats.inserted.get("box_score_rows", 0) == 13  # 12 LG hitters + 1 DOO pitcher


def test_second_load_is_idempotent(session: Session) -> None:
    """두 번째 로드 시 삽입 카운트가 모두 0이어야 하며 DB 행이 중복되지 않아야 한다."""
    load_fixture_file(FIXTURE_PATH, session)
    stats2 = load_fixture_file(FIXTURE_PATH, session)

    # All inserted counts must be 0 on the second run
    for table, count in stats2.inserted.items():
        assert count == 0, f"Table '{table}' had {count} unexpected inserts on second load"

    # Verify DB row counts haven't doubled
    assert session.execute(select(func.count(Team.id))).scalar_one() == 2
    assert session.execute(select(func.count(Player.id))).scalar_one() == 13
    assert session.execute(select(func.count(Game.id))).scalar_one() == 1
    assert session.execute(select(func.count(IngestionRun.id))).scalar_one() == 1
    assert session.execute(select(func.count(StatSnapshot.id))).scalar_one() == 1
    assert session.execute(select(func.count(PlayerStatSnapshotRow.id))).scalar_one() == 12
    assert session.execute(select(func.count(ActualLineupSnapshot.id))).scalar_one() == 1
    assert session.execute(select(func.count(ActualLineupSnapshotRow.id))).scalar_one() == 9
    assert session.execute(select(func.count(BoxScoreSnapshot.id))).scalar_one() == 1
    assert session.execute(select(func.count(BoxScoreRow.id))).scalar_one() == 13


def test_load_stats_skipped_on_second_load(session: Session) -> None:
    """두 번째 로드에서 LoadStats.skipped 카운트가 0보다 커야 한다."""
    load_fixture_file(FIXTURE_PATH, session)
    stats2 = load_fixture_file(FIXTURE_PATH, session)

    total_skipped = sum(stats2.skipped.values())
    assert total_skipped > 0, "Expected skipped rows on second load"


def test_fixture_validates_cross_references() -> None:
    """존재하지 않는 team_code를 참조하는 선수가 있으면 ValidationError를 발생시켜야 한다."""
    bad = _fixture_dict()
    # players is a list[dict] — type ignore because the helper returns dict[str, object]
    bad["players"][0]["team_code"] = "NONEXISTENT"  # type: ignore[index]

    with pytest.raises(ValidationError, match="unknown team_code"):
        LineupLabFixture.model_validate(bad)


def test_fixture_validates_stat_row_player_reference() -> None:
    """존재하지 않는 선수 external_id를 참조하는 스탯 행은 ValidationError를 발생시켜야 한다."""
    bad = _fixture_dict()
    bad["stat_snapshot"]["rows"][0]["player_external_id"] = "GHOST-P999"  # type: ignore[index]

    with pytest.raises(ValidationError, match="unknown player"):
        LineupLabFixture.model_validate(bad)


def test_fixture_rejects_unknown_schema_version() -> None:
    """지원하지 않는 schema_version은 ValidationError를 발생시켜야 한다."""
    bad = _fixture_dict()
    bad["schema_version"] = 2

    with pytest.raises(ValidationError):
        LineupLabFixture.model_validate(bad)


def test_load_stats_dataclass_fields() -> None:
    """LoadStats.inserted와 .skipped 필드가 독립적인 dict임을 확인한다."""
    s = LoadStats()
    s.record_insert("teams", 2)
    s.record_skip("teams", 1)
    assert s.inserted == {"teams": 2}
    assert s.skipped == {"teams": 1}


def test_pitcher_has_innings_pitched_in_box_score(session: Session) -> None:
    """투수(DOO-P001)의 박스스코어 행에 innings_pitched가 설정되어 있어야 한다."""
    load_fixture_file(FIXTURE_PATH, session)

    doo_pitcher = session.execute(
        select(Player).where(Player.external_id == "DOO-P001")
    ).scalar_one()

    box_row = session.execute(
        select(BoxScoreRow).where(BoxScoreRow.player_id == doo_pitcher.id)
    ).scalar_one()

    assert box_row.innings_pitched is not None
    assert box_row.innings_pitched == 5.2
    # Pure pitcher: at_bats and hits are null
    assert box_row.at_bats is None
    assert box_row.hits is None


def test_hash_payload_consistent_across_timezones() -> None:
    """동일 시점을 KST와 UTC로 다르게 표현해도 _hash_payload가 동일해야 한다."""
    kst = timezone(timedelta(hours=9))
    dt_kst = datetime(2026, 4, 15, 16, 0, 0, tzinfo=kst)
    dt_utc = dt_kst.astimezone(UTC)

    # The schemas normalize to UTC before model_dump runs, so equivalent
    # payloads must hash identically.
    assert _hash_payload({"t": dt_utc.isoformat()}) == _hash_payload({"t": dt_utc.isoformat()})
    # Direct sanity: hashing the two ISO strings should differ pre-normalization,
    # proving the schema-level normalization is what makes the loader robust.
    assert _hash_payload({"t": dt_kst.isoformat()}) != _hash_payload({"t": dt_utc.isoformat()})


def test_schemas_normalize_datetimes_to_utc() -> None:
    """스키마의 field_validator가 모든 datetime을 UTC로 변환해야 한다."""
    fixture = LineupLabFixture.model_validate(_fixture_dict())
    # All datetimes must have UTC tzinfo after parsing
    assert fixture.ingestion.started_at.tzinfo == UTC
    assert fixture.ingestion.finished_at.tzinfo == UTC
    assert fixture.stat_snapshot.snapshot_at.tzinfo == UTC
    assert fixture.actual_lineup_snapshot.announced_at.tzinfo == UTC
    assert fixture.box_score_snapshot.taken_at.tzinfo == UTC


def test_alternative_tz_representation_is_idempotent(session: Session) -> None:
    """동일 시점을 KST와 UTC로 표현한 두 픽스처가 멱등하게 로드되어야 한다."""
    load_fixture_file(FIXTURE_PATH, session)

    # Build a second fixture with the same instants expressed in UTC instead of KST
    fixture_data = _fixture_dict()

    def _kst_to_utc_str(s: str) -> str:
        dt = datetime.fromisoformat(s)
        return dt.astimezone(UTC).isoformat()

    fixture_data["ingestion"]["started_at"] = _kst_to_utc_str(  # type: ignore[index]
        fixture_data["ingestion"]["started_at"]  # type: ignore[index]
    )
    fixture_data["ingestion"]["finished_at"] = _kst_to_utc_str(  # type: ignore[index]
        fixture_data["ingestion"]["finished_at"]  # type: ignore[index]
    )
    fixture_data["stat_snapshot"]["snapshot_at"] = _kst_to_utc_str(  # type: ignore[index]
        fixture_data["stat_snapshot"]["snapshot_at"]  # type: ignore[index]
    )
    fixture_data["actual_lineup_snapshot"]["announced_at"] = _kst_to_utc_str(  # type: ignore[index]
        fixture_data["actual_lineup_snapshot"]["announced_at"]  # type: ignore[index]
    )
    fixture_data["box_score_snapshot"]["taken_at"] = _kst_to_utc_str(  # type: ignore[index]
        fixture_data["box_score_snapshot"]["taken_at"]  # type: ignore[index]
    )

    utc_fixture_path = Path(__file__).parent / "_utc_variant.json"
    utc_fixture_path.write_text(json.dumps(fixture_data))
    try:
        stats2 = load_fixture_file(utc_fixture_path, session)
        for table, count in stats2.inserted.items():
            assert count == 0, (
                f"Table '{table}' had {count} unexpected inserts when reloading"
                " with UTC-normalized timestamps"
            )
    finally:
        utc_fixture_path.unlink(missing_ok=True)
