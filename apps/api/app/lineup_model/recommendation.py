"""Deterministic lineup recommendation generator.

Strategy
--------
Given a pool of eligible hitters this module builds a valid 9-slot lineup:

1. Assign the 9 defensive positions by solving the assignment problem exactly:
   a maximum-weight matching of distinct eligible players to positions, where
   a player's weight at a position is compute_player_score(...).total_score and
   ineligible (None-scoring) pairs are forbidden. Equal-total optima are broken
   canonically (lexicographically smallest assigned-player_id tuple in position
   order) so the result is deterministic.

2. Once the 9 defensive assignments are fixed, sort players into batting
   order slots by applying slot-specific reshuffling:
   - Slot 1: highest OBP
   - Slot 4: highest SLG
   - Slot 3: highest OPS (balanced)
   - Slots 2, 5-9: descending composite score for the remaining players.

3. Compute and return the LineupScoreBreakdown for the resulting lineup.
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

# Ordered list of positions to fill (determines assignment sequence).
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


def select_and_assign_positions(
    eligible_players: list[HitterStats],
    opp_handedness: Handedness,
) -> dict[Position, HitterStats]:
    """Assign the 9 defensive positions to maximise total per-position score.

    Solves the assignment problem exactly: a maximum-weight matching of distinct
    players to ``_POSITIONS_TO_FILL``, where a (player, position) pair is
    forbidden when ``compute_player_score`` returns None. Among equally optimal
    assignments the canonical one is chosen -- the tuple of assigned player_ids,
    read in ``_POSITIONS_TO_FILL`` order, that is lexicographically smallest --
    so the result is a pure, deterministic function of the inputs.

    Complexity is O(positions * 2^P * P) with memoisation, where P is the pool
    size; available-hitter pools are small (~13-16), so this is trivially fast.

    Args:
        eligible_players: Pool of available hitters.
        opp_handedness: Opposing starter's handedness.

    Returns:
        Mapping of position to the assigned HitterStats (9 entries).

    Raises:
        ValueError: If the 9 positions cannot be filled with distinct eligible
            players from the pool.
    """
    # Canonical player order: ascending player_id. Bit i corresponds to pool[i].
    pool = sorted(eligible_players, key=lambda s: s.player_id)
    n = len(pool)

    # score[i][k] = total score of pool[i] at _POSITIONS_TO_FILL[k], or None
    # (forbidden cell) when the player is ineligible for that position.
    score: list[list[float | None]] = []
    for stats in pool:
        row: list[float | None] = []
        for pos in _POSITIONS_TO_FILL:
            breakdown = compute_player_score(stats, pos, opp_handedness)
            row.append(breakdown.total_score if breakdown is not None else None)
        score.append(row)

    # Fast path: name the position in the common infeasible case (no eligible player at all).
    for k, pos in enumerate(_POSITIONS_TO_FILL):
        if all(score[i][k] is None for i in range(n)):
            raise ValueError(f"Cannot fill position {pos}: no eligible player remaining in pool.")

    num_positions = len(_POSITIONS_TO_FILL)
    memo: dict[tuple[int, int], tuple[float, tuple[int, ...]] | None] = {}

    def solve(k: int, used: int) -> tuple[float, tuple[int, ...]] | None:
        """Best (total, player_id tuple) for positions k..end given used players."""
        if k == num_positions:
            return (0.0, ())
        key = (k, used)
        if key in memo:
            return memo[key]
        best: tuple[float, tuple[int, ...]] | None = None
        for i in range(n):  # ascending player_id -> canonical tie-break
            if used & (1 << i):
                continue
            cell = score[i][k]
            if cell is None:
                continue
            sub = solve(k + 1, used | (1 << i))
            if sub is None:
                continue
            cand = (cell + sub[0], (pool[i].player_id, *sub[1]))
            if best is None or cand[0] > best[0] or (cand[0] == best[0] and cand[1] < best[1]):
                best = cand
        memo[key] = best
        return best

    result = solve(0, 0)
    if result is None:
        raise ValueError(
            "Cannot fill all defensive positions: no assignment of distinct "
            "eligible players covers every position."
        )
    _, player_ids = result
    by_id = {stats.player_id: stats for stats in pool}
    return {pos: by_id[pid] for pos, pid in zip(_POSITIONS_TO_FILL, player_ids, strict=True)}


def _assign_batting_order(
    assignments: dict[Position, HitterStats],
    opp_handedness: Handedness,
) -> list[LineupSlot]:
    """Assign batting-order slots using slot-specific reshuffling.

    Slot 1 -> highest OBP
    Slot 4 -> highest SLG
    Slot 3 -> highest OPS (season)
    Remaining slots (2, 5, 6, 7, 8, 9) -> descending composite score
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

    # Slot 1 -- highest OBP
    pos1, s1 = pop_by_key(lambda pos, st: st.obp)
    slots.append(LineupSlot(batting_order=1, player_id=s1.player_id, position=pos1))

    # Slot 4 -- highest SLG
    pos4, s4 = pop_by_key(lambda pos, st: st.slg)
    slots.append(LineupSlot(batting_order=4, player_id=s4.player_id, position=pos4))

    # Slot 3 -- highest OPS (balanced)
    pos3, s3 = pop_by_key(lambda pos, st: st.ops)
    slots.append(LineupSlot(batting_order=3, player_id=s3.player_id, position=pos3))

    # Remaining 6 slots (2, 5, 6, 7, 8, 9) -- descending composite
    remaining_orders = [2, 5, 6, 7, 8, 9]
    for order in remaining_orders:
        pos_r, s_r = pop_by_key(lambda pos, st: composite(pos, st))
        slots.append(LineupSlot(batting_order=order, player_id=s_r.player_id, position=pos_r))

    return slots


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
