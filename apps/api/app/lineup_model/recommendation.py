"""Deterministic lineup recommendation generator.

Strategy
--------
Given a pool of eligible hitters this module uses a constrained greedy
approach to build a valid 9-slot lineup:

1. For each defensive position in LINEUP_POSITIONS order (C, 1B, …, DH)
   pick the highest-scoring eligible player not already assigned to
   another position.  Score is derived from compute_player_score with
   the candidate position.  Ties are broken by ascending player_id for
   determinism.

2. Once the 9 defensive assignments are fixed, sort players into batting
   order slots by applying slot-specific reshuffling:
   - Slot 1: highest OBP
   - Slot 4: highest SLG
   - Slot 3: highest OPS (balanced)
   - Slots 2, 5–9: descending composite score for the remaining players.

3. Compute and return the LineupScoreBreakdown for the resulting lineup.

This greedy approach is O(positions × players) and is fully deterministic
for identical inputs.  For small pools (≤ ~15 hitters) the quality is
acceptable for MVP purposes.  A future version may enumerate permutations.
"""

from __future__ import annotations

from collections.abc import Callable

from app.lineup_model.lineup_score import compute_lineup_score
from app.lineup_model.player_score import compute_player_score
from app.lineup_model.types import (
    Handedness,
    HitterStats,
    LineupScoreBreakdown,
    LineupSlot,
    Position,
)

# Ordered list of positions to fill (determines greedy assignment sequence).
_POSITIONS_TO_FILL: tuple[Position, ...] = (
    Position.C,
    Position.FIRST,
    Position.SECOND,
    Position.THIRD,
    Position.SHORT,
    Position.LEFT,
    Position.CENTER,
    Position.RIGHT,
    Position.DH,
)


def _best_player_for_position(
    candidates: list[HitterStats],
    position: Position,
    opp_handedness: Handedness,
    excluded_ids: set[int],
) -> HitterStats | None:
    """Return the highest-scoring available player for a position.

    Eligibility is determined by compute_player_score returning non-None.
    Ties broken by ascending player_id (stable, deterministic).

    Args:
        candidates: Pool of eligible hitters.
        position: Defensive position to fill.
        opp_handedness: Opposing starter's handedness.
        excluded_ids: player_ids already assigned to another position.

    Returns:
        Best HitterStats or None if no eligible player remains.
    """
    best: HitterStats | None = None
    best_score: float = -1.0

    for stats in candidates:
        if stats.player_id in excluded_ids:
            continue
        breakdown = compute_player_score(stats, position, opp_handedness)
        if breakdown is None:
            # Impossible position — skip
            continue
        score = breakdown.total_score
        if score > best_score or (
            score == best_score and (best is None or stats.player_id < best.player_id)
        ):
            best = stats
            best_score = score

    return best


def _assign_batting_order(
    assignments: dict[Position, HitterStats],
    opp_handedness: Handedness,
) -> list[LineupSlot]:
    """Assign batting-order slots using slot-specific reshuffling.

    Slot 1 → highest OBP
    Slot 4 → highest SLG
    Slot 3 → highest OPS (season)
    Remaining slots (2, 5, 6, 7, 8, 9) → descending composite score
    (using the player's own position for the score; ties by player_id).

    Args:
        assignments: Mapping from defensive position to chosen HitterStats.
        opp_handedness: Opposing starter's handedness.

    Returns:
        List of LineupSlot (unsorted; callers may sort by batting_order).
    """
    players = list(assignments.items())  # [(position, stats), ...]

    # Compute composite score for each player at their assigned position.
    def composite(pos: Position, stats: HitterStats) -> float:
        bd = compute_player_score(stats, pos, opp_handedness)
        return bd.total_score if bd is not None else 0.0

    remaining: list[tuple[Position, HitterStats]] = list(players)
    slots: list[LineupSlot] = []

    def pop_by_key(
        key_fn: Callable[[Position, HitterStats], float],
    ) -> tuple[Position, HitterStats]:
        best_idx = 0
        best_val: float | None = None
        for i, (pos, st) in enumerate(remaining):
            val = key_fn(pos, st)
            if (
                best_val is None
                or val > best_val
                or (val == best_val and st.player_id < remaining[best_idx][1].player_id)
            ):
                best_idx = i
                best_val = val
        return remaining.pop(best_idx)

    # Slot 1 — highest OBP
    pos1, s1 = pop_by_key(lambda pos, st: st.obp)
    slots.append(LineupSlot(batting_order=1, player_id=s1.player_id, position=pos1))

    # Slot 4 — highest SLG
    pos4, s4 = pop_by_key(lambda pos, st: st.slg)
    slots.append(LineupSlot(batting_order=4, player_id=s4.player_id, position=pos4))

    # Slot 3 — highest OPS (balanced)
    pos3, s3 = pop_by_key(lambda pos, st: st.ops)
    slots.append(LineupSlot(batting_order=3, player_id=s3.player_id, position=pos3))

    # Remaining 6 slots (2, 5, 6, 7, 8, 9) — descending composite
    remaining_orders = [2, 5, 6, 7, 8, 9]
    for order in remaining_orders:
        pos_r, s_r = pop_by_key(lambda pos, st: composite(pos, st))
        slots.append(LineupSlot(batting_order=order, player_id=s_r.player_id, position=pos_r))

    return slots


def select_and_assign_positions(
    eligible_players: list[HitterStats],
    opp_handedness: Handedness,
) -> dict[Position, HitterStats]:
    """Greedily assign the highest-scoring eligible player to each defensive position.

    Args:
        eligible_players: Pool of available hitters.
        opp_handedness: Opposing starter's handedness.

    Returns:
        Mapping of position to the assigned HitterStats (9 entries).

    Raises:
        ValueError: If any of the 9 positions cannot be filled from the pool.
    """
    assigned: dict[Position, HitterStats] = {}
    excluded_ids: set[int] = set()

    for position in _POSITIONS_TO_FILL:
        best = _best_player_for_position(eligible_players, position, opp_handedness, excluded_ids)
        if best is None:
            raise ValueError(
                f"Cannot fill position {position}: no eligible player remaining in pool. "
                f"Assigned so far: {list(assigned.keys())}"
            )
        assigned[position] = best
        excluded_ids.add(best.player_id)

    return assigned


def generate_recommendation(
    eligible_players: list[HitterStats],
    opp_handedness: Handedness,
) -> LineupScoreBreakdown:
    """Generate the best valid 9-slot lineup from the eligible player pool.

    Raises ValueError if the pool cannot fill all 9 positions.

    Args:
        eligible_players: All available hitters (status = available).
        opp_handedness: Opposing starter's handedness.

    Returns:
        LineupScoreBreakdown for the recommended lineup.

    Raises:
        ValueError: If no valid 9-player lineup can be assembled.
    """
    assigned = select_and_assign_positions(eligible_players, opp_handedness)
    slots = _assign_batting_order(assigned, opp_handedness)
    stats_by_player = {stats.player_id: stats for stats in eligible_players}
    return compute_lineup_score(tuple(slots), stats_by_player, opp_handedness)
