from __future__ import annotations

import pytest

from app.lineup_model.run_expectancy.rates import EventRates, event_rates


def _total(r: EventRates) -> float:
    return r.bb + r.single + r.double + r.triple + r.hr + r.out


def test_probabilities_sum_to_one() -> None:
    r = event_rates(obp=0.350, slg=0.450)
    assert _total(r) == pytest.approx(1.0, abs=1e-9)


def test_out_rate_complements_obp() -> None:
    r = event_rates(obp=0.350, slg=0.450)
    assert r.out == pytest.approx(1.0 - 0.350, abs=1e-9)


def test_higher_slg_gives_more_extra_base_power() -> None:
    low = event_rates(obp=0.340, slg=0.380)
    high = event_rates(obp=0.340, slg=0.560)
    assert high.hr > low.hr
    assert high.single < low.single


def test_higher_obp_gives_more_baserunners() -> None:
    low = event_rates(obp=0.300, slg=0.420)
    high = event_rates(obp=0.400, slg=0.420)
    assert (high.bb + high.single + high.double + high.triple + high.hr) > (
        low.bb + low.single + low.double + low.triple + low.hr
    )


def test_degenerate_inputs_are_clamped_and_normalised() -> None:
    r = event_rates(obp=0.500, slg=0.500)
    assert _total(r) == pytest.approx(1.0, abs=1e-9)
    assert all(v >= 0.0 for v in (r.bb, r.single, r.double, r.triple, r.hr, r.out))
    zero = event_rates(obp=0.000, slg=0.000)
    assert zero.out == pytest.approx(1.0, abs=1e-9)
    clamped = event_rates(obp=1.5, slg=-0.2)  # obp clamps to 0.999, slg to 0.0
    assert _total(clamped) == pytest.approx(1.0, abs=1e-9)
    assert all(
        v >= 0.0
        for v in (
            clamped.bb,
            clamped.single,
            clamped.double,
            clamped.triple,
            clamped.hr,
            clamped.out,
        )
    )
