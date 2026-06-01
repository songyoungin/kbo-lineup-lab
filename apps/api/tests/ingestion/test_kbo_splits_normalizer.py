"""merge_hitter_splits_into_stats is a pure merge of L/R splits + RISP into a
stats_json copy; normalize_kbo_hitter_splits resolves KBO HitterDetail payloads
by external_id and writes those keys back into the player's snapshot row."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.orm import Session

from app.ingestion.normalizers.kbo_splits import (
    merge_hitter_splits_into_stats,
    normalize_kbo_hitter_splits,
)
from app.ingestion.raw_store import save_raw_payload
from app.ingestion.types import PayloadCategory
from app.models.player import Player
from app.models.snapshot import (
    IngestionRun,
    PlayerStatSnapshotRow,
    StatSnapshot,
)
from app.models.team import Team
from app.schemas.ingestion import RawPayloadCreate

_FIX = Path(__file__).resolve().parents[1] / "fixtures" / "sources" / "kbo"


def test_merge_sets_vs_hand_and_risp() -> None:
    sit = (_FIX / "hitter_situation_66108.html").read_text(encoding="utf-8")
    bas = (_FIX / "hitter_basic_66108.html").read_text(encoding="utf-8")
    merged = merge_hitter_splits_into_stats({"OPS": 0.8}, sit, bas)
    assert merged["vs_lhp_pa"] == 66
    assert int(merged["vs_rhp_pa"]) >= 141  # type: ignore[call-overload]
    assert float(merged["vs_lhp_ops"]) > 0  # type: ignore[arg-type]
    assert "risp_avg" in merged
    assert merged["OPS"] == 0.8


def test_merge_no_data_is_noop() -> None:
    assert merge_hitter_splits_into_stats({"OPS": 0.8}, "<html></html>", "<html></html>") == {
        "OPS": 0.8
    }


def _save_html(session: Session, run_id: int, source_url: str, raw_body: str) -> None:
    save_raw_payload(
        session,
        RawPayloadCreate(
            ingestion_run_id=run_id,
            category=PayloadCategory.PLAYER_STATS,
            source_name="kbo_official",
            source_url=source_url,
            fetched_at=datetime.now(UTC),
            content_type="text/html",
            raw_body=raw_body,
        ),
    )


def test_normalize_kbo_hitter_splits_updates_row(session: Session) -> None:
    team = Team(code="LG", name="LG")
    session.add(team)
    session.flush()
    player = Player(
        team_id=team.id,
        external_id="66108",
        name="홍길동",
        position="CF",
        bats="L",
        throws="R",
    )
    session.add(player)
    session.flush()

    run = IngestionRun(source="test:kbo_splits", status="running")
    session.add(run)
    session.flush()

    snapshot = StatSnapshot(
        ingestion_run_id=run.id,
        snapshot_at=datetime.now(UTC),
        content_hash="kbo-splits-test",
    )
    session.add(snapshot)
    session.flush()
    row = PlayerStatSnapshotRow(
        snapshot_id=snapshot.id,
        player_id=player.id,
        stats_json={"OPS": 0.8},
    )
    session.add(row)
    session.flush()

    sit = (_FIX / "hitter_situation_66108.html").read_text(encoding="utf-8")
    bas = (_FIX / "hitter_basic_66108.html").read_text(encoding="utf-8")
    _save_html(
        session,
        run.id,
        "https://www.koreabaseball.com/Record/Player/HitterDetail/Situation.aspx?playerId=66108",
        sit,
    )
    _save_html(
        session,
        run.id,
        "https://www.koreabaseball.com/Record/Player/HitterDetail/Basic.aspx?playerId=66108",
        bas,
    )

    result = normalize_kbo_hitter_splits(session, ingestion_run_id=run.id, snapshot_id=snapshot.id)

    assert result.rows_updated == 1
    refreshed = (
        session.query(PlayerStatSnapshotRow)
        .filter(PlayerStatSnapshotRow.player_id == player.id)
        .one()
    )
    assert "vs_lhp_ops" in refreshed.stats_json
    assert "risp_avg" in refreshed.stats_json
    assert refreshed.stats_json["OPS"] == 0.8
