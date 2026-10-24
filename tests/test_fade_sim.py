"""The income maths: capital, risk, and the ways this can look better than it is."""

from __future__ import annotations

import pytest

from pm5m.fade_sim import (
    FadeConfig,
    capital_for_daily_target,
    expected_daily_pnl,
    simulate,
    win_profit_per_dollar,
)

DAY = 86_400.0
T0 = 1_735_689_600.0          # 2025-01-01 UTC


def windows(n_days: int, per_day: int, move: float = 0.01, win: bool = True):
    """`per_day` bets a day, 5 minutes apart, all the same size and outcome."""
    out = []
    for d in range(n_days):
        for k in range(per_day):
            ts = T0 + d * DAY + k * 300.0
            # move > 0 means we bet DOWN, so a win needs up == 0
            up = (0 if move > 0 else 1) if win else (1 if move > 0 else 0)
            out.append((ts, up, move))
    return out


def test_win_payout_matches_the_fee_algebra():
    # pay 50c plus 1.75c of fee, a winner pays 1.00
    assert win_profit_per_dollar(FadeConfig(fill=0.50)) == pytest.approx(0.9324, abs=1e-3)


def test_expected_daily_profit_is_linear_in_bankroll():
    w = windows(30, 8)
    cfg = FadeConfig(fill=0.50)
    a = expected_daily_pnl(w, 1_000.0, cfg)
    b = expected_daily_pnl(w, 2_000.0, cfg)
    assert a > 0
    assert b == pytest.approx(2 * a, rel=1e-9)


def test_capital_scales_inversely_with_the_target_and_the_edge():
    w = windows(30, 8)
    cfg = FadeConfig(fill=0.50)
    assert capital_for_daily_target(w, 30.0, cfg) == pytest.approx(
        2 * capital_for_daily_target(w, 15.0, cfg), rel=1e-9)
    # a weaker true edge needs more capital, and one below break-even needs all of it
    assert capital_for_daily_target(w, 15.0, cfg, p_true=0.5305) > \
        capital_for_daily_target(w, 15.0, cfg, p_true=0.5476)
    # 0.5% moves only clear 53.91%, so at 54c every bet is skipped outright
    small = windows(30, 8, move=0.005)
    assert capital_for_daily_target(small, 15.0, FadeConfig(fill=0.54)) == float("inf")


def test_all_wins_grow_and_all_losses_shrink():
    cfg = FadeConfig(fill=0.50, min_stake=0.0)
    up = simulate(windows(10, 5, win=True), 1_000.0, cfg, compound=False)
    down = simulate(windows(10, 5, win=False), 1_000.0, cfg, compound=False)
    assert up.trades == 50 and up.wins == 50 and up.hit_rate == 1.0
    assert up.mean_daily_pnl > 0
    assert down.wins == 0 and down.mean_daily_pnl < 0


def test_concurrency_cap_binds_when_the_same_event_fires_three_ways():
    """BTC, ETH and SOL at once is one trade. The cap must see the total."""
    ts = T0
    w = [(ts, 1, 0.01), (ts + 1, 1, 0.01), (ts + 2, 1, 0.01)]     # three losers
    cfg = FadeConfig(fill=0.50, kelly_mult=1.0, max_stake_pct=0.50,
                     max_concurrent_pct=0.02, min_stake=1.0)
    res = simulate(w, 1_000.0, cfg, compound=False)
    # read the P&L, not final_equity: compound=False restores the float, which
    # would hide the loss entirely (this test failed that way first time).
    lost = -sum(res.daily_pnl)
    assert 0 < lost <= 20.0 + 1e-6         # 2% of a 1,000 bankroll, no more
    assert res.trades <= 1                 # the later two found no room


def test_separate_events_do_not_trip_the_cap():
    w = [(T0, 1, 0.01), (T0 + 600, 1, 0.01)]     # ten minutes apart, settled
    cfg = FadeConfig(fill=0.50, kelly_mult=1.0, max_stake_pct=0.50,
                     max_concurrent_pct=0.02, min_stake=0.0)
    res = simulate(w, 1_000.0, cfg, compound=False)
    assert res.trades == 2


def test_flat_bankroll_does_not_compound():
    """compound=False is the income model: stake size must not drift upward."""
    cfg = FadeConfig(fill=0.50, min_stake=0.0)
    res = simulate(windows(60, 8, win=True), 1_000.0, cfg, compound=False)
    grown = simulate(windows(60, 8, win=True), 1_000.0, cfg, compound=True)
    assert res.final_equity == pytest.approx(1_000.0, abs=1e-6)
    assert grown.final_equity > 1_000.0


def test_stakes_below_the_minimum_are_skipped():
    cfg = FadeConfig(fill=0.50, min_stake=100.0)
    res = simulate(windows(10, 5), 1_000.0, cfg, compound=False)
    assert res.trades == 0


def test_withdrawals_are_capped_by_the_bankroll_and_counted():
    cfg = FadeConfig(fill=0.50, min_stake=0.0)
    res = simulate(windows(20, 2, win=False), 100.0, cfg,
                   withdraw_per_day=15.0, compound=True)
    assert res.withdrawn <= 20 * 15.0
    assert res.short_day_pct > 0.5        # losing days cannot fund the target
