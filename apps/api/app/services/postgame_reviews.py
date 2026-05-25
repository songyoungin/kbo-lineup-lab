"""Service layer for postgame review generation and retrieval.

Internal helpers use session.flush() so that the caller controls the
transaction boundary.  The public job-entry-point
generate_postgame_review_for_request commits the transaction itself,
matching the replay_evaluation pattern from Plan 06.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.lineup_model.types import Handedness
from app.models.evaluation import LineupEvaluationRun, LineupEvaluationSummary, RecommendedLineupRow
from app.models.game import Game
from app.models.player import Player
from app.models.postgame import PostgameReviewRun, PostgameReviewSummary
from app.models.snapshot import (
    ActualLineupSnapshotRow,
    BoxScoreRow,
    BoxScoreSnapshot,
)
from app.postgame.review_generator import (
    ActualLineupRow,
    BoxLineEntry,
    RecommendedRow,
    generate_postgame_review,
)
from app.postgame.types import PlayerPerformance
from app.schemas.postgame import (
    GeneratePostgameReviewRequest,
    GeneratePostgameReviewResponse,
    PostgameDifferenceReview,
    PostgamePlayerLine,
    PostgameResponse,
)
from app.services.lineup_evaluator import compute_actual_lineup_score

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _player_names_bulk(session: Session, player_ids: list[int]) -> dict[int, str]:
    """Return player_id → name mapping for the given ids."""
    if not player_ids:
        return {}
    players = session.execute(select(Player).where(Player.id.in_(player_ids))).scalars().all()
    return {p.id: p.name for p in players}


def _output_hash(breakdown_json: dict[str, object]) -> str:
    """Produce a stable SHA-256 fingerprint of a review breakdown."""
    canonical = json.dumps(breakdown_json, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


def _to_player_line(perf: PlayerPerformance, name: str) -> PostgamePlayerLine:
    return PostgamePlayerLine(
        player_id=perf.player_id,
        name=name,
        performance_score=perf.performance_score,
        label=perf.label.value,
        box_line=dict(perf.box_line),
    )


# ---------------------------------------------------------------------------
# Public service functions
# ---------------------------------------------------------------------------


def get_or_create_postgame_review(
    session: Session,
    *,
    evaluation_run_id: int,
    box_score_snapshot_id: int,
) -> tuple[PostgameReviewRun, bool]:
    """Look up an existing PostgameReviewRun by natural key or create a new one.

    The natural key is (evaluation_run_id, box_score_snapshot_id), enforced by
    a DB-level UNIQUE constraint (see migration 0003_*).

    Args:
        session: SQLAlchemy session.
        evaluation_run_id: PK of the LineupEvaluationRun to review.
        box_score_snapshot_id: PK of the BoxScoreSnapshot to compare against.

    Returns:
        Tuple of (run, created) where created=True when a new row was inserted.

    Raises:
        HTTPException: 404 when evaluation_run_id or box_score_snapshot_id
            do not exist.
    """
    # Validate foreign keys exist
    eval_run = session.get(LineupEvaluationRun, evaluation_run_id)
    if eval_run is None:
        raise HTTPException(
            status_code=404,
            detail=f"LineupEvaluationRun {evaluation_run_id} not found",
        )

    box_snapshot = session.get(BoxScoreSnapshot, box_score_snapshot_id)
    if box_snapshot is None:
        raise HTTPException(
            status_code=404,
            detail=f"BoxScoreSnapshot {box_score_snapshot_id} not found",
        )

    # Cross-game validation: evaluation run and box score must belong to the same game
    if eval_run.game_id != box_snapshot.game_id:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Mismatch: evaluation_run_id={evaluation_run_id} belongs to "
                f"game_id={eval_run.game_id} but box_score_snapshot_id={box_score_snapshot_id} "
                f"belongs to game_id={box_snapshot.game_id}."
            ),
        )

    existing = (
        session.execute(
            select(PostgameReviewRun).where(
                PostgameReviewRun.evaluation_run_id == evaluation_run_id,
                PostgameReviewRun.box_score_snapshot_id == box_score_snapshot_id,
            )
        )
        .scalars()
        .first()
    )

    if existing is not None:
        return existing, False

    run = PostgameReviewRun(
        evaluation_run_id=evaluation_run_id,
        box_score_snapshot_id=box_score_snapshot_id,
        model_version_id=eval_run.model_version_id,
        status="pending",
    )
    session.add(run)
    session.flush()
    return run, True


def generate_review_for_run(
    session: Session,
    *,
    run: PostgameReviewRun,
) -> PostgameReviewRun:
    """Compute and persist the postgame review output for a PostgameReviewRun.

    Reads the referenced evaluation run and box score snapshot, calls the
    pure generate_postgame_review function, then persists a PostgameReviewSummary
    and updates the run's status to 'completed'.

    Idempotent: returns the run unchanged when status is already 'completed'.

    Args:
        session: SQLAlchemy session.
        run: PostgameReviewRun row to process.

    Returns:
        The updated PostgameReviewRun.

    Raises:
        HTTPException: 404 when dependent data (eval run, box score) is missing.
    """
    if run.status == "completed":
        return run

    run.started_at = datetime.now(UTC)

    # Load the pregame evaluation run
    eval_run = session.get(LineupEvaluationRun, run.evaluation_run_id)
    if eval_run is None:
        raise HTTPException(
            status_code=404,
            detail=f"LineupEvaluationRun {run.evaluation_run_id} not found",
        )

    # Read the stored scores from the evaluation summary (NOT recomputed here)
    eval_summary = (
        session.execute(
            select(LineupEvaluationSummary).where(
                LineupEvaluationSummary.evaluation_run_id == eval_run.id
            )
        )
        .scalars()
        .first()
    )

    insights: dict[str, object] = (
        eval_summary.key_insights_json
        if eval_summary is not None and eval_summary.key_insights_json is not None
        else {}
    )
    _rec_raw = insights.get("recommended_total_score", 0.0)
    pregame_recommended_score = float(_rec_raw) if isinstance(_rec_raw, (int, float)) else 0.0

    # Read the actual lineup score from the evaluation run's stored summary.
    # Plan 05 evaluate_lineup_for_run writes 'actual_total_score' alongside
    # 'recommended_total_score', so postgame reviews never have to recompute.
    # Fall back to recomputing only for legacy runs that predate that field.
    _actual_raw = insights.get("actual_total_score")
    if _actual_raw is not None and isinstance(_actual_raw, (int, float)):
        pregame_actual_score = float(_actual_raw)
    else:
        pregame_actual_score = compute_actual_lineup_score(session, eval_run, Handedness.RIGHT)

    # Load actual lineup rows (from the snapshot the eval run was based on)
    actual_snapshot_rows = (
        session.execute(
            select(ActualLineupSnapshotRow).where(
                ActualLineupSnapshotRow.snapshot_id == eval_run.lineup_snapshot_id
            )
        )
        .scalars()
        .all()
    )

    # Load recommended lineup rows (persisted during the eval run — not recomputed)
    rec_rows = (
        session.execute(
            select(RecommendedLineupRow)
            .where(RecommendedLineupRow.evaluation_run_id == eval_run.id)
            .order_by(RecommendedLineupRow.batting_order)
        )
        .scalars()
        .all()
    )

    # Load box score rows for this snapshot
    box_rows = (
        session.execute(
            select(BoxScoreRow).where(BoxScoreRow.snapshot_id == run.box_score_snapshot_id)
        )
        .scalars()
        .all()
    )

    # Collect all player ids for bulk name lookup
    all_player_ids = list(
        {r.player_id for r in actual_snapshot_rows}
        | {r.player_id for r in rec_rows}
        | {r.player_id for r in box_rows}
    )
    name_map = _player_names_bulk(session, all_player_ids)

    # Convert ORM rows to plain NamedTuples for the pure generator
    actual_lineup = [
        ActualLineupRow(
            batting_order=r.batting_order or 0,
            player_id=r.player_id,
            position=r.position,
        )
        for r in actual_snapshot_rows
        if r.batting_order is not None
    ]

    recommended_lineup = [
        RecommendedRow(
            batting_order=r.batting_order or 0,
            player_id=r.player_id,
            position=r.position,
        )
        for r in rec_rows
        if r.batting_order is not None
    ]

    box_score_entries = [
        BoxLineEntry(
            player_id=r.player_id,
            box_line={
                "at_bats": r.at_bats,
                "hits": r.hits,
                "runs": r.runs,
                "rbis": r.rbis,
                "extra_stats_json": r.extra_stats_json or {},
                "innings_pitched": r.innings_pitched,
            },
        )
        for r in box_rows
    ]

    # Call the pure review generator (no DB access inside)
    breakdown = generate_postgame_review(
        evaluation_run_id=eval_run.id,
        box_score_snapshot_id=run.box_score_snapshot_id,
        pregame_actual_score=pregame_actual_score,
        pregame_recommended_score=pregame_recommended_score,
        actual_lineup=actual_lineup,
        recommended_lineup=recommended_lineup,
        box_score_rows=box_score_entries,
        player_names_by_id=name_map,
    )

    # Persist summary (UNIQUE on review_run_id)
    summary = PostgameReviewSummary(
        review_run_id=run.id,
        summary_text=breakdown.summary_text,
        comparison_json=dict(breakdown.key_insights_json),
    )
    session.add(summary)

    # Update run status
    run.status = "completed"
    run.output_hash = _output_hash(breakdown.key_insights_json)
    run.finished_at = datetime.now(UTC)

    session.flush()
    return run


def build_postgame_view(
    session: Session,
    game_id: int,
    *,
    team_id: int | None = None,
) -> PostgameResponse:
    """Assemble the postgame view payload for GET /api/games/{game_id}/postgame.

    Finds the latest completed PostgameReviewRun for this game + team,
    then composes a full PostgameResponse from the stored summary data.

    Args:
        session: SQLAlchemy session.
        game_id: Game to retrieve.
        team_id: Optional team filter; defaults to LG when None.

    Returns:
        PostgameResponse payload.

    Raises:
        HTTPException: 404 when game or review run is not found.
    """
    from app.models.team import Team

    game = session.get(Game, game_id)
    if game is None:
        raise HTTPException(status_code=404, detail=f"Game {game_id} not found")

    if team_id is None:
        # Default to LG — same convention as pregame views
        lg = session.execute(select(Team).where(Team.code == "LG")).scalars().first()
        if lg is None:
            raise HTTPException(status_code=404, detail="Team 'LG' not found")
        team_id = lg.id

    # Find the latest completed PostgameReviewRun for this game+team
    # by joining through LineupEvaluationRun
    completed_run = (
        session.execute(
            select(PostgameReviewRun)
            .join(
                LineupEvaluationRun,
                PostgameReviewRun.evaluation_run_id == LineupEvaluationRun.id,
            )
            .where(
                LineupEvaluationRun.game_id == game_id,
                LineupEvaluationRun.team_id == team_id,
                PostgameReviewRun.status == "completed",
            )
            .order_by(PostgameReviewRun.finished_at.desc())
            .limit(1)
        )
        .scalars()
        .first()
    )

    if completed_run is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"No completed postgame review for game_id={game_id} team_id={team_id}. "
                "Trigger one via POST /api/jobs/generate-postgame-review first."
            ),
        )

    # Load the stored summary
    summary = (
        session.execute(
            select(PostgameReviewSummary).where(
                PostgameReviewSummary.review_run_id == completed_run.id
            )
        )
        .scalars()
        .first()
    )

    if summary is None:
        raise HTTPException(
            status_code=404,
            detail=f"No summary found for postgame_review_run_id={completed_run.id}",
        )

    comparison: dict[str, object] = (
        summary.comparison_json if summary.comparison_json is not None else {}
    )

    # Reconstruct player performance lists from comparison_json
    eval_run = session.get(LineupEvaluationRun, completed_run.evaluation_run_id)
    if eval_run is None:
        raise HTTPException(
            status_code=404,
            detail=f"LineupEvaluationRun {completed_run.evaluation_run_id} not found",
        )

    # Re-derive performance data from the box score and actual lineup so the
    # response always reflects the stored snapshot data (not live data).
    actual_snapshot_rows = (
        session.execute(
            select(ActualLineupSnapshotRow).where(
                ActualLineupSnapshotRow.snapshot_id == eval_run.lineup_snapshot_id
            )
        )
        .scalars()
        .all()
    )
    rec_rows = (
        session.execute(
            select(RecommendedLineupRow)
            .where(RecommendedLineupRow.evaluation_run_id == eval_run.id)
            .order_by(RecommendedLineupRow.batting_order)
        )
        .scalars()
        .all()
    )
    box_rows = (
        session.execute(
            select(BoxScoreRow).where(
                BoxScoreRow.snapshot_id == completed_run.box_score_snapshot_id
            )
        )
        .scalars()
        .all()
    )

    all_player_ids = list(
        {r.player_id for r in actual_snapshot_rows}
        | {r.player_id for r in rec_rows}
        | {r.player_id for r in box_rows}
    )
    name_map = _player_names_bulk(session, all_player_ids)

    # Reconstruct the full breakdown from stored inputs
    from app.postgame.review_generator import (
        ActualLineupRow,
        BoxLineEntry,
        RecommendedRow,
        generate_postgame_review,
    )

    actual_lineup = [
        ActualLineupRow(
            batting_order=r.batting_order or 0, player_id=r.player_id, position=r.position
        )
        for r in actual_snapshot_rows
        if r.batting_order is not None
    ]
    recommended_lineup = [
        RecommendedRow(
            batting_order=r.batting_order or 0, player_id=r.player_id, position=r.position
        )
        for r in rec_rows
        if r.batting_order is not None
    ]
    box_score_entries = [
        BoxLineEntry(
            player_id=r.player_id,
            box_line={
                "at_bats": r.at_bats,
                "hits": r.hits,
                "runs": r.runs,
                "rbis": r.rbis,
                "extra_stats_json": r.extra_stats_json or {},
                "innings_pitched": r.innings_pitched,
            },
        )
        for r in box_rows
    ]

    _rec_raw = comparison.get("pregame_recommended_score", 0.0)
    pregame_recommended_score = float(_rec_raw) if isinstance(_rec_raw, (int, float)) else 0.0
    _actual_raw = comparison.get("pregame_actual_score", 0.0)
    pregame_actual_score = float(_actual_raw) if isinstance(_actual_raw, (int, float)) else 0.0

    breakdown = generate_postgame_review(
        evaluation_run_id=eval_run.id,
        box_score_snapshot_id=completed_run.box_score_snapshot_id,
        pregame_actual_score=pregame_actual_score,
        pregame_recommended_score=pregame_recommended_score,
        actual_lineup=actual_lineup,
        recommended_lineup=recommended_lineup,
        box_score_rows=box_score_entries,
        player_names_by_id=name_map,
    )

    def _line(perf: PlayerPerformance) -> PostgamePlayerLine:
        return _to_player_line(perf, name_map.get(perf.player_id, f"Player({perf.player_id})"))

    difference_reviews_out: list[PostgameDifferenceReview] = []
    for dr in breakdown.difference_reviews:
        difference_reviews_out.append(
            PostgameDifferenceReview(
                batting_order=dr.batting_order,
                actual_player_id=dr.actual_player_id,
                actual_player_name=name_map.get(
                    dr.actual_player_id, f"Player({dr.actual_player_id})"
                ),
                recommended_player_id=dr.recommended_player_id,
                recommended_player_name=name_map.get(
                    dr.recommended_player_id, f"Player({dr.recommended_player_id})"
                ),
                actual_performance=dr.actual_performance,
                verdict=dr.verdict,
                rationale=dr.rationale,
            )
        )

    return PostgameResponse(
        game_id=game_id,
        evaluation_run_id=eval_run.id,
        postgame_review_run_id=completed_run.id,
        pregame_actual_score=breakdown.pregame_actual_score,
        pregame_recommended_score=breakdown.pregame_recommended_score,
        pregame_score_gap=breakdown.pregame_score_gap,
        pregame_gap_label=breakdown.pregame_gap_label,
        overperformers=[_line(p) for p in breakdown.overperformers],
        underperformers=[_line(p) for p in breakdown.underperformers],
        other_actual=[_line(p) for p in breakdown.other_actual],
        difference_reviews=difference_reviews_out,
        summary_text=breakdown.summary_text,
        model_limitations=[
            "Performance score uses box score totals only; "
            "does not account for context (RISP, leverage, etc.)"
        ],
    )


def generate_postgame_review_for_request(
    session: Session,
    *,
    request: GeneratePostgameReviewRequest,
) -> GeneratePostgameReviewResponse:
    """Trigger or retrieve a postgame review run.

    1. Call get_or_create_postgame_review (idempotent).
    2. Call generate_review_for_run (idempotent).
    3. Commit the transaction.
    4. Return run metadata.

    This function commits the session after generating the review, matching
    the replay_evaluation entry-point pattern from Plan 06.  Route handlers
    must not commit again.

    Args:
        session: SQLAlchemy session.
        request: GeneratePostgameReviewRequest with evaluation_run_id and box_score_snapshot_id.

    Returns:
        GeneratePostgameReviewResponse with run id, created flag, and status.
    """
    run, created = get_or_create_postgame_review(
        session,
        evaluation_run_id=request.evaluation_run_id,
        box_score_snapshot_id=request.box_score_snapshot_id,
    )
    run = generate_review_for_run(session, run=run)
    session.commit()

    return GeneratePostgameReviewResponse(
        postgame_review_run_id=run.id,
        created=created,
        status=run.status,
    )
