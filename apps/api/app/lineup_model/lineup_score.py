"""Lineup-level scoring: expected runs (run-expectancy model) with handedness adjustment.

Inputs are pure Python objects; no database access.
"""

from __future__ import annotations

from app.lineup_model.run_expectancy.markov import expected_runs
from app.lineup_model.run_expectancy.optimizer import handedness_penalty
from app.lineup_model.run_expectancy.rates import event_rates
from app.lineup_model.types import (
    Handedness,
    HitterStats,
    LineupScoreBreakdown,
    LineupSlot,
    ScoringReason,
)

# ---------------------------------------------------------------------------
# Lineup score
# ---------------------------------------------------------------------------


def compute_lineup_score(
    slots: tuple[LineupSlot, ...],
    stats_by_player: dict[int, HitterStats],
    opp_handedness: Handedness,
) -> LineupScoreBreakdown:
    """Score a lineup by its expected runs (deterministic run-expectancy model).

    The batting order in ``slots`` is evaluated by the analytic Markov model; a
    small run-unit penalty discourages long same-handedness streaks. Returns a
    LineupScoreBreakdown whose ``weighted_player_score`` carries the raw expected
    runs and ``total_score`` = expected runs + handedness adjustment.

    Args:
        slots: 9 lineup slots with batting_order, player_id, position.
        stats_by_player: Full HitterStats for every player in the lineup.
        opp_handedness: Opposing starter's handedness (reserved for future use).

    Returns:
        LineupScoreBreakdown with all components.
    """
    ordered = sorted(slots, key=lambda s: s.batting_order)
    rates = tuple(
        event_rates(stats_by_player[s.player_id].obp, stats_by_player[s.player_id].slg)
        for s in ordered
    )
    runs = expected_runs(rates)
    hand_adj = -handedness_penalty([(s.position, stats_by_player[s.player_id]) for s in ordered])
    total = runs + hand_adj

    reasons: list[ScoringReason] = [
        ScoringReason(
            component="expected_runs",
            value=runs,
            weight=1.0,
            note=f"{len(ordered)} batters",
        ),
        ScoringReason(
            component="handedness_balance",
            value=hand_adj,
            weight=1.0,
            note=f"penalty={hand_adj}",
        ),
    ]
    return LineupScoreBreakdown(
        slots=tuple(slots),
        weighted_player_score=runs,
        position_completeness_adjustment=0.0,
        handedness_balance_adjustment=hand_adj,
        total_score=total,
        reasons=tuple(reasons),
    )
