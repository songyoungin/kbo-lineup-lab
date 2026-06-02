"""Integration tests for opponent-starter quality calibration in the evaluator.

The multiplier is applied EQUALLY to both headline totals
(recommended_total_score / actual_total_score) without changing player
selection, batting order, or run.output_hash.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

import app.models  # noqa: F401 — registers all ORM models with Base.metadata
from app.db.base import Base
from app.lineup_model.pitcher_quality import matchup_difficulty_multiplier
from app.models.evaluation import (
    LineupEvaluationRun,
    LineupEvaluationSummary,
    RecommendedLineupRow,
)
from app.models.game import Game
from app.models.player import Player
from app.models.snapshot import (
    ActualLineupSnapshot,
    ActualLineupSnapshotRow,
    IngestionRun,
    PlayerStatSnapshotRow,
    RawIngestionPayload,
    StatSnapshot,
)
from app.services.lineup_evaluator import evaluate_lineup_for_run

_OPPONENT_CODE = "55322"
_FIXTURE = Path(__file__).parent / "fixtures" / "sources" / "kbo" / "pitcher_basic_55322.html"


@pytest.fixture
def session() -> Iterator[Session]:
    """In-memory SQLite session with the full ORM schema."""
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as s:
        yield s
    engine.dispose()


_POSITIONS = ["C", "1B", "2B", "3B", "SS", "LF", "CF", "RF", "DH"]


def _seed_evaluable_run(
    session: Session,
    *,
    team_id: int,
    game_id: int,
    opponent_starter_id: str | None,
    stat_snapshot_id: int,
    ingestion_run_id: int,
) -> LineupEvaluationRun:
    """Seed a Game + team players + stat/lineup snapshots → a pending run.

    Each run uses its own ``team_id`` (and a distinct player-id block) so that
    ``_load_recent_lineups`` (filtered by team) cannot cross-contaminate the
    no-payload baseline run with the calibrated run's lineup history.
    """
    cutoff = datetime(2026, 8, 1, 18, 0, tzinfo=UTC)
    base_pid = team_id * 100  # distinct player-id block per team

    for idx, pos in enumerate(_POSITIONS, start=1):
        pid = base_pid + idx
        session.add(
            Player(
                id=pid,
                team_id=team_id,
                external_id=f"player-{pid}",
                name=f"P{pid}",
                position=pos,
            )
        )
        session.add(
            PlayerStatSnapshotRow(
                snapshot_id=stat_snapshot_id,
                player_id=pid,
                stats_json={
                    "OPS": 0.800,
                    "OBP": 0.350,
                    "SLG": 0.450,
                    "primary_position": pos,
                    "handedness": "R",
                },
            )
        )

    session.add(
        Game(
            id=game_id,
            external_id=f"ext-{game_id}",
            home_team_id=team_id,
            away_team_id=99,
            game_date=date(2026, 8, 1),
            opponent_starter_id=opponent_starter_id,
        )
    )
    session.flush()

    # The actual lineup that was played (drives compute_actual_lineup_score).
    actual_snap = ActualLineupSnapshot(
        game_id=game_id,
        team_id=team_id,
        ingestion_run_id=ingestion_run_id,
        announced_at=cutoff - timedelta(hours=1),
        content_hash=f"actual-{game_id}",
    )
    session.add(actual_snap)
    session.flush()
    for order, pos in enumerate(_POSITIONS, start=1):
        session.add(
            ActualLineupSnapshotRow(
                snapshot_id=actual_snap.id,
                player_id=base_pid + order,
                batting_order=order,
                position=pos,
            )
        )
    session.flush()

    run = LineupEvaluationRun(
        game_id=game_id,
        team_id=team_id,
        model_version_id=1,
        stat_snapshot_id=stat_snapshot_id,
        lineup_snapshot_id=actual_snap.id,
        evaluation_cutoff_at=cutoff,
        status="pending",
    )
    session.add(run)
    session.flush()
    return run


def _seed_stat_snapshot(session: Session, *, snapshot_id: int, ingestion_run_id: int) -> None:
    """Persist a StatSnapshot so run.stat_snapshot_id resolves to an ingestion run."""
    session.add(
        StatSnapshot(
            id=snapshot_id,
            ingestion_run_id=ingestion_run_id,
            snapshot_at=datetime(2026, 8, 1, 12, 0, tzinfo=UTC),
            content_hash=f"snap-{snapshot_id}",
        )
    )
    session.flush()


def _seed_pitcher_payload(session: Session, *, ingestion_run_id: int, code: str) -> None:
    """Store the kbo_official PitcherDetail/Basic payload for ``code``."""
    session.add(
        RawIngestionPayload(
            ingestion_run_id=ingestion_run_id,
            category="player_stats",
            source_name="kbo_official",
            source_url=(
                f"https://www.koreabaseball.com/Record/Player/PitcherDetail/"
                f"Basic.aspx?playerId={code}"
            ),
            fetched_at=datetime(2026, 8, 1, 12, 0, tzinfo=UTC),
            content_type="text/html",
            payload_hash=f"hash-{code}",
            raw_body=_FIXTURE.read_text(),
        )
    )
    session.flush()


def _key_insights(session: Session, run: LineupEvaluationRun) -> dict[str, object]:
    summary = (
        session.query(LineupEvaluationSummary)
        .filter(LineupEvaluationSummary.evaluation_run_id == run.id)
        .one()
    )
    insights = summary.key_insights_json
    assert insights is not None
    return insights


def test_pitcher_multiplier_scales_both_totals_equally(session: Session) -> None:
    """With a PitcherDetail payload, both headline totals scale by the computed
    multiplier, while per-slot scores and the run's output_hash stay unmultiplied.

    The baseline (no payload) and calibrated runs use distinct teams + player-id
    blocks so lineup-history lookups (filtered by team) cannot cross-contaminate.
    Both teams carry identical stats, so the only difference in the headline
    totals is the multiplier itself.
    """
    ingest = IngestionRun(source="lineup", status="completed")
    session.add(ingest)
    session.flush()

    # Baseline run (team 1): no opponent starter, no pitcher payload → mult = 1.0.
    _seed_stat_snapshot(session, snapshot_id=9001, ingestion_run_id=ingest.id)
    base_run = _seed_evaluable_run(
        session,
        team_id=1,
        game_id=800,
        opponent_starter_id=None,
        stat_snapshot_id=9001,
        ingestion_run_id=ingest.id,
    )
    evaluate_lineup_for_run(session, run=base_run, opp_handedness=None)
    base_insights = _key_insights(session, base_run)
    base_recommended = float(base_insights["recommended_total_score"])  # type: ignore[arg-type]
    base_actual = float(base_insights["actual_total_score"])  # type: ignore[arg-type]

    # Calibrated run (team 2): opponent starter + matching payload → mult < 1.0.
    _seed_stat_snapshot(session, snapshot_id=9002, ingestion_run_id=ingest.id)
    _seed_pitcher_payload(session, ingestion_run_id=ingest.id, code=_OPPONENT_CODE)
    calib_run = _seed_evaluable_run(
        session,
        team_id=2,
        game_id=801,
        opponent_starter_id=_OPPONENT_CODE,
        stat_snapshot_id=9002,
        ingestion_run_id=ingest.id,
    )
    evaluate_lineup_for_run(session, run=calib_run, opp_handedness=None)
    calib_insights = _key_insights(session, calib_run)

    mult = matchup_difficulty_multiplier(era=3.18, whip=1.59)
    assert mult != pytest.approx(1.0)

    calib_recommended = float(calib_insights["recommended_total_score"])  # type: ignore[arg-type]
    calib_actual = float(calib_insights["actual_total_score"])  # type: ignore[arg-type]

    # Both headline totals scale by the same multiplier.
    assert calib_recommended == pytest.approx(base_recommended * mult)
    assert calib_actual == pytest.approx(base_actual * mult)

    opponent = calib_insights["opponent_pitcher"]
    assert isinstance(opponent, dict)
    assert opponent["multiplier"] == pytest.approx(mult)
    assert opponent["era"] == pytest.approx(3.18)
    assert opponent["whip"] == pytest.approx(1.59)
    assert opponent["k_pct"] == pytest.approx(14.0 / 52.0)

    # output_hash embeds the UNMULTIPLIED recommended total. Recompute the hash
    # from the persisted recommended rows + the unmultiplied total (= stored
    # headline / mult) and confirm it matches the run's output_hash, proving the
    # fingerprint is independent of the multiplier.
    rows = sorted(
        session.query(RecommendedLineupRow)
        .filter(RecommendedLineupRow.evaluation_run_id == calib_run.id)
        .all(),
        key=lambda r: r.batting_order or 0,
    )
    unmultiplied_total = calib_recommended / mult
    payload = {
        "slots": [
            {
                "batting_order": r.batting_order,
                "player_id": r.player_id,
                "position": str(r.position),
            }
            for r in rows
        ],
        "total_score": unmultiplied_total,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    expected_hash = hashlib.sha256(canonical.encode()).hexdigest()
    assert calib_run.output_hash == expected_hash


def test_no_pitcher_data_multiplier_is_one(session: Session) -> None:
    """No opponent_starter_id → mult = 1.0, totals unscaled, no opponent block.

    Compared against a structurally identical baseline run (distinct team) so the
    no-op multiplier leaves the headline totals exactly equal to the baseline.
    """
    ingest = IngestionRun(source="lineup", status="completed")
    session.add(ingest)
    session.flush()

    _seed_stat_snapshot(session, snapshot_id=9101, ingestion_run_id=ingest.id)
    base_run = _seed_evaluable_run(
        session,
        team_id=1,
        game_id=810,
        opponent_starter_id=None,
        stat_snapshot_id=9101,
        ingestion_run_id=ingest.id,
    )
    evaluate_lineup_for_run(session, run=base_run, opp_handedness=None)
    base_insights = _key_insights(session, base_run)

    _seed_stat_snapshot(session, snapshot_id=9102, ingestion_run_id=ingest.id)
    run = _seed_evaluable_run(
        session,
        team_id=2,
        game_id=811,
        opponent_starter_id=None,
        stat_snapshot_id=9102,
        ingestion_run_id=ingest.id,
    )
    evaluate_lineup_for_run(session, run=run, opp_handedness=None)
    insights = _key_insights(session, run)

    assert "opponent_pitcher" not in insights or insights["opponent_pitcher"] is None
    # mult = 1.0 → totals match the structurally identical baseline exactly.
    assert float(insights["recommended_total_score"]) == pytest.approx(  # type: ignore[arg-type]
        float(base_insights["recommended_total_score"])  # type: ignore[arg-type]
    )
    assert float(insights["actual_total_score"]) == pytest.approx(  # type: ignore[arg-type]
        float(base_insights["actual_total_score"])  # type: ignore[arg-type]
    )
