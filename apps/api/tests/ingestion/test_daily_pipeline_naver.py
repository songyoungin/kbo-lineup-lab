"""End-to-end daily pipeline test against captured Naver fixtures.

Validates:
- A full run for 2025-05-14 ingests the single LG game (schedule -> lineup +
  player stats from the shared preview payload -> box score) and reports
  status='completed' with games_found == 1.
- Exactly one Game, ActualLineupSnapshot, StatSnapshot, and BoxScoreSnapshot
  are persisted.
- Re-running the same date is a no-op (completed-run short-circuit): same
  ingestion_run_id, status='completed', and no duplicate snapshots.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import date
from pathlib import Path

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ingestion.http_client import HttpClient
from app.jobs.daily_pipeline import run_daily_pipeline
from app.models.game import Game
from app.models.snapshot import (
    ActualLineupSnapshot,
    BoxScoreRow,
    BoxScoreSnapshot,
    PlayerStatSnapshotRow,
    StatSnapshot,
)
from app.models.team import Team

FIXTURE_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "sources" / "naver"
SCHEDULE_JSON = (FIXTURE_DIR / "schedule_20250514.json").read_text(encoding="utf-8")
PREVIEW_JSON = (FIXTURE_DIR / "preview_20250514WOLG02025.json").read_text(encoding="utf-8")
RECORD_JSON = (FIXTURE_DIR / "record_20250514WOLG02025.json").read_text(encoding="utf-8")

# Captured per-player season record used as a template: the handler clones it and
# substitutes the requested player code so every lineup batter resolves to a row
# (only 62415 and 69102 have real captures, but the test asserts row counts and
# numeric types, not per-player values).
_PLAYER_TEMPLATE = json.loads(
    (FIXTURE_DIR / "player_season_62415.json").read_text(encoding="utf-8")
)


def _player_season_body(code: str) -> str:
    """Return a synthetic but valid per-player season payload for ``code``."""
    body = json.loads(json.dumps(_PLAYER_TEMPLATE))  # deep copy
    body["result"]["playerId"] = code
    # record is a JSON-encoded string; rewrite each season row's pcode to code.
    record = json.loads(body["result"]["record"])
    for row in record.get("season", []):
        if isinstance(row, dict):
            row["pcode"] = code
    body["result"]["record"] = json.dumps(record, ensure_ascii=False)
    return json.dumps(body, ensure_ascii=False)


SessionFactory = Callable[[], AbstractContextManager[Session]]


@pytest.fixture
def session_factory(session: Session) -> SessionFactory:
    """Session factory that yields the single shared session to the pipeline."""

    class _ContextSession(AbstractContextManager[Session]):
        def __enter__(self) -> Session:
            return session

        def __exit__(self, *args: object) -> None:
            pass

    class _Factory:
        def __call__(self) -> _ContextSession:
            return _ContextSession()

    return _Factory()


def _make_naver_mock_http() -> HttpClient:
    """Build an HttpClient backed by a MockTransport routing the Naver fixtures."""

    def handler(request: httpx.Request) -> httpx.Response:
        u = str(request.url)
        if "/schedule/games?" in u:
            body = SCHEDULE_JSON
        elif u.endswith("/preview"):
            body = PREVIEW_JSON
        elif u.endswith("/record"):
            body = RECORD_JSON
        else:
            match = re.search(r"/players/kbo/([^/]+)/playerend-record", u)
            if match is not None:
                return httpx.Response(
                    200,
                    text=_player_season_body(match.group(1)),
                    headers={"content-type": "application/json"},
                )
            return httpx.Response(404, text="nf")
        return httpx.Response(200, text=body, headers={"content-type": "application/json"})

    transport = httpx.MockTransport(handler)
    return HttpClient(client=httpx.Client(transport=transport), retry_backoff=(0.0,))


def _seed_teams(session: Session) -> None:
    session.add(Team(code="LG", name="LG 트윈스"))
    session.add(Team(code="WO", name="키움 히어로즈"))
    session.commit()


def test_daily_pipeline_naver_end_to_end(session: Session, session_factory: SessionFactory) -> None:
    """A full run ingests the single LG game and its lineup/stats/box snapshots."""
    _seed_teams(session)

    result = run_daily_pipeline(
        target_date=date(2025, 5, 14),
        session_factory=session_factory,
        http=_make_naver_mock_http(),
    )

    assert result.status == "completed"
    assert result.games_found == 1

    games = list(session.execute(select(Game)).scalars())
    assert len(games) == 1
    assert games[0].external_id == "20250514WOLG0"

    assert session.execute(select(func.count()).select_from(ActualLineupSnapshot)).scalar() == 1
    assert len(session.execute(select(StatSnapshot)).scalars().all()) == 1
    assert session.execute(select(func.count()).select_from(BoxScoreSnapshot)).scalar() == 1

    # One stat row per available team hitter: 9 starters + 6 batterCandidate
    # bench hitters = 15 (zero overlap in the 2025-05-14 fixture). The starting
    # pitcher 51111 is position "P" and is excluded.
    assert result.stat_snapshots_created == 1
    assert session.execute(select(func.count()).select_from(PlayerStatSnapshotRow)).scalar() == 15

    # Box-score rows: the normalizer upserts box-only substitutes.  The fixture
    # has 16 LG batters in the box score; 9 also appear in the lineup upsert (the
    # 10th lineup player, pitcher 51111, never bats) and the remaining 7 box-only
    # substitutes are now upserted as Players rather than skipped.
    assert session.execute(select(func.count()).select_from(BoxScoreRow)).scalar() == 16


def test_daily_pipeline_naver_is_idempotent(
    session: Session, session_factory: SessionFactory
) -> None:
    """A second run for the same date is a no-op short-circuit with no duplicates."""
    _seed_teams(session)

    result1 = run_daily_pipeline(
        target_date=date(2025, 5, 14),
        session_factory=session_factory,
        http=_make_naver_mock_http(),
    )
    result2 = run_daily_pipeline(
        target_date=date(2025, 5, 14),
        session_factory=session_factory,
        http=_make_naver_mock_http(),
    )

    # The completed-run short-circuit makes the second call a no-op.
    assert result2.ingestion_run_id == result1.ingestion_run_id
    assert result2.status == "completed"
    assert not result2.schedule_created
    assert result2.games_found == 0

    assert session.execute(select(func.count()).select_from(Game)).scalar() == 1
    assert session.execute(select(func.count()).select_from(ActualLineupSnapshot)).scalar() == 1
    assert len(session.execute(select(StatSnapshot)).scalars().all()) == 1
    assert session.execute(select(func.count()).select_from(BoxScoreSnapshot)).scalar() == 1
    assert session.execute(select(func.count()).select_from(BoxScoreRow)).scalar() == 16
    # No duplicate stat rows on the short-circuit second run (15 hitters).
    assert session.execute(select(func.count()).select_from(PlayerStatSnapshotRow)).scalar() == 15


def test_collect_roster_player_season_stats_covers_hitters_not_just_lineup(
    session: Session,
) -> None:
    """The roster collector fetches every team hitter and excludes pitchers."""
    from app.jobs.daily_pipeline import _collect_roster_player_season_stats
    from app.models.player import Player
    from app.models.snapshot import IngestionRun

    lg = Team(code="LG", name="LG")
    session.add(lg)
    session.flush()
    session.add_all(
        [
            Player(team_id=lg.id, external_id="100", name="hitter1", position="CF"),
            Player(team_id=lg.id, external_id="101", name="hitter2", position="DH"),
            Player(team_id=lg.id, external_id="900", name="pitcher1", position="P"),
        ]
    )
    run = IngestionRun(source="test:roster-stats", status="running")
    session.add(run)
    session.flush()

    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        match = re.search(r"/players/kbo/([^/]+)/playerend-record", str(request.url))
        assert match is not None
        seen.append(match.group(1))
        return httpx.Response(200, text="{}", headers={"content-type": "application/json"})

    http = HttpClient(
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        retry_backoff=(0.0,),
    )

    count = _collect_roster_player_season_stats(
        session, ingestion_run=run, team_id=lg.id, http=http
    )

    assert count == 2
    assert set(seen) == {"100", "101"}  # pitcher 900 excluded
