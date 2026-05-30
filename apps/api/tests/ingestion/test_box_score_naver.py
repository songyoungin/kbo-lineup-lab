"""Tests for the Naver-record-backed LG box score collector and normalizer.

Covers:
- build_naver_record_url maps a KBO game id to the api-gw record endpoint
- collect_lg_box_score stores a payload and returns COLLECTED, sending a Referer,
  parsing the final score from scoreBoard.rheb
- collect_lg_box_score returns WAITING when the box score data is absent
- normalize_box_score parses result.recordData.battersBoxscore (LG side) into one
  BoxScoreSnapshot with one row per batter, upserting box-only substitutes as
  Players (team=LG, position from the box `pos` token)
- normalize_box_score skips batters lacking a playerCode, is idempotent, and gates
  on box-score data presence (skipped_not_final)

No real network connections are made; all HTTP uses httpx.MockTransport.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, date, datetime
from typing import Final

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


# The 9 announced-lineup starters (also upserted by the lineup normalizer). The
# remaining 7 LG box batters are box-only substitutes the normalizer must upsert.
_STARTER_PLAYER_CODES: Final = (
    "62415",
    "68119",
    "53123",
    "69102",
    "76290",
    "79109",
    "68110",
    "69100",
    "52154",
)


def _seed_lg_starters(session: Session, lg: Team, load_source: Callable[[str], str]) -> int:
    """Seed a Player only for the 9 announced-lineup starters in the fixture.

    Box-only substitutes are intentionally left unseeded so the normalizer must
    upsert them. Returns the count of starters seeded.
    """
    body = json.loads(load_source(FIXTURE))
    home_batters = body["result"]["recordData"]["battersBoxscore"]["home"]
    by_code = {str(entry["playerCode"]): entry for entry in home_batters}
    for code in _STARTER_PLAYER_CODES:
        entry = by_code[code]
        session.add(
            Player(
                team_id=lg.id,
                external_id=code,
                name=str(entry["name"]),
                position=str(entry.get("pos") or "?"),
            )
        )
    session.flush()
    return len(_STARTER_PLAYER_CODES)


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
    # Seed only the 9 announced-lineup starters; the 7 box-only substitutes must
    # be upserted by the normalizer.
    starter_count = _seed_lg_starters(session, lg, load_source)
    assert starter_count == 9
    players_before = session.query(Player).count()
    run = IngestionRun(source="test:box_score", status="running")
    session.add(run)
    session.flush()
    payload = _save_record_payload(session, run, load_source(FIXTURE))

    result = normalize_box_score(session, payload)

    snapshots = session.execute(select(BoxScoreSnapshot)).scalars().all()
    assert len(snapshots) == 1
    assert snapshots[0].id == result.snapshot_id
    assert result.skipped_not_final is False
    # 9 matched starters + 7 upserted substitutes.
    assert result.rows_created == 16
    assert result.rows_skipped == 0
    assert session.query(BoxScoreRow).count() == 16
    # The 7 box-only substitutes were upserted as Players.
    assert session.query(Player).count() == players_before + 7

    # Substitute upsert canonicalizes the box `pos` token: 함창건 (50108, pos="타우")
    # is a messy multi-position token -> DH; 김성우 (52105, pos="포") -> C.
    ham = session.execute(select(Player).where(Player.external_id == "50108")).scalar_one()
    assert ham.name == "함창건"
    assert ham.position == "DH"
    kim = session.execute(select(Player).where(Player.external_id == "52105")).scalar_one()
    assert kim.position == "C"

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


def test_normalize_box_score_upserts_all_when_no_players_seeded(
    session: Session,
    load_source: Callable[[str], str],
) -> None:
    lg, wo = _seed_teams(session)
    _seed_game(session, lg, wo)
    # No players seeded: every LG batter must be upserted.
    run = IngestionRun(source="test:box_score", status="running")
    session.add(run)
    session.flush()
    payload = _save_record_payload(session, run, load_source(FIXTURE))

    result = normalize_box_score(session, payload)

    assert result.rows_created == 16
    assert result.rows_skipped == 0
    assert session.query(Player).count() == 16
    snapshots = session.execute(select(BoxScoreSnapshot)).scalars().all()
    assert len(snapshots) == 1


def test_normalize_box_score_skips_batter_missing_player_code(
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
                    "battersBoxscore": {
                        "home": [{"name": "이름없음", "pos": "중", "ab": 3, "hit": 1}],
                        "away": [],
                    },
                }
            }
        }
    )
    payload = _save_record_payload(session, run, body)

    result = normalize_box_score(session, payload)

    assert result.rows_created == 0
    assert result.rows_skipped == 1
    assert result.needs_review_reasons
    assert session.query(Player).count() == 0


def test_normalize_box_score_is_idempotent(
    session: Session,
    load_source: Callable[[str], str],
) -> None:
    lg, wo = _seed_teams(session)
    _seed_game(session, lg, wo)
    _seed_lg_starters(session, lg, load_source)
    run = IngestionRun(source="test:box_score", status="running")
    session.add(run)
    session.flush()
    payload = _save_record_payload(session, run, load_source(FIXTURE))

    first = normalize_box_score(session, payload)
    players_after_first = session.query(Player).count()
    second = normalize_box_score(session, payload)

    assert first.snapshot_id == second.snapshot_id
    assert second.rows_created == 0
    snapshots = session.execute(select(BoxScoreSnapshot)).scalars().all()
    assert len(snapshots) == 1
    # The content_hash early-return prevents re-upserting players.
    assert session.query(Player).count() == players_after_first


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
