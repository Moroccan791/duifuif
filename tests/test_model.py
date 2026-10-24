"""Tests for the settlement index and the probability model.

The Monte Carlo tests are the important ones: they check the analytic variance of
the integral of GBM against simulation, which is the single formula the whole
strategy rests on. If that formula is wrong, every signal is wrong.
"""

from __future__ import annotations

import math
import random

import pytest

from pm5m.model import (
    fair_up,
    norm_cdf,
    p_up_from_analytic,
    remaining_integral_moments,
)
from pm5m.settlement import SettlementIndex


def integrate_gbm(rng: random.Random, spot: float, sigma: float, tau: float, steps: int = 300) -> float:
    dt = tau / steps
    total = 0.0
    p = spot
    for _ in range(steps):
        p_next = p * math.exp(-0.5 * sigma**2 * dt + sigma * math.sqrt(dt) * rng.gauss(0, 1))
        total += (p + p_next) / 2 * dt
        p = p_next
    return total


def test_integral_mean_matches_monte_carlo():
    rng = random.Random(1)
    spot, sigma, tau = 100_000.0, 1e-4, 300.0
    draws = [integrate_gbm(rng, spot, sigma, tau, steps=60) for _ in range(400)]
    mean = sum(draws) / len(draws)
    expected_mean, _ = remaining_integral_moments(spot, tau, sigma)
    assert abs(mean - expected_mean) / expected_mean < 0.01


def test_integral_variance_matches_monte_carlo():
    rng = random.Random(2)
    spot, sigma, tau = 100_000.0, 2e-4, 240.0
    draws = [integrate_gbm(rng, spot, sigma, tau, steps=120) for _ in range(600)]
    mean = sum(draws) / len(draws)
    var = sum((d - mean) ** 2 for d in draws) / (len(draws) - 1)
    _, analytic = remaining_integral_moments(spot, tau, sigma)
    assert analytic > 0
    # Within 15% -- Monte Carlo of an integral is noisy by nature.
    assert abs(var - analytic) / analytic < 0.15


def test_small_sigma_variance_matches_brownian_limit():
    spot, sigma, tau = 50_000.0, 5e-5, 300.0
    _, var = remaining_integral_moments(spot, tau, sigma)
    brownian = spot**2 * sigma**2 * tau**3 / 3.0
    assert abs(var - brownian) / brownian < 0.02


def test_zero_tau_collapses_to_certainty():
    state = SettlementIndex(start_ts=0.0, end_ts=300.0, open_price=100.0)
    state.add(10.0, 101.0)
    view = fair_up(state, 101.0, 1e-4, 300.0)
    assert view.p_up > 0.99
    assert view.remaining_s == 0.0


def test_probability_is_symmetric_at_the_open():
    """At the open with a flat path, Up and Down should be near 50/50."""
    state = SettlementIndex(start_ts=0.0, end_ts=300.0, open_price=100.0)
    for t in range(0, 2):
        state.add(float(t), 100.0)
    view = fair_up(state, 100.0, 1e-4, 1.0)
    assert 0.45 < view.p_up < 0.55


def test_higher_spot_raises_p_up():
    state = SettlementIndex(start_ts=0.0, end_ts=300.0, open_price=100_000.0)
    for t in range(0, 120, 5):
        state.add(float(t), 100_000.0)
    # Fair value now depends on where spot sits relative to the open.
    low = fair_up(state, 99_900.0, 1e-4, 120.0).p_up
    high = fair_up(state, 100_100.0, 1e-4, 120.0).p_up
    assert high > low


def test_more_time_left_means_more_uncertainty():
    state = SettlementIndex(start_ts=0.0, end_ts=300.0, open_price=100_000.0)
    for t in range(0, 60, 5):
        state.add(float(t), 100_050.0)
    early = fair_up(state, 100_050.0, 1e-4, 60.0)
    late = fair_up(state, 100_050.0, 1e-4, 290.0)
    assert late.sd_price < early.sd_price
    assert abs(late.p_up - 0.5) > abs(early.p_up - 0.5)


def test_settlement_index_has_no_lookahead():
    state = SettlementIndex(start_ts=0.0, end_ts=300.0, open_price=100.0)
    state.add(10.0, 100.0)
    state.add(200.0, 200.0)  # a huge future move
    at_50 = state.known_integral(50.0)
    # 10s at 100 then flat to 50s at 100 => exactly 5000
    assert at_50 == pytest.approx(5000.0)
    full = state.known_integral(300.0)
    assert full > at_50


def test_resolution_ties_go_up():
    state = SettlementIndex(start_ts=0.0, end_ts=10.0, open_price=100.0)
    state.add(0.0, 100.0)
    state.add(10.0, 100.0)
    assert state.resolved_outcome() == "up"


def test_resolution_down_when_average_below_open():
    state = SettlementIndex(start_ts=0.0, end_ts=10.0, open_price=100.0)
    state.add(0.0, 100.0)
    state.add(5.0, 90.0)
    state.add(10.0, 90.0)
    assert state.resolved_outcome() == "down"


def test_norm_cdf_extremes():
    assert norm_cdf(0.0) == pytest.approx(0.5)
    assert norm_cdf(-8.0) < 1e-10
    assert norm_cdf(8.0) > 1 - 1e-10


def test_z_is_capped():
    state = SettlementIndex(start_ts=0.0, end_ts=300.0, open_price=100.0)
    state.add(0.0, 100.0)
    state.add(1.0, 10_000.0)
    view = fair_up(state, 10_000.0, 1e-5, 1.0, z_cap=6.0)
    assert -6.0 <= view.z <= 6.0
    assert view.p_up <= 1.0 - 1e-4


def test_analytic_matches_fair_up_wrapper():
    state = SettlementIndex(start_ts=0.0, end_ts=300.0, open_price=100_000.0)
    for t in range(0, 100, 5):
        state.add(float(t), 100_010.0)
    view = fair_up(state, 100_010.0, 1e-4, 100.0)
    direct = p_up_from_analytic(
        state.open_price, state.known_integral(100.0), 100_010.0, 200.0, 300.0, 1e-4
    )
    assert view.p_up == pytest.approx(direct, abs=1e-9)
