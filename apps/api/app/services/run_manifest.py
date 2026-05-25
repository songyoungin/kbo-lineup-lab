"""Input manifest construction and hashing for evaluation runs.

Manifests are canonical JSON blobs that uniquely describe the inputs to a
lineup evaluation. Hashing the manifest produces a stable idempotency key.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import datetime

from app.util.time import to_utc


def canonical_json(payload: Mapping[str, object]) -> str:
    """Serialize a payload to canonical JSON with sorted keys and no whitespace.

    Strict requirements: NaN/Infinity floats raise ValueError (allow_nan=False)
    so non-finite values cannot pollute the hash. Dates and datetimes are
    serialized to ISO strings via `default=str`. Datetimes must be tz-aware
    (callers normalize to UTC via build_manifest).

    Args:
        payload: Mapping to serialize.

    Returns:
        Compact JSON string with keys sorted alphabetically.

    Raises:
        ValueError: If payload contains NaN or Infinity float values.
    """
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
        allow_nan=False,
    )


def hash_manifest(payload: Mapping[str, object]) -> str:
    """Return the SHA-256 hex digest of canonical_json(payload).

    Args:
        payload: Mapping to hash. Keys are sorted before hashing, so insertion
            order does not affect the result.

    Returns:
        64-character lowercase hex string.
    """
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def build_manifest(
    *,
    game_id: int,
    team_id: int,
    evaluation_cutoff_at: datetime,
    stat_snapshot_id: int,
    lineup_snapshot_id: int,
    model_version_id: int,
    model_config: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Construct the canonical input manifest dict.

    The structure is intentionally flat for easy diffing across runs. Always
    normalizes evaluation_cutoff_at to UTC before serialization.

    Args:
        game_id: Game being evaluated.
        team_id: Team being evaluated.
        evaluation_cutoff_at: Tz-aware cutoff timestamp; normalized to UTC.
        stat_snapshot_id: FK to the stat snapshot used.
        lineup_snapshot_id: FK to the lineup snapshot used.
        model_version_id: FK to the model version used.
        model_config: Optional model configuration dict.

    Returns:
        Flat dict suitable for passing to canonical_json / hash_manifest.

    Raises:
        ValueError: If evaluation_cutoff_at is naive.
    """
    return {
        "evaluation_cutoff_at": to_utc(evaluation_cutoff_at).isoformat(),
        "game_id": game_id,
        "lineup_snapshot_id": lineup_snapshot_id,
        "model_config": dict(model_config) if model_config is not None else None,
        "model_version_id": model_version_id,
        "stat_snapshot_id": stat_snapshot_id,
        "team_id": team_id,
    }
