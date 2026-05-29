"""Normalize a Naver record box-score payload into BoxScoreSnapshot + rows.

The collector stores the raw Naver api-gw record JSON. This normalizer extracts
the LG side of ``result.recordData.battersBoxscore`` into one immutable box-score
snapshot and creates one row per batter, matching each to a Player via
:func:`~app.ingestion.player_matcher.match_player`. The game is resolved from the
Naver game id embedded in the raw payload source URL.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Final

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ingestion.normalizers._shared import (
    LG_TEAM_CODE,
    compute_content_hash,
    parse_game_datetime_kst,
    resolve_game_from_naver_url,
)
from app.ingestion.player_matcher import MatchStatus, match_player
from app.models.snapshot import BoxScoreRow, BoxScoreSnapshot, RawIngestionPayload

__all__ = ["BoxScoreNormalizeResult", "normalize_box_score"]

# Stat keys preserved in extra_stats_json beyond the typed columns.
_EXTRA_STAT_KEYS: Final = ("hr", "bb", "kk", "sb", "hra", "pos", "batOrder")


@dataclass(frozen=True)
class BoxScoreNormalizeResult:
    """Result of normalizing a box-score payload.

    Attributes:
        snapshot_id: PK of the created or existing BoxScoreSnapshot. None when the
            game is not yet final.
        rows_created: Number of newly inserted BoxScoreRow rows.
        rows_skipped: Number of batters skipped because no Player matched.
        skipped_not_final: True when the LG box score is absent (game not final).
        needs_review_reasons: Reasons that require manual review.
    """

    snapshot_id: int | None
    rows_created: int
    rows_skipped: int
    skipped_not_final: bool
    needs_review_reasons: tuple[str, ...]


def _int_or_none(value: object) -> int | None:
    """Return value as int when int/float, else None."""
    if isinstance(value, (int, float)):
        return int(value)
    return None


def normalize_box_score(
    session: Session,
    raw_payload: RawIngestionPayload,
) -> BoxScoreNormalizeResult:
    """Parse a Naver record payload into a BoxScoreSnapshot + per-batter rows.

    Selects the LG side via ``gameInfo.hCode``/``aCode``. When the LG batters list
    is empty/absent the game is not final yet and no snapshot is created. A
    deterministic ``taken_at`` is derived from gdate/gtime (KST -> UTC), and a
    content hash over the extracted batters makes re-runs idempotent.

    Args:
        session: Active SQLAlchemy session. Caller controls the transaction.
        raw_payload: Row from ``raw_ingestion_payloads``.

    Returns:
        BoxScoreNormalizeResult.

    Raises:
        NotImplementedError: If the payload content_type is not JSON.
        ValueError: If the payload is malformed, the game cannot be resolved, or
            LG is not in the game.
    """
    if "json" not in raw_payload.content_type.lower():
        raise NotImplementedError(
            f"HTML box_score normalization not implemented in MVP; "
            f"content_type={raw_payload.content_type!r}"
        )

    try:
        body = json.loads(raw_payload.raw_body)
    except json.JSONDecodeError as exc:
        raise ValueError(f"box_score payload is not valid JSON: {exc}") from exc

    record = (body.get("result") or {}).get("recordData") or {}
    if not isinstance(record, dict):
        raise ValueError("box_score payload missing result.recordData")
    game_info = record.get("gameInfo") or {}
    if not isinstance(game_info, dict):
        raise ValueError("box_score payload missing recordData.gameInfo")

    batters_box = record.get("battersBoxscore") or {}
    if not isinstance(batters_box, dict):
        raise ValueError("box_score payload missing recordData.battersBoxscore")

    home_code = game_info.get("hCode")
    away_code = game_info.get("aCode")
    if home_code == LG_TEAM_CODE:
        lg_batters = batters_box.get("home") or []
    elif away_code == LG_TEAM_CODE:
        lg_batters = batters_box.get("away") or []
    else:
        raise ValueError(f"LG not in game: hCode={home_code!r} aCode={away_code!r}")

    lg_batters = [b for b in lg_batters if isinstance(b, dict)]
    if not lg_batters:
        return BoxScoreNormalizeResult(
            snapshot_id=None,
            rows_created=0,
            rows_skipped=0,
            skipped_not_final=True,
            needs_review_reasons=(),
        )

    game = resolve_game_from_naver_url(session, raw_payload.source_url)
    taken_at = parse_game_datetime_kst(game_info)
    content_hash = compute_content_hash(lg_batters)

    existing_snapshot = session.execute(
        select(BoxScoreSnapshot).where(BoxScoreSnapshot.content_hash == content_hash)
    ).scalar_one_or_none()
    if existing_snapshot is not None:
        return BoxScoreNormalizeResult(
            snapshot_id=existing_snapshot.id,
            rows_created=0,
            rows_skipped=0,
            skipped_not_final=False,
            needs_review_reasons=(),
        )

    new_snapshot = BoxScoreSnapshot(
        game_id=game.id,
        ingestion_run_id=raw_payload.ingestion_run_id,
        taken_at=taken_at,
        content_hash=content_hash,
    )
    session.add(new_snapshot)
    session.flush()
    snapshot_id = new_snapshot.id

    rows_created = 0
    rows_skipped = 0
    needs_review_reasons: list[str] = []

    for entry in lg_batters:
        raw_player_code = entry.get("playerCode")
        external_id = str(raw_player_code) if raw_player_code is not None else None
        name = entry.get("name")
        match = match_player(
            session,
            team_code=LG_TEAM_CODE,
            external_id=external_id,
            name=str(name) if name else None,
        )

        if match.status == MatchStatus.NOT_FOUND:
            # Box-only substitutes (not present from the lineup upsert) are skipped
            # in this MVP — match-only by design.  Upserting box-only players is a
            # documented follow-up.
            rows_skipped += 1
            needs_review_reasons.append(
                f"box_score batter skipped — {match.reason} (playerCode={external_id!r})"
            )
            continue

        if match.status == MatchStatus.NEEDS_REVIEW:
            needs_review_reasons.append(match.reason)
            if match.player_id is None:
                rows_skipped += 1
                continue

        assert match.player_id is not None
        extra_stats = {key: entry.get(key) for key in _EXTRA_STAT_KEYS}
        session.add(
            BoxScoreRow(
                snapshot_id=snapshot_id,
                player_id=match.player_id,
                at_bats=_int_or_none(entry.get("ab")),
                hits=_int_or_none(entry.get("hit")),
                runs=_int_or_none(entry.get("run")),
                rbis=_int_or_none(entry.get("rbi")),
                extra_stats_json=extra_stats,
                innings_pitched=None,
            )
        )
        rows_created += 1

    session.flush()
    return BoxScoreNormalizeResult(
        snapshot_id=snapshot_id,
        rows_created=rows_created,
        rows_skipped=rows_skipped,
        skipped_not_final=False,
        needs_review_reasons=tuple(needs_review_reasons),
    )
