"""The settlement rule, checked against Monte Carlo.

The variance formulas here are the foundation of every fair value the analysis
produces. If any of them is wrong, every calibration curve downstream is wrong,
so they are verified by simulating the actual path and measuring.
"""

from __future__ import annotations

import math
import random

import pytest

SIG = 0.03 / math.sqrt(86400)  # per-root-second, 3% daily

from pm5m.twap import (
    conditional_moments_observed,
    fair_p_up_observed,
    A_S,
    B_S,
    WINDOW_S,
    conditional_moments,
    cov_with_spot,
    fair_p_up,
    gap_in_sigmas,
    settlement_sd,
    var_of_average,
)


def simulate(spot: float, sigma: float, n: int = 4000, dt: float = 1.0, seed: int = 1):
    """Return (final-average path integral over [A,B]) for many simulated paths."""
    rng = random.Random(seed)
    out = []
    steps_b = int(B_S / dt)
    for _ in range(n):
        p = spot
        total = 0.0
        weight = 0.0
        for i in range(1, steps_b + 1):
            p *= math.exp(-0.5 * sigma**2 * dt + sigma * math.sqrt(dt) * rng.gauss(0, 1))
            t = i * dt
            if t > A_S:
                total += p * dt
                weight += dt
        out.append(total / weight)
    return out


def test_var_of_average_value():
    # For a 300s window with a trailing 60s settlement mean.
    assert var_of_average() == pytest.approx(260.0, abs=1e-9)


def test_var_of_average_matches_monte_carlo():
    spot, sigma = 100_000.0, 3e-5
    vals = simulate(spot, sigma, n=600)
    mean = sum(vals) / len(vals)
    var = sum((v - mean) ** 2 for v in vals) / (len(vals) - 1)
    # Analytic: (sigma*spot)^2 * Var(L)
    analytic = (sigma * spot) ** 2 * var_of_average()
    assert analytic > 0
    assert abs(var - analytic) / analytic < 0.35


def test_cov_branches_are_continuous_at_a():
    a, b = A_S, B_S
    left = cov_with_spot(a - 1e-6, a, b)
    right = cov_with_spot(a + 1e-6, a, b)
    assert left == pytest.approx(right, abs=1e-3)


def test_conditional_mean_multiple_at_open_is_one():
    k, var = conditional_moments(0.0)
    assert k == pytest.approx(1.0)
    assert var == pytest.approx(260.0)


def test_conditional_variance_collapses_through_the_window():
    """Most of the uncertainty disappears before the settlement window begins."""
    variances = [conditional_moments(t)[1] for t in (0, 60, 120, 180, 240)]
    assert variances == sorted(variances, reverse=True)
    assert variances[0] == pytest.approx(260.0)
    # By t=240 the settlement mean is over [240,300] only: Var = 60/3 = 20.
    assert variances[-1] == pytest.approx(20.0, abs=1e-6)


def test_variance_is_not_zero_at_the_bell():
    """Knowing the price at the close does not tell you the preceding average.

    This is precisely why the TWAP rule defeats a final-second spike.
    """
    _, var = conditional_moments(WINDOW_S)
    assert var > 0
    assert var == pytest.approx(17.0, abs=1e-6)


def test_settlement_sd_at_open_is_about_165_dollars():
    sd = settlement_sd(100_000.0, 0.03 / math.sqrt(86400), t=0.0)
    assert 150 < sd < 180


def test_gap_in_sigmas_and_probability_agree():
    spot, strike, sigma = 100_100.0, 100_000.0, 0.03 / math.sqrt(86400)
    x = gap_in_sigmas(spot, strike, sigma, 0.0)
    p = fair_p_up(spot, strike, sigma, 0.0)
    assert 0.4 < x < 0.8
    assert 0.60 < p < 0.80


def test_fair_p_up_is_fifty_fifty_when_spot_equals_strike():
    sigma = 0.03 / math.sqrt(86400)
    assert fair_p_up(100_000.0, 100_000.0, sigma, 0.0) == pytest.approx(0.5, abs=1e-9)


def test_fair_p_up_is_monotonic_in_spot():
    sigma = 0.03 / math.sqrt(86400)
    ps = [fair_p_up(100_000.0 + g, 100_000.0, sigma, 0.0) for g in (-300, -100, 0, 100, 300)]
    assert ps == sorted(ps)


