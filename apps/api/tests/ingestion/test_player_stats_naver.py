"""normalize_player_stats builds one PlayerStatSnapshotRow per lineup player
from per-player season payloads (record.season row for the game's year), with
numeric OPS/OBP/SLG, and is idempotent."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime

from sqlalchemy.orm import Session

from app.ingestion.normalizers.player_stats import normalize_player_stats
from app.ingestion.raw_store import save_raw_payload
from app.ingestion.types import PayloadCategory
from app.models.game import Game
from app.models.player import Player
from app.models.snapshot import IngestionRun, PlayerStatSnapshotRow, StatSnapshot
from app.models.team import Team
from app.schemas.ingestion import RawPayloadCreate


def _seed(session: Session) -> None:
    lg = Team(code="LG", name="LG")
    wo = Team(code="WO", name="Kiwoom")
    session.add_all([lg, wo])
    session.flush()
    game = Game(
        external_id="20250514WOLG0",
        home_team_id=lg.id,
        away_team_id=wo.id,
        game_date=date(2025, 5, 14),
    )
    session.add(game)
    session.flush()
    session.add_all(
        [
            Player(
                team_id=lg.id,
                external_id="62415",
                name="박해민",
                position="CF",
                bats="L",
                throws="R",
            ),
            Player(
                team_id=lg.id,
                external_id="69102",
                name="문보경",
                position="3B",
                bats="L",
                throws="R",
            ),
        ]
    )
    session.flush()


def _save(session: Session, run_id: int, code: str, load_source: Callable[[str], str]) -> None:
    save_raw_payload(
        session,
        RawPayloadCreate(
            ingestion_run_id=run_id,
            category=PayloadCategory.PLAYER_STATS,
            source_name="naver_sports",
            source_url=f"https://api-gw.sports.naver.com/players/kbo/{code}/playerend-record",
            fetched_at=datetime.now(UTC),
            content_type="application/json",
            raw_body=load_source(f"naver/player_season_{code}.json"),
        ),
    )


def test_builds_one_row_per_player_with_numeric_rates(
    session: Session, load_source: Callable[[str], str]
) -> None:
    _seed(session)
    run = IngestionRun(source="test:season", status="running")
    session.add(run)
    session.flush()
    _save(session, run.id, "62415", load_source)
    _save(session, run.id, "69102", load_source)

    result = normalize_player_stats(
        session, game_external_id="20250514WOLG0", ingestion_run_id=run.id
    )

    assert session.query(StatSnapshot).count() == 1
    rows = session.query(PlayerStatSnapshotRow).all()
    assert len(rows) == 2
    assert result.rows_created == 2
    for row in rows:
        assert isinstance(row.stats_json["OPS"], float)
        assert isinstance(row.stats_json["OBP"], float)
        assert isinstance(row.stats_json["SLG"], float)
        assert row.stats_json["season_year"] == "2025"


def test_selects_2025_season_row_values(
    session: Session, load_source: Callable[[str], str]
) -> None:
    _seed(session)
    run = IngestionRun(source="test:season", status="running")
    session.add(run)
    session.flush()
    _save(session, run.id, "62415", load_source)
    normalize_player_stats(session, game_external_id="20250514WOLG0", ingestion_run_id=run.id)
    player = session.query(Player).filter(Player.external_id == "62415").one()
    row = (
        session.query(PlayerStatSnapshotRow)
        .filter(PlayerStatSnapshotRow.player_id == player.id)
        .one()
    )
    # Verified 2025 values for 박해민.
    assert abs(float(row.stats_json["OBP"]) - 0.379) < 1e-6  # type: ignore[arg-type]
    assert abs(float(row.stats_json["SLG"]) - 0.346) < 1e-6  # type: ignore[arg-type]
    assert abs(float(row.stats_json["OPS"]) - 0.725) < 1e-6  # type: ignore[arg-type]


def test_idempotent(session: Session, load_source: Callable[[str], str]) -> None:
    _seed(session)
    run = IngestionRun(source="test:season", status="running")
    session.add(run)
    session.flush()
    _save(session, run.id, "62415", load_source)
    normalize_player_stats(session, game_external_id="20250514WOLG0", ingestion_run_id=run.id)
    second = normalize_player_stats(
        session, game_external_id="20250514WOLG0", ingestion_run_id=run.id
    )
    assert second.rows_created == 0
    assert session.query(StatSnapshot).count() == 1


def test_skips_player_not_in_db(session: Session, load_source: Callable[[str], str]) -> None:
    # Seed teams + game but only ONE of the two players.
    lg = Team(code="LG", name="LG")
    wo = Team(code="WO", name="Kiwoom")
    session.add_all([lg, wo])
    session.flush()
    session.add(
        Game(
            external_id="20250514WOLG0",
            home_team_id=lg.id,
            away_team_id=wo.id,
            game_date=date(2025, 5, 14),
        )
    )
    session.add(
        Player(
            team_id=lg.id, external_id="62415", name="박해민", position="CF", bats="L", throws="R"
        )
    )
    session.flush()
    run = IngestionRun(source="test:season", status="running")
    session.add(run)
    session.flush()
    _save(session, run.id, "62415", load_source)
    _save(session, run.id, "69102", load_source)  # no Player row -> skipped
    result = normalize_player_stats(
        session, game_external_id="20250514WOLG0", ingestion_run_id=run.id
    )
    assert result.rows_created == 1
    assert result.rows_skipped == 1
