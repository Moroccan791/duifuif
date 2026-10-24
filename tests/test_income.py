"""The one-decision-a-day interface, and the capital arithmetic."""

from __future__ import annotations

import pytest

from chartbot.data import Candle, trending_candles
from chartbot.income import capital_for, daily_from_annual, months_to_double, today
from chartbot.position import PositionConfig


def test_uptrend_says_buy_and_downtrend_says_sell():
    up = today(trending_candles(n=300, up=True, seed=3))
    assert up.action == "buy" and up.target > 0
    down = today(trending_candles(n=300, up=False, seed=3))
    assert down.action == "sell" and down.target == 0.0
    assert "SELL" in down.line(100.0)


def test_the_line_states_the_dollars_not_just_a_fraction():
    call = today(trending_candles(n=300, up=True, seed=3), equity=1_000.0)
    line = call.line(1_000.0)
    assert line.startswith("BUY") and "$" in line
    assert f"{call.target * 1_000:,.2f}" in line


def test_low_volatility_means_a_bigger_position():
    """Same direction, different volatility: size must move inversely."""
    def series(swing: float, n: int = 201):
        out = []
        for i in range(n):
            px = 100 * (1 + 0.0005 * i) * (1 + swing if i % 2 == 0 else 1.0)
            out.append(Candle(ts=float(i), open=px, high=px, low=px, close=px,
                              volume=1.0))
        return out

    calm = today(series(0.0))        # drift only: vol ~0, target capped at 1x
    wild = today(series(0.03))       # +/-3% a day: vol ~57%, target ~0.44
    assert calm.action == "buy" and wild.action == "buy"
    assert calm.target == pytest.approx(1.0)
    assert wild.target < 0.6
    assert calm.target > wild.target


def test_too_little_history_is_an_error_not_a_guess():
    with pytest.raises(ValueError):
        today(trending_candles(n=10))


def test_capital_scales_linearly_and_never_goes_negative():
    assert capital_for(10.0, 0.20) == pytest.approx(18_250.0)
    assert capital_for(20.0, 0.20) == pytest.approx(2 * capital_for(10.0, 0.20))
    assert capital_for(10.0, 0.0) == float("inf")


def test_daily_income_matches_the_annual_figure():
    assert daily_from_annual(10_000.0, 0.365) == pytest.approx(10.0)
    assert daily_from_annual(0.0, 0.4) == 0.0


def test_doubling_time():
    assert months_to_double(0.20) / 12 == pytest.approx(3.8, abs=0.05)
    assert months_to_double(0.0) == float("inf")


def test_the_honest_answer_at_100_dollars():
    """If this test ever changes, the whole pitch changes with it."""
    assert daily_from_annual(100.0, 0.412) < 0.12      # $0.11 a day
    assert capital_for(10.0, 0.412) > 8_000            # not a $100 business
