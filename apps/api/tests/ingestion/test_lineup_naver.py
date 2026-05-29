"""Tests for the Naver-preview-backed LG lineup collector and normalizer.

Covers:
- build_naver_preview_url maps a KBO game id to the api-gw preview endpoint
- collect_lg_lineup stores a payload and returns COLLECTED, sending a Referer
- normalize_lineup parses result.previewData.fullLineUp (LG side) into one
  snapshot with 9 rows, upserts Player rows with handedness, and is idempotent
- _parse_handedness unit cases

No real network connections are made; all HTTP uses httpx.MockTransport.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ingestion.collectors.lineup import (
    LineupStatus,
    build_naver_preview_url,
    collect_lg_lineup,
)
from app.ingestion.http_client import HttpClient
from app.ingestion.normalizers.lineup import _parse_handedness, normalize_lineup
from app.ingestion.raw_store import save_raw_payload
from app.ingestion.types import PayloadCategory
from app.models.game import Game
from app.models.player import Player
from app.models.snapshot import (
    ActualLineupSnapshot,
    ActualLineupSnapshotRow,
    IngestionRun,
    RawIngestionPayload,
)
from app.models.team import Team
from app.schemas.ingestion import RawPayloadCreate

MockHttpBuilder = Callable[[Callable[[httpx.Request], httpx.Response]], HttpClient]

KBO_GAME_ID = "20250514WOLG0"
NAVER_GAME_ID = "20250514WOLG02025"
PREVIEW_URL = f"https://api-gw.sports.naver.com/schedule/games/{NAVER_GAME_ID}/preview"


def _seed_teams(session: Session) -> tuple[Team, Team]:
    """Seed LG (home) and WO (away) teams and return them."""
    lg = Team(code="LG", name="LG")
    wo = Team(code="WO", name="Kiwoom")
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


# ---------------------------------------------------------------------------
# URL builder
# ---------------------------------------------------------------------------


def test_build_naver_preview_url_targets_api_gw() -> None:
    url = build_naver_preview_url(kbo_game_id=KBO_GAME_ID)
    assert url == PREVIEW_URL
    assert url.endswith(f"/schedule/games/{NAVER_GAME_ID}/preview")


# ---------------------------------------------------------------------------
# Collector
# ---------------------------------------------------------------------------


def test_collect_lg_lineup_stores_payload_and_sends_referer(
    session: Session,
    mock_http: MockHttpBuilder,
    load_source: Callable[[str], str],
) -> None:
    body = load_source("naver/preview_20250514WOLG02025.json")
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, text=body, headers={"content-type": "application/json"})

    run = IngestionRun(source="test:lineup", status="running")
    session.add(run)
    session.flush()

    result = collect_lg_lineup(
        session=session,
        ingestion_run=run,
        game_id=KBO_GAME_ID,
        http=mock_http(handler),
    )

    assert result.status == LineupStatus.COLLECTED
    assert result.raw_payload is not None
    assert result.raw_payload.source_name == "naver_sports"
    assert result.raw_payload.category == "lineup"
    assert len(seen) == 1
    assert seen[0].headers.get("Referer") == "https://m.sports.naver.com/"


def test_collect_lg_lineup_waiting_when_no_lineup(
    session: Session,
    mock_http: MockHttpBuilder,
) -> None:
    import json

    body = json.dumps(
        {
            "result": {
                "previewData": {
                    "homeTeamLineUp": {"fullLineUp": []},
                    "awayTeamLineUp": {"fullLineUp": []},
                }
            }
        }
    )

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=body, headers={"content-type": "application/json"})

    run = IngestionRun(source="test:lineup", status="running")
    session.add(run)
    session.flush()

    result = collect_lg_lineup(
        session=session,
        ingestion_run=run,
        game_id=KBO_GAME_ID,
        http=mock_http(handler),
    )

    assert result.status == LineupStatus.WAITING
    assert result.raw_payload is None


# ---------------------------------------------------------------------------
# Normalizer
# ---------------------------------------------------------------------------


def _save_preview_payload(
    session: Session,
    run: IngestionRun,
    load_source: Callable[[str], str],
) -> RawIngestionPayload:
    payload, _ = save_raw_payload(
        session,
        RawPayloadCreate(
            ingestion_run_id=run.id,
            category=PayloadCategory.LINEUP,
            source_name="naver_sports",
            source_url=PREVIEW_URL,
            fetched_at=datetime.now(UTC),
            content_type="application/json",
            raw_body=load_source("naver/preview_20250514WOLG02025.json"),
        ),
    )
    return payload


def test_normalize_lineup_creates_snapshot_with_nine_rows(
    session: Session,
    load_source: Callable[[str], str],
) -> None:
    lg, wo = _seed_teams(session)
    _seed_game(session, lg, wo)
    run = IngestionRun(source="test:lineup", status="running")
    session.add(run)
    session.flush()
    payload = _save_preview_payload(session, run, load_source)

    result = normalize_lineup(session, payload)

    # Exactly one snapshot for LG.
    snapshots = (
        session.execute(select(ActualLineupSnapshot).where(ActualLineupSnapshot.team_id == lg.id))
        .scalars()
        .all()
    )
    assert len(snapshots) == 1
    assert snapshots[0].id == result.snapshot_id

    # Nine batter rows.
    rows = (
        session.execute(
            select(ActualLineupSnapshotRow).where(
                ActualLineupSnapshotRow.snapshot_id == result.snapshot_id
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 9
    assert result.rows_created == 9

    # Players upserted (>=9, 10 including the starting pitcher).
    players = session.execute(select(Player)).scalars().all()
    assert len(players) >= 9

    # Handedness populated for a known batter ("박해민" / "62415", 우투좌타 -> bats L).
    park = session.execute(select(Player).where(Player.external_id == "62415")).scalar_one()
    assert park.name == "박해민"
    assert park.bats == "L"
    assert park.throws == "R"


def test_normalize_lineup_is_idempotent(
    session: Session,
    load_source: Callable[[str], str],
) -> None:
    lg, wo = _seed_teams(session)
    _seed_game(session, lg, wo)
    run = IngestionRun(source="test:lineup", status="running")
    session.add(run)
    session.flush()
    payload = _save_preview_payload(session, run, load_source)

    first = normalize_lineup(session, payload)
    second = normalize_lineup(session, payload)

    assert first.snapshot_id == second.snapshot_id
    assert second.rows_created == 0
    snapshots = session.execute(select(ActualLineupSnapshot)).scalars().all()
    assert len(snapshots) == 1


# ---------------------------------------------------------------------------
# Handedness parser unit tests
# ---------------------------------------------------------------------------


def test_parse_handedness_position_player() -> None:
    assert _parse_handedness("우투좌타", "좌타") == ("L", "R")


def test_parse_handedness_pitcher() -> None:
    assert _parse_handedness("좌완투수", "좌투") == (None, "L")


def test_parse_handedness_switch_hitter() -> None:
    assert _parse_handedness("우투양타", "양타") == ("S", "R")


def test_parse_handedness_falls_back_to_bats_throws() -> None:
    # Unknown hitType but bats_throws indicates a right-handed batter.
    assert _parse_handedness(None, "우타") == ("R", None)
    assert _parse_handedness("", "좌투") == (None, "L")


def test_parse_handedness_unknown() -> None:
    assert _parse_handedness(None, None) == (None, None)
