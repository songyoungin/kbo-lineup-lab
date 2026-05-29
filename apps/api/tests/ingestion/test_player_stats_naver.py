"""Tests for the Naver-preview-backed LG player stats collector and normalizer.

Covers:
- collect_lg_hitter_season_stats stores a PLAYER_STATS payload, sends a Referer
  header, and uses the Naver preview URL
- normalize_player_stats extracts homeTopPlayer (hitter) and homeStarter (pitcher)
  currentSeasonStats into a single StatSnapshot with 2 PlayerStatSnapshotRow rows
- Only Naver-provided fields are stored (obp present, slg/ops/wrcPlus/woba absent)
- Second normalize call is idempotent (rows_created=0, still exactly 1 StatSnapshot)

No real network connections are made; all HTTP uses httpx.MockTransport.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ingestion.collectors.player_stats import (
    NAVER_SOURCE_NAME,
    collect_lg_hitter_season_stats,
)
from app.ingestion.http_client import HttpClient
from app.ingestion.normalizers.player_stats import normalize_player_stats
from app.ingestion.raw_store import save_raw_payload
from app.ingestion.types import PayloadCategory
from app.models.game import Game
from app.models.player import Player
from app.models.snapshot import (
    IngestionRun,
    PlayerStatSnapshotRow,
    RawIngestionPayload,
    StatSnapshot,
)
from app.models.team import Team
from app.schemas.ingestion import RawPayloadCreate

MockHttpBuilder = Callable[[Callable[[httpx.Request], httpx.Response]], HttpClient]

KBO_GAME_ID = "20250514WOLG0"
NAVER_GAME_ID = "20250514WOLG02025"
PREVIEW_URL = f"https://api-gw.sports.naver.com/schedule/games/{NAVER_GAME_ID}/preview"

# Player external IDs from fixture
HITTER_EXTERNAL_ID = "68119"  # 문성주 (homeTopPlayer)
PITCHER_EXTERNAL_ID = "51111"  # 송승기 (homeStarter)


def _seed_teams(session: Session) -> tuple[Team, Team]:
    """Seed LG (home) and WO (away) teams and return them."""
    lg = Team(code="LG", name="LG Twins")
    wo = Team(code="WO", name="Kiwoom Heroes")
    session.add_all([lg, wo])
    session.flush()
    return lg, wo


def _seed_game(session: Session, lg: Team, wo: Team) -> Game:
    """Seed the LG (home) vs WO (away) game for the fixture."""
    game = Game(
        external_id=KBO_GAME_ID,
        home_team_id=lg.id,
        away_team_id=wo.id,
        game_date=date(2025, 5, 14),
    )
    session.add(game)
    session.flush()
    return game


def _seed_lg_players(session: Session, lg: Team) -> tuple[Player, Player]:
    """Seed the LG hitter and pitcher referenced by the fixture."""
    hitter = Player(
        team_id=lg.id,
        external_id=HITTER_EXTERNAL_ID,
        name="문성주",
        position="7",
    )
    pitcher = Player(
        team_id=lg.id,
        external_id=PITCHER_EXTERNAL_ID,
        name="송승기",
        position="1",
    )
    session.add_all([hitter, pitcher])
    session.flush()
    return hitter, pitcher


def _save_preview_payload(
    session: Session,
    run: IngestionRun,
    load_source: Callable[[str], str],
) -> RawIngestionPayload:
    """Save the fixture as a PLAYER_STATS raw payload and return it."""
    payload, _ = save_raw_payload(
        session,
        RawPayloadCreate(
            ingestion_run_id=run.id,
            category=PayloadCategory.PLAYER_STATS,
            source_name="naver_sports",
            source_url=PREVIEW_URL,
            fetched_at=datetime.now(UTC),
            content_type="application/json",
            raw_body=load_source("naver/preview_20250514WOLG02025.json"),
        ),
    )
    return payload


# ---------------------------------------------------------------------------
# Collector tests
# ---------------------------------------------------------------------------


def test_collect_lg_hitter_season_stats_stores_payload_and_sends_referer(
    session: Session,
    mock_http: MockHttpBuilder,
    load_source: Callable[[str], str],
) -> None:
    """Collector fetches the preview, sends Referer, and stores a PLAYER_STATS payload."""
    body = load_source("naver/preview_20250514WOLG02025.json")
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, text=body, headers={"content-type": "application/json"})

    run = IngestionRun(source="test:player_stats", status="running")
    session.add(run)
    session.flush()

    payload, created = collect_lg_hitter_season_stats(
        session=session,
        ingestion_run=run,
        game_id=KBO_GAME_ID,
        http=mock_http(handler),
    )

    assert created is True
    assert len(seen) == 1
    assert seen[0].headers.get("Referer") == "https://m.sports.naver.com/"
    assert payload.source_name == NAVER_SOURCE_NAME
    assert payload.source_name == "naver_sports"
    assert payload.category == PayloadCategory.PLAYER_STATS.value


# ---------------------------------------------------------------------------
# Normalizer tests
# ---------------------------------------------------------------------------


def test_normalize_player_stats_creates_snapshot_with_two_rows(
    session: Session,
    load_source: Callable[[str], str],
) -> None:
    """normalize_player_stats extracts hitter + pitcher stats into 2 snapshot rows."""
    lg, wo = _seed_teams(session)
    _seed_game(session, lg, wo)
    hitter, pitcher = _seed_lg_players(session, lg)

    run = IngestionRun(source="test:player_stats", status="running")
    session.add(run)
    session.flush()
    payload = _save_preview_payload(session, run, load_source)

    result = normalize_player_stats(session, payload)

    # Exactly one StatSnapshot
    snapshots = session.execute(select(StatSnapshot)).scalars().all()
    assert len(snapshots) == 1
    assert snapshots[0].id == result.snapshot_id

    assert result.rows_created == 2

    # Hitter row: obp present, no advanced metrics
    hitter_row = session.execute(
        select(PlayerStatSnapshotRow).where(
            PlayerStatSnapshotRow.snapshot_id == result.snapshot_id,
            PlayerStatSnapshotRow.player_id == hitter.id,
        )
    ).scalar_one()
    hitter_stats = hitter_row.stats_json
    assert "obp" in hitter_stats
    assert "slg" not in hitter_stats
    assert "ops" not in hitter_stats
    assert "wrcPlus" not in hitter_stats
    assert "woba" not in hitter_stats

    # Pitcher row: era and whip present
    pitcher_row = session.execute(
        select(PlayerStatSnapshotRow).where(
            PlayerStatSnapshotRow.snapshot_id == result.snapshot_id,
            PlayerStatSnapshotRow.player_id == pitcher.id,
        )
    ).scalar_one()
    pitcher_stats = pitcher_row.stats_json
    assert "era" in pitcher_stats
    assert "whip" in pitcher_stats


def test_normalize_player_stats_snapshot_at_derived_from_game_date(
    session: Session,
    load_source: Callable[[str], str],
) -> None:
    """snapshot_at is derived from gameInfo.gdate/gtime as KST->UTC."""
    lg, wo = _seed_teams(session)
    _seed_game(session, lg, wo)
    _seed_lg_players(session, lg)

    run = IngestionRun(source="test:player_stats", status="running")
    session.add(run)
    session.flush()
    payload = _save_preview_payload(session, run, load_source)

    result = normalize_player_stats(session, payload)

    snapshot = session.get(StatSnapshot, result.snapshot_id)
    assert snapshot is not None
    # gdate=20250514 gtime=18:30 KST => 09:30 UTC
    expected = datetime(2025, 5, 14, 9, 30, tzinfo=UTC)
    snap_at = snapshot.snapshot_at
    if snap_at.tzinfo is None:
        assert snap_at == expected.replace(tzinfo=None)
    else:
        from app.util.time import to_utc

        assert to_utc(snap_at) == expected


def test_normalize_player_stats_is_idempotent(
    session: Session,
    load_source: Callable[[str], str],
) -> None:
    """Second normalize_player_stats call returns rows_created=0 with no new snapshot."""
    lg, wo = _seed_teams(session)
    _seed_game(session, lg, wo)
    _seed_lg_players(session, lg)

    run = IngestionRun(source="test:player_stats", status="running")
    session.add(run)
    session.flush()
    payload = _save_preview_payload(session, run, load_source)

    first = normalize_player_stats(session, payload)
    second = normalize_player_stats(session, payload)

    assert first.snapshot_id == second.snapshot_id
    assert second.rows_created == 0

    snapshots = session.execute(select(StatSnapshot)).scalars().all()
    assert len(snapshots) == 1
