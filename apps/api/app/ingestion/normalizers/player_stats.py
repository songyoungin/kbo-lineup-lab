"""Normalizer that builds one StatSnapshot from per-player season payloads.

Task 3's ``collect_player_season_stats`` stores one raw payload per LG lineup
player, each holding the verified Naver ``playerend-record`` JSON. This
normalizer reads every PLAYER_STATS payload for an ingestion run, selects the
``record.season`` row matching the game's year (falling back to the career row),
maps it through :func:`~app.ingestion.season_stats_map.map_season_stats`, and
writes one :class:`PlayerStatSnapshotRow` per resolved player into a single
:class:`StatSnapshot`.

Source shape (verified in docs/data-sources/player-season-stats-verification.md):
the payload body is ``{"code","success","result"}``; ``result.record`` is a
JSON-encoded string holding ``{"day_limit","day_start","game","season"}`` where
``season`` is a list of flat per-year batting rows keyed by ``gyear`` (a string
year, plus a career row ``gyear="통산"``). ``obp/slg/ops`` are native numbers.

Idempotency
-----------
``StatSnapshot.content_hash`` (UNIQUE) is computed over the game id plus the
sorted (player_id, OPS) pairs of the mapped rows. Re-running an identical set of
payloads yields the same hash and returns the existing snapshot unchanged.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Final

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ingestion.normalizers._shared import KST, compute_content_hash
from app.ingestion.season_stats_map import map_season_stats
from app.ingestion.types import PayloadCategory
from app.models.game import Game
from app.models.player import Player
from app.models.snapshot import PlayerStatSnapshotRow, RawIngestionPayload, StatSnapshot

__all__ = ["PlayerStatsNormalizeResult", "normalize_player_stats"]

# Extracts the Naver player code from ".../players/kbo/{code}/playerend-record".
_PLAYER_CODE_URL_RE: Final = re.compile(r"/players/kbo/([^/]+)/playerend-record")

# Career fallback marker used by the Naver season list (gyear == "통산").
_CAREER_GYEAR: Final = "통산"


@dataclass(frozen=True)
class PlayerStatsNormalizeResult:
    """Result of a player-stats normalization pass.

    Attributes:
        snapshot_id: PK of the created or pre-existing StatSnapshot.
        rows_created: Number of newly inserted PlayerStatSnapshotRow rows.
        rows_skipped: Number of payloads skipped (no player code, unknown player,
            invalid JSON, or no usable season row).
        needs_review_reasons: Audit strings describing each skip.
    """

    snapshot_id: int
    rows_created: int
    rows_skipped: int
    needs_review_reasons: tuple[str, ...]


def _extract_season_row(body: dict[str, object], *, year: str) -> dict[str, object] | None:
    """Return the record.season row for the given year, else the career row,
    else None. record is a JSON-encoded string holding {"season":[...]}."""
    result = body.get("result")
    if not isinstance(result, dict):
        return None
    record_raw = result.get("record")
    if not isinstance(record_raw, str):
        return None
    try:
        record = json.loads(record_raw)
    except json.JSONDecodeError:
        return None
    rows = record.get("season")
    if not isinstance(rows, list):
        return None
    dict_rows: list[dict[str, object]] = [r for r in rows if isinstance(r, dict)]
    by_year: dict[str, dict[str, object]] = {str(r.get("gyear")): r for r in dict_rows}
    if year in by_year:
        return by_year[year]
    return by_year.get(_CAREER_GYEAR)


def normalize_player_stats(
    session: Session,
    *,
    game_external_id: str,
    ingestion_run_id: int,
) -> PlayerStatsNormalizeResult:
    """Build one StatSnapshot from per-player season payloads of an ingestion run.

    For each PLAYER_STATS payload in ``ingestion_run_id``, extracts the Naver
    player code from the source URL, resolves the matching ``Player`` by
    ``external_id``, selects the ``record.season`` row for the game's year (career
    fallback), and maps it to an evaluator stats_json. One row per resolved player
    is written into a single snapshot. Payloads with no code, no matching player,
    invalid JSON, or no usable season row are skipped with a review reason.

    Idempotency is enforced via ``StatSnapshot.content_hash`` (UNIQUE), computed
    over the game id and the sorted (player_id, OPS) pairs. An identical re-run
    returns the existing snapshot with ``rows_created=0``.

    Args:
        session: Active SQLAlchemy session. Caller controls the transaction.
        game_external_id: KBO game external id (e.g. "20250514WOLG0").
        ingestion_run_id: Ingestion run whose PLAYER_STATS payloads to normalize.

    Returns:
        PlayerStatsNormalizeResult.

    Raises:
        ValueError: If no game matches ``game_external_id``.
    """
    game = session.execute(
        select(Game).where(Game.external_id == game_external_id)
    ).scalar_one_or_none()
    if game is None:
        raise ValueError(f"unknown game: {game_external_id!r}")
    year = str(game.game_date.year)

    payloads = list(
        session.execute(
            select(RawIngestionPayload).where(
                RawIngestionPayload.ingestion_run_id == ingestion_run_id,
                RawIngestionPayload.category == PayloadCategory.PLAYER_STATS.value,
            )
        ).scalars()
    )

    mapped: list[tuple[int, dict[str, object]]] = []
    rows_skipped = 0
    needs_review_reasons: list[str] = []

    for payload in payloads:
        match = _PLAYER_CODE_URL_RE.search(payload.source_url)
        if match is None:
            rows_skipped += 1
            needs_review_reasons.append(
                f"player_stats payload skipped — no player code in source_url "
                f"({payload.source_url!r})"
            )
            continue
        code = match.group(1)

        player = session.execute(
            select(Player).where(Player.external_id == code)
        ).scalar_one_or_none()
        if player is None:
            rows_skipped += 1
            needs_review_reasons.append(
                f"player_stats payload skipped — no Player with external_id={code!r}"
            )
            continue

        try:
            body = json.loads(payload.raw_body)
        except json.JSONDecodeError as exc:
            rows_skipped += 1
            needs_review_reasons.append(
                f"player_stats payload skipped — invalid JSON for code={code!r}: {exc}"
            )
            continue

        if not isinstance(body, dict):
            rows_skipped += 1
            needs_review_reasons.append(
                f"player_stats payload skipped — body not an object for code={code!r}"
            )
            continue

        season_row = _extract_season_row(body, year=year)
        if season_row is None:
            rows_skipped += 1
            needs_review_reasons.append(
                f"player_stats payload skipped — no season row for code={code!r}, year={year}"
            )
            continue

        stats_json = map_season_stats(season_row, bats=player.bats, position=player.position)
        stats_json["season_year"] = str(season_row.get("gyear"))
        mapped.append((player.id, stats_json))

    content_hash = compute_content_hash(
        {
            "game": game_external_id,
            "rows": sorted((pid, sj["OPS"]) for pid, sj in mapped),
        }
    )

    existing = session.execute(
        select(StatSnapshot).where(StatSnapshot.content_hash == content_hash)
    ).scalar_one_or_none()
    if existing is not None:
        return PlayerStatsNormalizeResult(
            snapshot_id=existing.id,
            rows_created=0,
            rows_skipped=rows_skipped,
            needs_review_reasons=tuple(needs_review_reasons),
        )

    # Deterministic noon-KST sentinel derived from the game date so re-runs and
    # audits stay stable (idempotency itself is guarded by content_hash).
    snapshot = StatSnapshot(
        ingestion_run_id=ingestion_run_id,
        snapshot_at=datetime(
            game.game_date.year, game.game_date.month, game.game_date.day, 12, 0, tzinfo=KST
        ),
        content_hash=content_hash,
    )
    session.add(snapshot)
    session.flush()

    for player_id, stats_json in mapped:
        session.add(
            PlayerStatSnapshotRow(
                snapshot_id=snapshot.id,
                player_id=player_id,
                stats_json=stats_json,
            )
        )
    session.flush()

    return PlayerStatsNormalizeResult(
        snapshot_id=snapshot.id,
        rows_created=len(mapped),
        rows_skipped=rows_skipped,
        needs_review_reasons=tuple(needs_review_reasons),
    )
