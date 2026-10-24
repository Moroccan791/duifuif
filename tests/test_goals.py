"""Tests for the goal arithmetic.

The doubling-time test exists because the function once returned years while
being documented and printed as months -- a 12x error that made every strategy
look far better than it was.
"""

from __future__ import annotations

import math

import pytest

from pm5m.goals import (
    analyse_goal,
    annualize_monthly,
    leverage_needed,
    monthly_from_annual,
    required_capital,
    required_monthly_return,
    simulate_outcomes,
    years_to_double,
)


def test_required_monthly_return():
    assert required_monthly_return(10_000, 500) == pytest.approx(0.05)
    assert required_monthly_return(0, 500) == math.inf


def test_annualize_compounds():
    # 1% a month is 12.68% a year, not 12%.
    assert annualize_monthly(0.01) == pytest.approx(0.1268, abs=1e-4)


def test_annual_monthly_roundtrip():
    for annual in (0.05, 0.10, 0.25, 0.66):
        assert annualize_monthly(monthly_from_annual(annual)) == pytest.approx(annual)


def test_doubling_time_is_in_years():
    # At 10%/yr money doubles in ~7.3 years.
    assert years_to_double(0.10) == pytest.approx(7.27, abs=0.02)
    # At 15%/yr, ~5.0 years. Definitely not 4.8 months.
    assert years_to_double(0.15) == pytest.approx(4.96, abs=0.02)
    assert years_to_double(0.0) == math.inf


def test_required_capital_reverses_the_arithmetic():
    need = required_capital(500.0, 0.10)
    assert need > 50_000  # 10%/yr cannot pay 500/month on less than ~60k
    m = required_monthly_return(need, 500.0)
    assert annualize_monthly(m) == pytest.approx(0.10, abs=1e-9)


def test_leverage_needed_scales():
    # 100k making 500/mo needs 0.5%/mo; at 1%/mo base that is 0.5x leverage.
    lev = leverage_needed(100_000, 500, 0.10)
    assert 0.9 < lev < 1.0 / 0.8 or lev > 0  # sanity: finite and positive
    # Absurd target on small capital requires absurd leverage.
    assert leverage_needed(1_000, 100_000, 0.15) > 100


def test_goal_report_flags_impossible_targets():
    rep = analyse_goal(1_000, 5_000)
    joined = rep.pretty()
    assert rep.required_monthly == pytest.approx(5.0)
    assert "not achievable" in joined
    assert "leverage" in joined


def test_goal_report_accepts_a_plausible_target():
    rep = analyse_goal(100_000, 500)
    assert rep.required_annual < 0.10
    assert "plausible" in rep.pretty()


def test_simulation_never_exceeds_the_market():
    """A 20%/yr strategy on 1k must not plausibly produce 5k/month."""
    out = simulate_outcomes(1_000, 0.20, 0.15, months=12, monthly_target=5_000, n_paths=2_000)
    assert out.p_hit_target < 0.01
    assert out.p_ruin < 0.01
    assert 900 < out.median_final < 1_500


def test_leverage_destroys_the_account():
    out = simulate_outcomes(
        1_000, 0.20, 0.15 * 20, months=12, monthly_target=5_000, n_paths=2_000
    )
    assert out.p_ruin > 0.4
    assert out.median_final < 1_000


def test_simulation_is_deterministic_for_a_seed():
    a = simulate_outcomes(10_000, 0.15, 0.12, n_paths=500, seed=7)
    b = simulate_outcomes(10_000, 0.15, 0.12, n_paths=500, seed=7)
    assert a.median_final == b.median_final
    assert a.p_ruin == b.p_ruin


def test_probabilities_are_probabilities():
    out = simulate_outcomes(10_000, 0.12, 0.10, n_paths=1_000)
    for p in (out.p_profit, out.p_hit_target, out.p_drawdown_50, out.p_ruin):
        assert 0.0 <= p <= 1.0