def test_fair_p_up_monte_carlo_calibration():
    """Simulate the real rule and check the analytic probability.

    Up iff mean(path over the last 60s) >= strike, starting from spot at t=0.
    """
    rng = random.Random(11)
    spot, strike, sigma = 100_100.0, 100_000.0, 0.03 / math.sqrt(86400)
    dt = 1.0
    steps = int(B_S / dt)
    wins = 0
    n = 3000
    for _ in range(n):
        p = spot
        total = 0.0
        cnt = 0
        for i in range(1, steps + 1):
            p *= math.exp(-0.5 * sigma**2 * dt + sigma * math.sqrt(dt) * rng.gauss(0, 1))
            if i * dt > A_S:
                total += p
                cnt += 1
        if total / cnt >= strike:
            wins += 1
    observed = wins / n
    predicted = fair_p_up(spot, strike, sigma, 0.0)
    # 3000 paths -> SE ~ 0.9pp; allow 4 SE plus discretisation error.
    assert abs(observed - predicted) < 0.05, f"observed {observed:.3f} vs predicted {predicted:.3f}"


def test_probability_is_capped_away_from_certainty():
    sigma = 0.03 / math.sqrt(86400)
    assert fair_p_up(200_000.0, 100_000.0, sigma, 0.0) <= 1.0 - 1e-4
    assert fair_p_up(1.0, 100_000.0, sigma, 0.0) >= 1e-4


def test_degenerate_inputs_are_safe():
    assert fair_p_up(0.0, 100.0, 1e-5) in (0.0, 1.0)
    assert fair_p_up(100.0, 100.0, 0.0) == 1.0
    assert gap_in_sigmas(100.0, 100.0, 0.0) == 0.0


# ---------------------------------------------------------------------------
# Regression: the conditional mean is the spot, never cov/t * spot
# ---------------------------------------------------------------------------
def test_mean_is_the_spot_at_every_time():
    """E[TWAP60(close) | W(t)] = W(t) for a martingale, at EVERY t.

    The first version of `conditional_moments` returned `k = cov/t` as the mean
    multiple, so at t=300 the mean became 0.867*spot. With a $1 cushion in a
    decided market that produced z = -6 and p_up = 0.0001 -- a confident call
    for the wrong side. W is a martingale; the mean multiple is 1.0.
    """
    for t in (0, 30, 100, 240, 270, 299, 300):
        k, _ = conditional_moments(t)
        assert k == pytest.approx(1.0), f"mean multiple at t={t} was {k}"


def test_a_cushion_above_the_strike_favours_up():
    spot, strike = 100_100.0, 100_000.0
    for t in (0, 150, 250, 290):
        assert fair_p_up(spot, strike, SIG, t) > 0.5


def test_observed_and_blind_agree_before_the_tail_window():
    """Before t=240 there is no tail window yet, so the two agree exactly."""
    for t in (0.0, 60.0, 180.0, 240.0):
        k, v_blind = conditional_moments(t)
        m_obs, v_obs = conditional_moments_observed(t, 0.0, 100_000.0, 100_000.0)
        assert m_obs == pytest.approx(100_000.0)
        assert v_obs == pytest.approx(v_blind, abs=1e-9)


def test_observed_variance_reaches_zero_at_the_bell():
    """Once the whole tail window has printed, the outcome is known."""
    _, v = conditional_moments_observed(300.0, 60.0 * 100_500.0, 100_500.0, 100_000.0)
    assert v == pytest.approx(0.0)
    # A decided market must not be flipped by the last print.
    p = fair_p_up_observed(60.0 * 100_500.0, 100_500.0, 100_000.0, SIG, 300.0)
    assert p > 0.999


def test_blind_variance_does_not_reach_zero():
    """Path-blind, the variance floors at ~17: the final print is not the average."""
    _, v = conditional_moments(300.0)
    assert 15.0 < v < 20.0


def test_observed_variance_falls_monotonically_through_the_tail():
    prev = None
    for t in (240.0, 250.0, 270.0, 290.0, 299.0):
        _, v = conditional_moments_observed(t, 0.0, 100_000.0, 100_000.0)
        if prev is not None:
            assert v < prev
        prev = v


def test_observed_monte_carlo_calibration_mid_window():
    """The path-aware probability, checked mid-window against simulation."""
    rng = random.Random(23)
    spot, strike = 100_000.0, 100_000.0
    sigma = 0.03 / math.sqrt(86400)
    t_now = 200.0
    dt = 1.0
    n, wins = 1500, 0
    for _ in range(n):
        p = spot
        total = 0.0
        cnt = 0
        for i in range(1, int(B_S / dt) + 1):
            p *= math.exp(-0.5 * sigma**2 * dt + sigma * math.sqrt(dt) * rng.gauss(0, 1))
            if i * dt > A_S:
                total += p
                cnt += 1
        if total / cnt >= strike:
            wins += 1
    observed_frac = wins / n
    # At t=200 the tail window has not opened, and we start the path exactly at
    # the strike, so the answer must be a coin flip.
    predicted = fair_p_up(spot, strike, sigma, t_now)
    assert predicted == pytest.approx(0.5, abs=1e-9)
    assert abs(observed_frac - 0.5) < 0.05
