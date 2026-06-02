"""Merge KBO HitterDetail L/R splits and RISP into existing stat snapshot rows.

Task 6's ``collect_kbo_official`` stores raw HTML for each hitter's
``HitterDetail/Situation.aspx`` (pitcher-type splits) and ``HitterDetail/
Basic.aspx`` (season RISP) pages under ``source_name="kbo_official"`` and
``category=PayloadCategory.PLAYER_STATS``. This normalizer reads those payloads
for an ingestion run, groups them by the ``playerId`` embedded in the source
URL, resolves each to a :class:`Player` by ``external_id``, and merges the
parsed splits (``vs_lhp_ops/pa``, ``vs_rhp_ops/pa``) and ``risp_avg`` into that
player's existing :class:`PlayerStatSnapshotRow` for the given snapshot.

Unlike :mod:`app.ingestion.normalizers.player_stats`, this is an enrichment pass
over an already-built snapshot: it never creates snapshot rows, only updates the
``stats_json`` of rows the season normalizer produced.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ingestion.kbo_parse import (
    parse_hitter_risp,
    parse_pitcher_type_splits,
    split_rate_stats,
)
from app.ingestion.types import PayloadCategory
from app.models.player import Player
from app.models.snapshot import PlayerStatSnapshotRow, RawIngestionPayload

__all__ = [
    "KboHitterSplitsNormalizeResult",
    "merge_hitter_splits_into_stats",
    "normalize_kbo_hitter_splits",
]

# Extracts the KBO playerId from ".../HitterDetail/...aspx?playerId=66108".
_PLAYER_ID_URL_RE: Final = re.compile(r"playerId=(\d+)")


def merge_hitter_splits_into_stats(
    stats_json: dict[str, object], situation_html: str, basic_html: str
) -> dict[str, object]:
    """Return a copy of ``stats_json`` enriched with L/R splits and RISP.

    Adds ``vs_lhp_ops/pa`` and ``vs_rhp_ops/pa`` from the situation page (a side
    is omitted when its split is None or has zero plate appearances) and
    ``risp_avg`` from the basic page (omitted when absent). Existing keys are
    preserved; when the HTML lacks the data the returned dict equals the input.

    Args:
        stats_json: Existing per-player stats blob.
        situation_html: HitterDetail/Situation.aspx HTML (pitcher-type splits).
        basic_html: HitterDetail/Basic.aspx HTML (season RISP).

    Returns:
        A new dict with the merged split/RISP keys added.
    """
    out = dict(stats_json)
    splits = parse_pitcher_type_splits(situation_html)
    if splits is not None:
        lhp = split_rate_stats(splits["lhp"])
        rhp = split_rate_stats(splits["rhp"])
        if lhp is not None:
            out["vs_lhp_ops"] = lhp["ops"]
            out["vs_lhp_pa"] = lhp["pa"]
        if rhp is not None:
            out["vs_rhp_ops"] = rhp["ops"]
            out["vs_rhp_pa"] = rhp["pa"]
    risp = parse_hitter_risp(basic_html)
    if risp is not None:
        out["risp_avg"] = risp
    return out


@dataclass(frozen=True)
class KboHitterSplitsNormalizeResult:
    """Result of a KBO L/R-splits + RISP enrichment pass.

    Attributes:
        rows_updated: Number of snapshot rows whose stats_json was rewritten.
        needs_review_reasons: Audit strings for payloads that could not be
            applied (no playerId, unknown player, or no snapshot row).
    """

    rows_updated: int
    needs_review_reasons: tuple[str, ...]


@dataclass
class _PlayerPages:
    """Situation/basic HTML bodies collected for one playerId."""

    situation_html: str | None = None
    basic_html: str | None = None


def normalize_kbo_hitter_splits(
    session: Session,
    *,
    ingestion_run_id: int,
    snapshot_id: int,
) -> KboHitterSplitsNormalizeResult:
    """Merge KBO L/R splits and RISP into a snapshot's existing player rows.

    Reads every ``kbo_official`` PLAYER_STATS payload for ``ingestion_run_id``,
    groups them by the ``playerId`` in the source URL and by page kind
    (``Situation.aspx`` vs ``HitterDetail/Basic.aspx``; PitcherDetail pages are
    ignored). For each playerId that has at least a situation page, resolves the
    ``Player`` by ``external_id`` and its ``PlayerStatSnapshotRow`` in
    ``snapshot_id``, then reassigns ``row.stats_json`` to the merged dict so
    SQLAlchemy flags the change. Payloads with no playerId, no matching player,
    or no snapshot row are recorded as needs-review reasons.

    Args:
        session: Active SQLAlchemy session. Caller controls the transaction.
        ingestion_run_id: Run whose ``kbo_official`` payloads to apply.
        snapshot_id: StatSnapshot whose rows are enriched.

    Returns:
        KboHitterSplitsNormalizeResult.
    """
    payloads = list(
        session.execute(
            select(RawIngestionPayload).where(
                RawIngestionPayload.ingestion_run_id == ingestion_run_id,
                RawIngestionPayload.source_name == "kbo_official",
                RawIngestionPayload.category == PayloadCategory.PLAYER_STATS.value,
            )
        ).scalars()
    )

    needs_review_reasons: list[str] = []
    pages_by_player: dict[str, _PlayerPages] = {}

    for payload in payloads:
        url = payload.source_url
        if "Situation.aspx" not in url and "HitterDetail/Basic.aspx" not in url:
            continue
        match = _PLAYER_ID_URL_RE.search(url)
        if match is None:
            needs_review_reasons.append(
                f"kbo_splits payload skipped — no playerId in source_url ({url!r})"
            )
            continue
        player_id = match.group(1)
        pages = pages_by_player.setdefault(player_id, _PlayerPages())
        if "Situation.aspx" in url:
            pages.situation_html = payload.raw_body
        else:
            pages.basic_html = payload.raw_body

    rows_updated = 0

    for external_id, pages in pages_by_player.items():
        if pages.situation_html is None:
            continue

        player = session.execute(
            select(Player).where(Player.external_id == external_id)
        ).scalar_one_or_none()
        if player is None:
            needs_review_reasons.append(
                f"kbo_splits payload skipped — no Player with external_id={external_id!r}"
            )
            continue

        row = session.execute(
            select(PlayerStatSnapshotRow).where(
                PlayerStatSnapshotRow.snapshot_id == snapshot_id,
                PlayerStatSnapshotRow.player_id == player.id,
            )
        ).scalar_one_or_none()
        if row is None:
            needs_review_reasons.append(
                f"kbo_splits payload skipped — no snapshot row for external_id="
                f"{external_id!r} in snapshot {snapshot_id}"
            )
            continue

        # Reassign the dict so SQLAlchemy detects the mutation (JSON columns are
        # not tracked in place) — same pattern as _persist_start_rhythm.
        row.stats_json = merge_hitter_splits_into_stats(
            row.stats_json, pages.situation_html, pages.basic_html or ""
        )
        rows_updated += 1

    session.flush()

    return KboHitterSplitsNormalizeResult(
        rows_updated=rows_updated,
        needs_review_reasons=tuple(needs_review_reasons),
    )
