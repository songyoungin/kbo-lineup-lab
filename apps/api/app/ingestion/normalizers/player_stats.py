"""Normalizer that parses Naver preview payloads into StatSnapshot rows.

The Naver preview endpoint (``result.previewData``) exposes ``currentSeasonStats``
for two LG players per game day:

- ``homeTopPlayer`` / ``awayTopPlayer``: the featured hitter with stats such as
  ``ab``, ``hit``, ``hra`` (AVG), ``obp``, ``rbi``, ``hr``.  There is **no**
  ``slg``, ``ops``, ``wrcPlus``, or ``woba`` in this source — only present fields
  are stored.
- ``homeStarter`` / ``awayStarter``: the starting pitcher with stats such as
  ``era``, ``whip``, ``w``, ``l``, ``kk``, ``bb``, ``inn``, ``hr``, ``er``, ``r``.

The LG side (home or away) is determined from ``gameInfo.hCode`` / ``aCode``.

Idempotency
-----------
``StatSnapshot.content_hash`` (UNIQUE) is computed over the canonical extracted
stats dict plus the raw payload id.  Re-running the same payload produces no
new rows.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Final

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ingestion.player_matcher import MatchStatus, match_player
from app.models.snapshot import PlayerStatSnapshotRow, RawIngestionPayload, StatSnapshot
from app.util.time import to_utc

__all__ = ["PlayerStatsNormalizeResult", "normalize_player_stats"]

_KST: Final = timezone(timedelta(hours=9))


@dataclass(frozen=True)
class PlayerStatsNormalizeResult:
    """Result of a player-stats normalization pass.

    Attributes:
        snapshot_id: PK of the created or pre-existing StatSnapshot.
        rows_created: Number of newly inserted PlayerStatSnapshotRow rows.
        rows_skipped: Number of entries skipped due to unresolved player matches.
        needs_review_reasons: Audit strings describing skips or soft-match concerns.
    """

    snapshot_id: int
    rows_created: int
    rows_skipped: int
    needs_review_reasons: tuple[str, ...]


def _compute_content_hash(canonical: object) -> str:
    """Return the SHA-256 hex digest of the canonical JSON representation.

    Args:
        canonical: Any JSON-serialisable object.

    Returns:
        64-character lowercase hex digest.
    """
    text = json.dumps(canonical, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(text.encode()).hexdigest()


def _parse_snapshot_at(game_info: dict[str, object]) -> datetime:
    """Derive a UTC snapshot timestamp from ``gameInfo.gdate`` and ``gtime``.

    Args:
        game_info: The ``gameInfo`` dict from ``result.previewData``.

    Returns:
        UTC-normalised datetime.  Falls back to midnight KST on the game date
        when ``gtime`` is absent or blank.

    Raises:
        ValueError: If ``gdate`` is missing or cannot be parsed as YYYYMMDD.
    """
    gdate_raw = game_info.get("gdate")
    gdate = str(gdate_raw) if isinstance(gdate_raw, int) else gdate_raw
    if not isinstance(gdate, str) or len(gdate) != 8:
        raise ValueError(f"gameInfo.gdate missing or invalid: {gdate_raw!r}")
    gtime_raw = game_info.get("gtime")
    gtime = gtime_raw if isinstance(gtime_raw, str) and gtime_raw.strip() else "00:00"
    local = datetime.strptime(f"{gdate} {gtime}", "%Y%m%d %H:%M").replace(tzinfo=_KST)
    return to_utc(local)


def normalize_player_stats(
    session: Session,
    raw_payload: RawIngestionPayload,
) -> PlayerStatsNormalizeResult:
    """Parse a Naver preview payload and upsert a StatSnapshot with stat rows.

    Parses ``result.previewData`` to extract ``currentSeasonStats`` for the
    LG-side featured hitter (topPlayer) and starting pitcher (starter).
    The LG side is detected via ``gameInfo.hCode`` / ``aCode``.

    Only fields present in the source JSON are stored.  Advanced metrics
    (slg, ops, wrcPlus, woba) are **not** fabricated.

    StatSnapshot idempotency is enforced via ``content_hash`` (UNIQUE constraint).
    Re-processing an identical payload returns the existing snapshot with
    ``rows_created=0``.

    Players are matched via :func:`~app.ingestion.player_matcher.match_player`
    using ``team_code="LG"`` and the source ``external_id``.  A ``NOT_FOUND``
    or ambiguous result records a ``needs_review`` reason and skips the row.
    Players are never upserted here — they must already exist (created by the
    lineup normalizer).

    Args:
        session: Active SQLAlchemy session. Caller controls the transaction.
        raw_payload: A ``raw_ingestion_payloads`` row with JSON content.

    Returns:
        PlayerStatsNormalizeResult.

    Raises:
        NotImplementedError: If ``content_type`` is not JSON.
        ValueError: If the JSON body cannot be parsed or ``gameInfo`` is missing.
    """
    if "json" not in raw_payload.content_type.lower():
        raise NotImplementedError(
            f"HTML player_stats normalization not implemented in MVP; "
            f"content_type={raw_payload.content_type!r}"
        )

    try:
        body = json.loads(raw_payload.raw_body)
    except json.JSONDecodeError as exc:
        raise ValueError(f"player_stats payload is not valid JSON: {exc}") from exc

    result_node: dict[str, object] = {}
    if isinstance(body, dict):
        _r = body.get("result")
        if isinstance(_r, dict):
            result_node = _r
    preview: dict[str, object] = {}
    _pd = result_node.get("previewData")
    if isinstance(_pd, dict):
        preview = _pd

    game_info_raw = preview.get("gameInfo")
    game_info: dict[str, object] = game_info_raw if isinstance(game_info_raw, dict) else {}
    snapshot_at = _parse_snapshot_at(game_info)

    h_code = game_info.get("hCode")
    a_code = game_info.get("aCode")

    if h_code == "LG":
        top_player_raw = preview.get("homeTopPlayer")
        starter_raw = preview.get("homeStarter")
    elif a_code == "LG":
        top_player_raw = preview.get("awayTopPlayer")
        starter_raw = preview.get("awayStarter")
    else:
        raise ValueError(f"LG not found in gameInfo: hCode={h_code!r}, aCode={a_code!r}")

    top_player: dict[str, object] = top_player_raw if isinstance(top_player_raw, dict) else {}
    starter: dict[str, object] = starter_raw if isinstance(starter_raw, dict) else {}

    # Build the entries list: [(external_id, stats_dict, role), ...]
    entries: list[tuple[str, dict[str, object], str]] = []

    if top_player:
        tp_ext_id = top_player.get("playerCode")
        tp_stats_raw = top_player.get("currentSeasonStats")
        if isinstance(tp_ext_id, str) and tp_ext_id and isinstance(tp_stats_raw, dict):
            entries.append((tp_ext_id, dict(tp_stats_raw), "hitter"))

    if starter:
        st_player_info_raw = starter.get("playerInfo")
        st_player_info: dict[str, object] = (
            st_player_info_raw if isinstance(st_player_info_raw, dict) else {}
        )
        st_ext_id = st_player_info.get("pCode")
        st_stats_raw = starter.get("currentSeasonStats")
        if isinstance(st_ext_id, str) and st_ext_id and isinstance(st_stats_raw, dict):
            entries.append((st_ext_id, dict(st_stats_raw), "pitcher"))

    # Content hash over extracted stats + payload id for determinism.
    canonical = {
        "payload_id": raw_payload.id,
        "entries": {ext_id: stats for ext_id, stats, _ in entries},
    }
    content_hash = _compute_content_hash(canonical)

    existing = session.execute(
        select(StatSnapshot).where(StatSnapshot.content_hash == content_hash)
    ).scalar_one_or_none()

    if existing is not None:
        return PlayerStatsNormalizeResult(
            snapshot_id=existing.id,
            rows_created=0,
            rows_skipped=len(entries),
            needs_review_reasons=(),
        )

    new_snapshot = StatSnapshot(
        ingestion_run_id=raw_payload.ingestion_run_id,
        snapshot_at=snapshot_at,
        content_hash=content_hash,
    )
    session.add(new_snapshot)
    session.flush()
    snapshot_id = new_snapshot.id

    rows_created = 0
    rows_skipped = 0
    needs_review_reasons: list[str] = []

    for external_id, stats, role in entries:
        # Derive name from the relevant sub-dict for fallback matching.
        if role == "hitter":
            _tp_info = top_player.get("playerInfo")
            _tp_info_d: dict[str, object] = _tp_info if isinstance(_tp_info, dict) else {}
            name_raw = _tp_info_d.get("name")
        else:
            _st_info = starter.get("playerInfo")
            _st_info_d: dict[str, object] = _st_info if isinstance(_st_info, dict) else {}
            name_raw = _st_info_d.get("name")
        name: str | None = name_raw if isinstance(name_raw, str) else None

        match = match_player(
            session,
            team_code="LG",
            external_id=external_id,
            name=name,
        )

        if match.status == MatchStatus.NOT_FOUND:
            rows_skipped += 1
            needs_review_reasons.append(
                f"player_stats row skipped — {match.reason} "
                f"(external_id={external_id!r}, role={role!r})"
            )
            continue

        if match.status == MatchStatus.NEEDS_REVIEW:
            needs_review_reasons.append(match.reason)
            if match.player_id is None:
                rows_skipped += 1
                continue

        assert match.player_id is not None
        session.add(
            PlayerStatSnapshotRow(
                snapshot_id=snapshot_id,
                player_id=match.player_id,
                stats_json={**stats, "role": role},
            )
        )
        rows_created += 1

    session.flush()
    return PlayerStatsNormalizeResult(
        snapshot_id=snapshot_id,
        rows_created=rows_created,
        rows_skipped=rows_skipped,
        needs_review_reasons=tuple(needs_review_reasons),
    )
