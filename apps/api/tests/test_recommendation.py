"""Tests for the recommendation generator and end-to-end lineup evaluation.

Covers:
- Deterministic output: two calls with identical inputs produce identical results.
- Impossible-position blocking: catcher-ineligible player never lands at C.
- Full recommendation: 9 distinct players with batting_order 1–9.
- DB persistence via evaluate_lineup_for_run:
  - recommended_lineup_rows count == 9
  - lineup_evaluation_summaries has 1 row
  - run.status == 'completed'
  - run.output_hash is set
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

import app.models  # noqa: F401 — registers all ORM models with Base.metadata
from app.db.base import Base
from app.lineup_model.recommendation import generate_recommendation, select_and_assign_positions
from app.lineup_model.types import Handedness, HitterStats, Position
from app.models.evaluation import (
    LineupEvaluationRun,
    LineupEvaluationSummary,
    ModelVersion,
    RecommendedLineupRow,
)
from app.models.game import Game
from app.models.team import Team
from app.services.fixture_loader import load_fixture_file
from app.services.lineup_evaluator import evaluate_lineup_for_run
from app.services.snapshot_selector import select_lineup_snapshot, select_stat_snapshot

# ---------------------------------------------------------------------------
# Fixtures / session setup
# ---------------------------------------------------------------------------

FIXTURE_PATH = Path(__file__).parent.parent / "fixtures" / "lg_2026_sample.json"


@pytest.fixture
def session() -> Iterator[Session]:
    """In-memory SQLite session with full schema."""
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as s:
        yield s
    engine.dispose()


# ---------------------------------------------------------------------------
# Minimal player pool helpers (no DB required)
# ---------------------------------------------------------------------------


def _make_pool() -> list[HitterStats]:
    """Build a minimal 9-player pool covering all 9 defensive positions."""
    positions = [
        Position.C,
        Position.FIRST,
        Position.SECOND,
        Position.THIRD,
        Position.SHORT,
        Position.LEFT,
        Position.CENTER,
        Position.RIGHT,
        Position.DH,
    ]
    players = []
    for i, pos in enumerate(positions):
        players.append(
            HitterStats(
                player_id=i + 1,
                handedness=Handedness.RIGHT if i % 2 == 0 else Handedness.LEFT,
                ops=0.750 + i * 0.010,
                obp=0.330 + i * 0.005,
                slg=0.420 + i * 0.008,
                primary_position=pos,
                starts_last_5_games=3,
            )
        )
    return players


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


def test_recommendation_is_deterministic() -> None:
    """Two calls with identical inputs must produce byte-identical results."""
    pool = _make_pool()
    r1 = generate_recommendation(pool, Handedness.RIGHT)
    r2 = generate_recommendation(pool, Handedness.RIGHT)
    assert r1.total_score == r2.total_score
    assert r1.slots == r2.slots
    assert r1.reasons == r2.reasons


def test_recommendation_deterministic_different_player_id_order() -> None:
    """Shuffling pool list order must not change the recommendation."""
    pool = _make_pool()
    pool_reversed = list(reversed(pool))
    r1 = generate_recommendation(pool, Handedness.RIGHT)
    r2 = generate_recommendation(pool_reversed, Handedness.RIGHT)
    # Slots should contain the same assignments even if pool order differs
    assert {(s.batting_order, s.player_id) for s in r1.slots} == {
        (s.batting_order, s.player_id) for s in r2.slots
    }


# ---------------------------------------------------------------------------
# Impossible-position blocking
# ---------------------------------------------------------------------------


def test_catcher_ineligible_player_not_placed_at_C() -> None:
    """A player with no C eligibility must not appear at the catcher slot."""
    pool = _make_pool()  # player 1 has primary_position=C; others do not

    # Remove the catcher from the pool and replace with a DH-only player
    pool_without_c = [p for p in pool if p.primary_position != Position.C]
    # Add a replacement catcher-eligible player
    pool_with_c = pool_without_c + [
        HitterStats(
            player_id=100,
            handedness=Handedness.RIGHT,
            ops=0.700,
            obp=0.310,
            slg=0.390,
            primary_position=Position.C,
            starts_last_5_games=3,
        )
    ]

    rec = generate_recommendation(pool_with_c, Handedness.RIGHT)
    catcher_slot = next(s for s in rec.slots if s.position == Position.C)
    # Only player 100 is catcher-eligible
    assert catcher_slot.player_id == 100


def test_impossible_position_raises_when_no_catcher() -> None:
    """ValueError raised when no eligible catcher is in the pool."""
    pool_no_catcher = [p for p in _make_pool() if p.primary_position != Position.C]
    with pytest.raises(ValueError, match="C"):
        generate_recommendation(pool_no_catcher, Handedness.RIGHT)


# ---------------------------------------------------------------------------
# Full recommendation structure
# ---------------------------------------------------------------------------


def test_recommendation_produces_9_distinct_players() -> None:
    """Recommended lineup must have exactly 9 distinct player_ids."""
    pool = _make_pool()
    rec = generate_recommendation(pool, Handedness.RIGHT)
    assert len(rec.slots) == 9
    player_ids = {s.player_id for s in rec.slots}
    assert len(player_ids) == 9


def test_recommendation_batting_orders_are_1_to_9() -> None:
    """batting_order values must be exactly {1, 2, 3, 4, 5, 6, 7, 8, 9}."""
    pool = _make_pool()
    rec = generate_recommendation(pool, Handedness.RIGHT)
    orders = {s.batting_order for s in rec.slots}
    assert orders == set(range(1, 10))


def test_recommendation_total_score_positive() -> None:
    """Total lineup score must be positive for a healthy pool."""
    pool = _make_pool()
    rec = generate_recommendation(pool, Handedness.RIGHT)
    assert rec.total_score > 0


# ---------------------------------------------------------------------------
# DB persistence via evaluate_lineup_for_run (uses LG fixture)
# ---------------------------------------------------------------------------


def _seed_evaluation_run(session: Session) -> LineupEvaluationRun:
    """Load the LG fixture, create supporting rows, return a pending run."""
    load_fixture_file(FIXTURE_PATH, session)

    # Resolve IDs needed for evaluation run
    team = session.query(Team).filter_by(code="LG").one()
    game = session.query(Game).filter_by(external_id="KBO-2026-LG-DOO-001").one()

    cutoff = datetime(2026, 4, 15, 18, 0, 0, tzinfo=UTC)
    stat_snap = select_stat_snapshot(session, team_id=team.id, cutoff_at=cutoff)
    lineup_snap = select_lineup_snapshot(
        session, game_id=game.id, team_id=team.id, cutoff_at=cutoff
    )

    # Create model version
    mv = ModelVersion(name="scoring-v1", version="v1", model_id="deterministic/scoring-v1")
    session.add(mv)
    session.flush()

    run = LineupEvaluationRun(
        game_id=game.id,
        team_id=team.id,
        model_version_id=mv.id,
        stat_snapshot_id=stat_snap.id,
        lineup_snapshot_id=lineup_snap.id,
        evaluation_cutoff_at=cutoff,
        status="pending",
    )
    session.add(run)
    session.flush()
    return run


def test_evaluate_lineup_persists_9_rows(session: Session) -> None:
    """After evaluate_lineup_for_run, recommended_lineup_rows count == 9."""
    run = _seed_evaluation_run(session)
    evaluate_lineup_for_run(session, run=run)
    session.commit()

    rows = session.query(RecommendedLineupRow).filter_by(evaluation_run_id=run.id).all()
    assert len(rows) == 9


def test_evaluate_lineup_persists_summary(session: Session) -> None:
    """After evaluate_lineup_for_run, lineup_evaluation_summaries has 1 row."""
    run = _seed_evaluation_run(session)
    evaluate_lineup_for_run(session, run=run)
    session.commit()

    summaries = session.query(LineupEvaluationSummary).filter_by(evaluation_run_id=run.id).all()
    assert len(summaries) == 1
    assert summaries[0].summary_text != ""
    assert summaries[0].key_insights_json is not None


def test_evaluate_lineup_updates_run_status(session: Session) -> None:
    """run.status must be 'completed' after evaluate_lineup_for_run."""
    run = _seed_evaluation_run(session)
    evaluate_lineup_for_run(session, run=run)
    session.commit()

    session.refresh(run)
    assert run.status == "completed"
    assert run.output_hash is not None
    assert run.finished_at is not None


def test_evaluate_lineup_batting_orders_1_to_9(session: Session) -> None:
    """Persisted rows must have batting_order values 1–9 (one each)."""
    run = _seed_evaluation_run(session)
    evaluate_lineup_for_run(session, run=run)
    session.commit()

    rows = session.query(RecommendedLineupRow).filter_by(evaluation_run_id=run.id).all()
    orders = {r.batting_order for r in rows}
    assert orders == set(range(1, 10))


def test_evaluate_lineup_key_insights_contains_score(session: Session) -> None:
    """key_insights_json must contain recommended_total_score."""
    run = _seed_evaluation_run(session)
    evaluate_lineup_for_run(session, run=run)
    session.commit()

    summary = session.query(LineupEvaluationSummary).filter_by(evaluation_run_id=run.id).one()
    insights = summary.key_insights_json
    assert insights is not None
    assert "recommended_total_score" in insights
    assert isinstance(insights["recommended_total_score"], float)


def test_evaluate_lineup_key_insights_contains_actual_total_score(session: Session) -> None:
    """key_insights_json must contain actual_total_score so postgame review can skip recompute."""
    run = _seed_evaluation_run(session)
    evaluate_lineup_for_run(session, run=run)
    session.commit()

    summary = session.query(LineupEvaluationSummary).filter_by(evaluation_run_id=run.id).one()
    insights = summary.key_insights_json
    assert insights is not None
    assert "actual_total_score" in insights
    assert isinstance(insights["actual_total_score"], float)


def test_evaluate_lineup_opp_handedness_default_noted(session: Session) -> None:
    """key_insights_json must document the opponent handedness default."""
    run = _seed_evaluation_run(session)
    evaluate_lineup_for_run(session, run=run)
    session.commit()

    summary = session.query(LineupEvaluationSummary).filter_by(evaluation_run_id=run.id).one()
    insights = summary.key_insights_json
    assert insights is not None
    assert "opp_handedness_default" in insights
    assert "opp_handedness_note" in insights


def test_evaluate_lineup_re_run_does_not_duplicate_rows(session: Session) -> None:
    """Calling evaluate_lineup_for_run twice on a completed run must be a no-op.

    Without the idempotency guard the second call would insert 9 more
    RecommendedLineupRow rows and a second LineupEvaluationSummary,
    because there is no DB-level UNIQUE protecting against this.
    """
    run = _seed_evaluation_run(session)

    # First call — populates 9 rows + 1 summary, marks completed.
    evaluate_lineup_for_run(session, run=run)
    session.commit()

    session.refresh(run)
    first_output_hash = run.output_hash
    first_finished_at = run.finished_at
    assert run.status == "completed"

    # Second call on the same already-completed run.
    evaluate_lineup_for_run(session, run=run)
    session.commit()

    # Row counts must remain at 9 and 1 respectively.
    rec_rows = session.query(RecommendedLineupRow).filter_by(evaluation_run_id=run.id).all()
    assert len(rec_rows) == 9

    summaries = session.query(LineupEvaluationSummary).filter_by(evaluation_run_id=run.id).all()
    assert len(summaries) == 1

    # output_hash and finished_at must be unchanged.
    session.refresh(run)
    assert run.output_hash == first_output_hash
    assert run.finished_at == first_finished_at


def test_select_and_assign_positions_is_deterministic_and_complete() -> None:
    """동일 입력에 대해 9개 포지션이 모두 채워지고 결과가 결정론적인지 검증."""
    pool = _make_pool()
    first = select_and_assign_positions(pool, Handedness.RIGHT)
    second = select_and_assign_positions(pool, Handedness.RIGHT)

    assert len(first) == 9
    assert {str(p) for p in first} == {"C", "1B", "2B", "3B", "SS", "LF", "CF", "RF", "DH"}
    assert {p: s.player_id for p, s in first.items()} == {p: s.player_id for p, s in second.items()}


def test_build_hitter_stats_raises_typeerror_on_non_numeric(session: Session) -> None:
    """build_hitter_stats must raise TypeError when stats_json holds non-numeric data.

    Uses a non-numeric OPS value (a list) to trigger the explicit type check
    in _float. The error message must include the offending key and player id.
    """
    from app.services.lineup_evaluator import build_hitter_stats

    bad_stats: dict[str, object] = {
        "OPS": [0.9],  # not numeric
        "OBP": 0.350,
        "SLG": 0.450,
    }
    with pytest.raises(TypeError) as exc_info:
        build_hitter_stats(42, bad_stats, "1B")

    msg = str(exc_info.value)
    assert "OPS" in msg
    assert "42" in msg  # player_id surfaces in the error message
    assert "list" in msg  # type name surfaces too
