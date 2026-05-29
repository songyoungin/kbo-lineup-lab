"""Tests for the Naver-record-backed LG box score collector and normalizer.

Covers:
- build_naver_record_url maps a KBO game id to the api-gw record endpoint
- collect_lg_box_score stores a payload and returns COLLECTED, sending a Referer,
  parsing the final score from scoreBoard.rheb
- collect_lg_box_score returns WAITING when the box score data is absent
- normalize_box_score parses result.recordData.battersBoxscore (LG side) into one
  BoxScoreSnapshot with one row per batter, matched via match_player
- normalize_box_score skips unknown players, is idempotent, and gates on box-score
  data presence (skipped_not_final)

No real network connections are made; all HTTP uses httpx.MockTransport.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, date, datetime

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ingestion.collectors.box_score import (
    BoxScoreStatus,
    FinalScore,
    build_naver_record_url,
    collect_lg_box_score,
)
from app.ingestion.http_client import HttpClient
from app.ingestion.normalizers.box_score import normalize_box_score
from app.ingestion.raw_store import save_raw_payload
from app.ingestion.types import PayloadCategory
from app.models.game import Game
from app.models.player import Player
from app.models.snapshot import (
    BoxScoreRow,
    BoxScoreSnapshot,
    IngestionRun,
    RawIngestionPayload,
)
from app.models.team import Team
from app.schemas.ingestion import RawPayloadCreate
from app.util.time import to_utc

MockHttpBuilder = Callable[[Callable[[httpx.Request], httpx.Response]], HttpClient]

KBO_GAME_ID = "20250514WOLG0"
NAVER_GAME_ID = "20250514WOLG02025"
RECORD_URL = f"https://api-gw.sports.naver.com/schedule/games/{NAVER_GAME_ID}/record"
FIXTURE = "naver/record_20250514WOLG02025.json"


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


def _seed_lg_batters(session: Session, lg: Team, load_source: Callable[[str], str]) -> int:
    """Seed a Player for every distinct LG home batter in the fixture.

    Returns the count of distinct LG batters seeded.
    """
    body = json.loads(load_source(FIXTURE))
    home_batters = body["result"]["recordData"]["battersBoxscore"]["home"]
    for entry in home_batters:
        session.add(
            Player(
                team_id=lg.id,
                external_id=str(entry["playerCode"]),
                name=str(entry["name"]),
                position=str(entry.get("pos") or "?"),
            )
        )
    session.flush()
    return len(home_batters)


def _save_record_payload(
    session: Session,
    run: IngestionRun,
    raw_body: str,
) -> RawIngestionPayload:
    """Persist a record payload via save_raw_payload and return the row."""
    payload, _ = save_raw_payload(
        session,
        RawPayloadCreate(
            ingestion_run_id=run.id,
            category=PayloadCategory.BOX_SCORE,
            source_name="naver_sports",
            source_url=RECORD_URL,
            fetched_at=datetime.now(UTC),
            content_type="application/json",
            raw_body=raw_body,
        ),
    )
    return payload


# ---------------------------------------------------------------------------
# URL builder
# ---------------------------------------------------------------------------


def test_build_naver_record_url_targets_api_gw() -> None:
    url = build_naver_record_url(kbo_game_id=KBO_GAME_ID)
    assert url == RECORD_URL
    assert url.endswith(f"/schedule/games/{NAVER_GAME_ID}/record")


# ---------------------------------------------------------------------------
# Collector
# ---------------------------------------------------------------------------


def test_collect_lg_box_score_collected_with_final_score(
    session: Session,
    mock_http: MockHttpBuilder,
    load_source: Callable[[str], str],
) -> None:
    body = load_source(FIXTURE)
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, text=body, headers={"content-type": "application/json"})

    run = IngestionRun(source="test:box_score", status="running")
    session.add(run)
    session.flush()

    result = collect_lg_box_score(
        session=session,
        ingestion_run=run,
        game_id=KBO_GAME_ID,
        http=mock_http(handler),
    )

    assert result.status == BoxScoreStatus.COLLECTED
    assert result.raw_payload is not None
    assert result.raw_payload.source_name == "naver_sports"
    assert result.raw_payload.category == "box_score"
    assert result.created is True
    assert result.final_score == FinalScore(home_runs=12, away_runs=0)
    assert len(seen) == 1
    assert seen[0].headers.get("Referer") == "https://m.sports.naver.com/"


def test_collect_lg_box_score_waiting_when_no_box_score(
    session: Session,
    mock_http: MockHttpBuilder,
) -> None:
    body = json.dumps({"result": {"recordData": {"battersBoxscore": {"home": [], "away": []}}}})

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=body, headers={"content-type": "application/json"})

    run = IngestionRun(source="test:box_score", status="running")
    session.add(run)
    session.flush()

    result = collect_lg_box_score(
        session=session,
        ingestion_run=run,
        game_id=KBO_GAME_ID,
        http=mock_http(handler),
    )

    assert result.status == BoxScoreStatus.WAITING
    assert result.raw_payload is None
    assert result.created is False


# ---------------------------------------------------------------------------
# Normalizer
# ---------------------------------------------------------------------------


def test_normalize_box_score_creates_snapshot_and_rows(
    session: Session,
    load_source: Callable[[str], str],
) -> None:
    lg, wo = _seed_teams(session)
    _seed_game(session, lg, wo)
    batter_count = _seed_lg_batters(session, lg, load_source)
    run = IngestionRun(source="test:box_score", status="running")
    session.add(run)
    session.flush()
    payload = _save_record_payload(session, run, load_source(FIXTURE))

    result = normalize_box_score(session, payload)

    snapshots = session.execute(select(BoxScoreSnapshot)).scalars().all()
    assert len(snapshots) == 1
    assert snapshots[0].id == result.snapshot_id
    assert result.skipped_not_final is False
    assert result.rows_created == batter_count == 16

    # taken_at derives from gdate=20250514 gtime=18:30 KST -> 09:30 UTC.
    expected_taken_at = datetime(2025, 5, 14, 9, 30, tzinfo=UTC)
    taken_at = snapshots[0].taken_at
    if taken_at.tzinfo is None:
        assert taken_at == expected_taken_at.replace(tzinfo=None)
    else:
        assert to_utc(taken_at) == expected_taken_at

    # 박해민 (62415): ab=4, hit=2, run=2, rbi=2, with hr/bb/sb in extra_stats_json.
    park = session.execute(select(Player).where(Player.external_id == "62415")).scalar_one()
    park_row = session.execute(
        select(BoxScoreRow).where(
            BoxScoreRow.snapshot_id == result.snapshot_id,
            BoxScoreRow.player_id == park.id,
        )
    ).scalar_one()
    assert park_row.at_bats == 4
    assert park_row.hits == 2
    assert park_row.runs == 2
    assert park_row.rbis == 2
    assert park_row.extra_stats_json is not None
    assert "hr" in park_row.extra_stats_json
    assert "bb" in park_row.extra_stats_json
    assert "sb" in park_row.extra_stats_json


def test_normalize_box_score_skips_unknown_players(
    session: Session,
    load_source: Callable[[str], str],
) -> None:
    lg, wo = _seed_teams(session)
    _seed_game(session, lg, wo)
    # No players seeded.
    run = IngestionRun(source="test:box_score", status="running")
    session.add(run)
    session.flush()
    payload = _save_record_payload(session, run, load_source(FIXTURE))

    result = normalize_box_score(session, payload)

    assert result.rows_created == 0
    assert result.rows_skipped == 16
    assert result.needs_review_reasons
    assert result.snapshot_id is not None
    snapshots = session.execute(select(BoxScoreSnapshot)).scalars().all()
    assert len(snapshots) == 1


def test_normalize_box_score_is_idempotent(
    session: Session,
    load_source: Callable[[str], str],
) -> None:
    lg, wo = _seed_teams(session)
    _seed_game(session, lg, wo)
    _seed_lg_batters(session, lg, load_source)
    run = IngestionRun(source="test:box_score", status="running")
    session.add(run)
    session.flush()
    payload = _save_record_payload(session, run, load_source(FIXTURE))

    first = normalize_box_score(session, payload)
    second = normalize_box_score(session, payload)

    assert first.snapshot_id == second.snapshot_id
    assert second.rows_created == 0
    snapshots = session.execute(select(BoxScoreSnapshot)).scalars().all()
    assert len(snapshots) == 1


def test_normalize_box_score_skips_when_not_final(
    session: Session,
) -> None:
    lg, wo = _seed_teams(session)
    _seed_game(session, lg, wo)
    run = IngestionRun(source="test:box_score", status="running")
    session.add(run)
    session.flush()
    body = json.dumps(
        {
            "result": {
                "recordData": {
                    "gameInfo": {"hCode": "LG", "aCode": "WO", "gdate": 20250514, "gtime": "18:30"},
                    "battersBoxscore": {"home": [], "away": []},
                }
            }
        }
    )
    payload = _save_record_payload(session, run, body)

    result = normalize_box_score(session, payload)

    assert result.skipped_not_final is True
    assert result.snapshot_id is None
    snapshots = session.execute(select(BoxScoreSnapshot)).scalars().all()
    assert len(snapshots) == 0
