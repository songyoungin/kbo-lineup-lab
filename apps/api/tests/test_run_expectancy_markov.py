from __future__ import annotations

import pytest

from app.lineup_model.run_expectancy.markov import expected_runs
from app.lineup_model.run_expectancy.rates import EventRates

_ALL_OUT = EventRates(bb=0.0, single=0.0, double=0.0, triple=0.0, hr=0.0, out=1.0)
_ALL_HR = EventRates(bb=0.0, single=0.0, double=0.0, triple=0.0, hr=1.0, out=0.0)


def _order(rates: EventRates) -> tuple[EventRates, ...]:
    return tuple(rates for _ in range(9))


def test_all_out_lineup_scores_zero() -> None:
    assert expected_runs(_order(_ALL_OUT)) == pytest.approx(0.0, abs=1e-12)


def test_all_hr_lineup_scores_unbounded_capped_by_pa_budget() -> None:
    runs = expected_runs(_order(_ALL_HR), innings=1)
    assert runs > 20.0


def test_sequencing_matters() -> None:
    """A high-OBP table-setter ahead of sluggers outscores the reverse order."""
    setter = EventRates(bb=0.20, single=0.20, double=0.0, triple=0.0, hr=0.0, out=0.60)
    slugger = EventRates(bb=0.0, single=0.0, double=0.0, triple=0.0, hr=0.25, out=0.75)
    filler = EventRates(bb=0.0, single=0.0, double=0.0, triple=0.0, hr=0.0, out=1.0)
    good = (setter, setter, slugger, slugger, filler, filler, filler, filler, filler)
    bad = (slugger, slugger, setter, setter, filler, filler, filler, filler, filler)
    assert expected_runs(good) > expected_runs(bad)


def test_deterministic_and_input_independent() -> None:
    a = EventRates(bb=0.1, single=0.2, double=0.05, triple=0.01, hr=0.04, out=0.60)
    b = EventRates(bb=0.08, single=0.18, double=0.04, triple=0.0, hr=0.05, out=0.65)
    order = (a, b, a, b, a, b, a, b, a)
    assert expected_runs(order) == expected_runs(order)


def test_more_innings_scores_more() -> None:
    r = EventRates(bb=0.1, single=0.2, double=0.05, triple=0.01, hr=0.04, out=0.60)
    assert expected_runs(_order(r), innings=9) > expected_runs(_order(r), innings=3)
