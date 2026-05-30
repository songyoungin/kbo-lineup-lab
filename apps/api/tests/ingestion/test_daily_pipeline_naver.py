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
from app.models.snapshot import ActualLineupSnapshot, BoxScoreRow, BoxScoreSnapshot, StatSnapshot
from app.models.team import Team

FIXTURE_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "sources" / "naver"
SCHEDULE_JSON = (FIXTURE_DIR / "schedule_20250514.json").read_text(encoding="utf-8")
PREVIEW_JSON = (FIXTURE_DIR / "preview_20250514WOLG02025.json").read_text(encoding="utf-8")
RECORD_JSON = (FIXTURE_DIR / "record_20250514WOLG02025.json").read_text(encoding="utf-8")

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
