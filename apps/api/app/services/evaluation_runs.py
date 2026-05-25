"""Service for creating and retrieving LineupEvaluationRun records.

Implements idempotent get-or-create semantics keyed on the six-column
unique constraint defined on lineup_evaluation_runs.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.evaluation import LineupEvaluationRun
from app.services.run_manifest import build_manifest, hash_manifest
from app.util.time import to_utc


def get_or_create_evaluation_run(
    session: Session,
    *,
    game_id: int,
    team_id: int,
    evaluation_cutoff_at: datetime,
    stat_snapshot_id: int,
    lineup_snapshot_id: int,
    model_version_id: int,
    model_config: Mapping[str, object] | None = None,
) -> LineupEvaluationRun:
    """Return the existing evaluation run for the idempotency key, or create one.

    Idempotency key: (game_id, team_id, evaluation_cutoff_at, stat_snapshot_id,
    lineup_snapshot_id, model_version_id). Matches the DB UNIQUE constraint
    uq_lineup_evaluation_runs_idempotency.

    On insert: sets status='pending', input_manifest_json, and input_hash.
    Leaves output_hash, started_at, and finished_at for the actual model
    execution to fill in later (Plan 05).

    This function does NOT commit — the caller controls transaction boundaries.
    session.flush() is called so the new row gets its id immediately.

    Args:
        session: SQLAlchemy session.
        game_id: Game being evaluated.
        team_id: Team being evaluated.
        evaluation_cutoff_at: Tz-aware cutoff timestamp. Raises ValueError if naive.
        stat_snapshot_id: FK to the stat snapshot used.
        lineup_snapshot_id: FK to the lineup snapshot used.
        model_version_id: FK to the model version used.
        model_config: Optional model configuration dict stored alongside the run.

    Returns:
        Existing or newly created LineupEvaluationRun.

    Raises:
        ValueError: If evaluation_cutoff_at is naive (propagated from to_utc).
    """
    # Normalize cutoff to UTC up front so the SELECT comparison and the stored
    # INSERT value match build_manifest's UTC-canonical representation. Without
    # this, text-storage backends (SQLite) compare cutoffs lexicographically and
    # can miss or duplicate rows when callers pass non-UTC tz-aware datetimes.
    cutoff_utc = to_utc(evaluation_cutoff_at)

    existing = (
        session.execute(
            select(LineupEvaluationRun).where(
                LineupEvaluationRun.game_id == game_id,
                LineupEvaluationRun.team_id == team_id,
                LineupEvaluationRun.evaluation_cutoff_at == cutoff_utc,
                LineupEvaluationRun.stat_snapshot_id == stat_snapshot_id,
                LineupEvaluationRun.lineup_snapshot_id == lineup_snapshot_id,
                LineupEvaluationRun.model_version_id == model_version_id,
            )
        )
        .scalars()
        .first()
    )

    if existing is not None:
        return existing

    manifest = build_manifest(
        game_id=game_id,
        team_id=team_id,
        evaluation_cutoff_at=cutoff_utc,
        stat_snapshot_id=stat_snapshot_id,
        lineup_snapshot_id=lineup_snapshot_id,
        model_version_id=model_version_id,
        model_config=model_config,
    )
    input_hash = hash_manifest(manifest)

    run = LineupEvaluationRun(
        game_id=game_id,
        team_id=team_id,
        evaluation_cutoff_at=cutoff_utc,
        stat_snapshot_id=stat_snapshot_id,
        lineup_snapshot_id=lineup_snapshot_id,
        model_version_id=model_version_id,
        status="pending",
        input_manifest_json=manifest,
        input_hash=input_hash,
        model_config_json=dict(model_config) if model_config is not None else None,
    )
    session.add(run)
    session.flush()
    return run
