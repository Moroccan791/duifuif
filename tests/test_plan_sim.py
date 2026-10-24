"""The stated plan, checked arithmetically rather than argued about."""

from __future__ import annotations

import math

import pytest

from pm5m.fees import fee_as_pct_of_stake
from pm5m.plan_sim import (PlanConfig, bankroll_for_daily, daily_distribution,
                           daily_ev, kelly_stake, simulate)


def test_the_target_is_an_enormous_daily_return():
    """$15/day on $90 is 16.7%/day, which compounds to ~10,000%/month."""
    daily = 15.0 / 90.0
    monthly = (1 + daily) ** 30 - 1
    assert daily == pytest.approx(0.1667, abs=1e-4)
    assert monthly > 50.0  # over 5,000%


def test_no_edge_means_no_stake():
    assert kelly_stake(1000.0, 0.55, 0.55, PlanConfig()) == 0.0


def test_edge_below_the_taker_fee_is_not_tradeable():
    """At 55c the taker fee is 1.73c, so a 1pp edge is negative."""
    fee_pp = fee_as_pct_of_stake(0.55) * 0.55 * 100  # cents per share
    assert fee_pp == pytest.approx(1.73, abs=0.02)
    assert kelly_stake(1000.0, 0.55, 0.56, PlanConfig()) == 0.0


def test_maker_has_no_fee_so_smaller_edges_survive():
    taker = kelly_stake(1000.0, 0.55, 0.56, PlanConfig(maker=False))
    maker = kelly_stake(1000.0, 0.55, 0.56, PlanConfig(maker=True))
    assert taker == 0.0
    assert maker > 0.0


def test_stake_is_capped():
    huge = kelly_stake(1000.0, 0.55, 0.90, PlanConfig(max_stake_pct=0.05))
    assert huge == pytest.approx(50.0)


def test_daily_ev_rises_with_edge_and_capital():
    assert daily_ev(1000.0, 0.05)["per_day"] > daily_ev(1000.0, 0.03)["per_day"]
    assert daily_ev(2000.0, 0.05)["per_day"] > daily_ev(1000.0, 0.05)["per_day"]


def test_bankroll_needed_falls_as_the_edge_grows():
    small = bankroll_for_daily(15.0, 0.03)
    big = bankroll_for_daily(15.0, 0.05)
    assert big < small


def test_bankroll_needed_is_infinite_without_an_edge():
    assert bankroll_for_daily(15.0, 0.0) == math.inf


def test_maker_needs_far_less_capital_than_taker():
    assert bankroll_for_daily(15.0, 0.03, maker=True) < bankroll_for_daily(
        15.0, 0.03, maker=False
    ) / 3


def test_a_fixed_daily_amount_is_impossible():
    """Even at a 5pp edge, a meaningful share of days lose money."""
    d = daily_distribution(90.0, 0.05, days=4000, seed=5)
    assert d["sd"] > d["mean"]   # the spread swamps the target
    assert 0.2 < d["p_loss_day"] < 0.45


def test_more_trades_per_day_narrows_relative_variation():
    few = daily_distribution(90.0, 0.05, trades_per_day=10, days=3000, seed=5)
    many = daily_distribution(90.0, 0.05, trades_per_day=100, days=3000, seed=5)
    # Same mean per dollar but the sd grows slower than the mean (sqrt scaling).
    assert many["mean"] / max(many["sd"], 1e-9) > few["mean"] / max(few["sd"], 1e-9)


def test_simulation_ruin_is_impossible_without_edge_because_it_never_trades():
    res = simulate(PlanConfig(start=90.0, edge=0.0, trades_per_day=40, days=30), paths=200)
    assert res.ruined == 0


def test_simulation_mostly_profits_at_a_large_edge():
    res = simulate(PlanConfig(start=500.0, edge=0.05, trades_per_day=40, days=90, seed=2),
                   paths=300)
    assert res.p_profit > 0.8


def test_simulation_result_is_reproducible():
    a = simulate(PlanConfig(start=90.0, edge=0.03, days=20, seed=11), paths=150)
    b = simulate(PlanConfig(start=90.0, edge=0.03, days=20, seed=11), paths=150)
    assert a.finals == b.finals
