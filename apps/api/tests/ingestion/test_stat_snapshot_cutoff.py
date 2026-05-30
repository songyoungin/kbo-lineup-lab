"""Regression: a stat snapshot produced by normalize_player_stats must be
selectable by the cutoff-safe selector using a same-day lineup cutoff.

The bug: normalize_player_stats stored ``snapshot_at`` as a noon-KST wall-clock
value without normalizing to UTC, while select_stat_snapshot compares against a
UTC-normalized cutoff. On SQLite (which strips tzinfo and compares wall-clock
text) the stored ``12:00`` (KST) sorted after a ``09:30`` UTC cutoff, so the
evaluation step failed with SnapshotNotFoundError even though the snapshot was
clearly before the game's lineup announcement. The fix stores ``snapshot_at`` in
UTC like every other timestamp.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.ingestion.normalizers.player_stats import normalize_player_stats
from app.ingestion.raw_store import save_raw_payload
from app.ingestion.types import PayloadCategory
from app.models.game import Game
from app.models.player import Player
from app.models.snapshot import IngestionRun
from app.models.team import Team
from app.schemas.ingestion import RawPayloadCreate
from app.services.snapshot_selector import select_stat_snapshot

_KST = timezone(timedelta(hours=9))


def test_stat_snapshot_is_selectable_by_lineup_cutoff(
    session: Session, load_source: Callable[[str], str]
) -> None:
    """normalize_player_stats output is found by a same-day KST-evening cutoff."""
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

    run = IngestionRun(source="test:cutoff", status="running")
    session.add(run)
    session.flush()
    save_raw_payload(
        session,
        RawPayloadCreate(
            ingestion_run_id=run.id,
            category=PayloadCategory.PLAYER_STATS,
            source_name="naver_sports",
            source_url="https://api-gw.sports.naver.com/players/kbo/62415/playerend-record",
            fetched_at=datetime.now(UTC),
            content_type="application/json",
            raw_body=load_source("naver/player_season_62415.json"),
        ),
    )

    result = normalize_player_stats(
        session, game_external_id="20250514WOLG0", ingestion_run_id=run.id
    )
    assert result.rows_created == 1

    # Lineup announced 18:30 KST (= 09:30 UTC) on game day; the noon-KST stat
    # snapshot (= 03:00 UTC) must sort at-or-before this cutoff.
    cutoff = datetime(2025, 5, 14, 18, 30, tzinfo=_KST)
    snapshot = select_stat_snapshot(session, team_id=lg.id, cutoff_at=cutoff)
    assert snapshot.id == result.snapshot_id
