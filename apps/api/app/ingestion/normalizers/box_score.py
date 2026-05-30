"""Normalize a Naver record box-score payload into BoxScoreSnapshot + rows.

The collector stores the raw Naver api-gw record JSON. This normalizer extracts
the LG side of ``result.recordData.battersBoxscore`` into one immutable box-score
snapshot and creates one row per batter. Each batter is upserted as a Player
(keyed on ``playerCode``); box-only substitutes that the lineup normalizer never
saw are created on the fly (team=LG, position from the box ``pos`` token via
:func:`~app.ingestion.normalizers._shared.to_position`, handedness left null until
a preview/lineup provides it). The game is resolved from the Naver game id
embedded in the raw payload source URL.
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
    to_position,
)
from app.models.player import Player
from app.models.snapshot import BoxScoreRow, BoxScoreSnapshot, RawIngestionPayload
from app.models.team import Team

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
        rows_skipped: Number of batters skipped because they had no playerCode.
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

    lg_team = session.execute(select(Team).where(Team.code == LG_TEAM_CODE)).scalar_one_or_none()
    if lg_team is None:
        raise ValueError(f"unknown team code: {LG_TEAM_CODE!r}")

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
        if external_id is None:
            # Cannot upsert a Player without a stable identifier.
            rows_skipped += 1
            needs_review_reasons.append("box_score batter missing playerCode")
            continue

        player = session.execute(
            select(Player).where(Player.external_id == external_id)
        ).scalar_one_or_none()
        if player is None:
            # Box-only substitute the lineup never saw: create it with the box
            # position; handedness stays null until a preview/lineup provides it.
            name = entry.get("name")
            player = Player(
                team_id=lg_team.id,
                external_id=external_id,
                name=str(name) if name else external_id,
                position=to_position(entry.get("pos")),
                bats=None,
                throws=None,
            )
            session.add(player)
            session.flush()

        extra_stats = {key: entry.get(key) for key in _EXTRA_STAT_KEYS}
        session.add(
            BoxScoreRow(
                snapshot_id=snapshot_id,
                player_id=player.id,
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
