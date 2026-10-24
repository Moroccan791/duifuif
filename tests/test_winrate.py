"""Tests for the win-rate / profit decoupling.

The central claim this module makes is that win rate alone is uninformative --
the same win rate is profitable or ruinous depending only on price. These tests
pin that down, because a tool that let someone believe "70% win rate = good"
would be worse than useless.
"""

from __future__ import annotations

import pytest

from pm5m.winrate import (
    accuracy_you_need,
    all_in_cost,
    break_even_win_rate,
    expectancy_on_stake,
    expectancy_per_share,
    expected_longest_loss_streak,
    loss_to_win_ratio,
    prob_streak_at_least,
    same_winrate_scenarios,
    table,
)


def test_break_even_equals_all_in_cost():
    """A winning share pays 1.00, so break-even accuracy IS the cost per share."""
    for p in (0.5, 0.6, 0.78, 0.95):
        assert break_even_win_rate(p) == pytest.approx(all_in_cost(p))


def test_all_in_cost_adds_the_fee():
    assert all_in_cost(0.55) == pytest.approx(0.55 + 0.07 * 0.55 * 0.45)


def test_fair_price_has_negative_expectancy_because_of_the_fee():
    """At the market's own probability you lose exactly the fee."""
    for p in (0.5, 0.6, 0.8):
        ev = expectancy_per_share(p, p)  # win rate == price
        assert ev < 0
        assert ev == pytest.approx(-(all_in_cost(p) - p))


def test_expectancy_zero_at_exactly_break_even():
    p = 0.78
    be = break_even_win_rate(p)
    assert expectancy_per_share(be, p) == pytest.approx(0.0, abs=1e-12)


def test_expectancy_positive_only_above_break_even():
    p = 0.78
    be = break_even_win_rate(p)
    assert expectancy_per_share(be - 0.001, p) < 0
    assert expectancy_per_share(be + 0.001, p) > 0


def test_same_win_rate_opposite_outcomes():
    """THE central result: 78% wins is hugely profitable at 60c, ruinous at 90c."""
    w = 0.78
    # At 60c, a 78% win rate is an 18-point edge over the price.
    assert expectancy_per_share(w, 0.60) > 0.15
    # At 72c it is marginal.
    assert abs(expectancy_per_share(w, 0.72)) < 0.06
    # At 90c it is a clear loss despite the SAME win rate.
    assert expectancy_per_share(w, 0.90) < 0
    assert expectancy_on_stake(w, 0.90) < 0


def test_loss_to_win_ratio_grows_with_price():
    assert loss_to_win_ratio(0.5) == pytest.approx(1.0)
    assert loss_to_win_ratio(0.78) == pytest.approx(0.78 / 0.22)
    assert loss_to_win_ratio(0.95) == pytest.approx(19.0)
    assert loss_to_win_ratio(0.9) > loss_to_win_ratio(0.8) > loss_to_win_ratio(0.7)


def test_scenario_text_reports_a_profit_and_a_loss():
    text = "\n".join(same_winrate_scenarios(0.78))
    assert "PROFIT" in text
    assert "LOSS" in text
    # The 60c case must show a positive number and the 90c case a negative one.
    lines = [l for l in text.splitlines() if "buy at" in l]
    assert "+" in lines[0]
    assert "-" in lines[-1]


def test_table_edge_needed_matches_fee():
    for row in table():
        assert row.edge_needed_pp == pytest.approx(
            (all_in_cost(row.price) - row.price) * 100, abs=1e-9
        )


def test_table_edge_needed_shrinks_at_high_prices():
    rows = {r.price: r for r in table()}
    assert rows[0.98].edge_needed_pp < rows[0.50].edge_needed_pp
    # But the loss multiple explodes.
    assert rows[0.98].loss_win_ratio > rows[0.50].loss_win_ratio * 40


def test_streaks_are_expected_not_exceptional():
    """At 80% you should expect a 4-5 loss run in 1,000 trades."""
    streak = expected_longest_loss_streak(0.80, 1000)
    assert 3.5 < streak < 5.5
    # Longer samples have longer streaks.
    assert expected_longest_loss_streak(0.80, 10_000) > streak


def test_streak_probability_rises_with_trade_count():
    a = prob_streak_at_least(0.80, 4, 100)
    b = prob_streak_at_least(0.80, 4, 1000)
    assert b > a
    assert 0.0 <= a <= 1.0 and 0.0 <= b <= 1.0


def test_high_win_rate_has_shorter_streaks_but_bigger_losses():
    hi = expected_longest_loss_streak(0.95, 1000)
    lo = expected_longest_loss_streak(0.78, 1000)
    assert hi < lo
    assert loss_to_win_ratio(0.95) > loss_to_win_ratio(0.78)


def test_accuracy_text_uses_percentage_points():
    text = accuracy_you_need(0.78)
    assert "pp" in text
    assert "79.20%" in text


def test_degenerate_inputs_are_safe():
    assert expected_longest_loss_streak(1.0, 1000) == 0.0
    assert expected_longest_loss_streak(0.8, 1) == 0.0
    assert prob_streak_at_least(1.0, 5, 1000) == 0.0
