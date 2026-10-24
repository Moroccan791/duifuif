"""The chart bot: reader, signals, sizing, broker safety, render."""

from __future__ import annotations

import pytest

from chartbot.broker import (BinanceBroker, BrokerError, PaperBroker,
                             Position, RiskLimits)
from chartbot.data import Candle
from chartbot.data import candle_from_binance, synthetic_candles, trending_candles
from chartbot.reader import atr, ema, read_chart, rsi, swings
from chartbot.render import render_svg
from chartbot.runner import Runner, RunnerConfig, backtest
from chartbot.signals import SignalConfig, evaluate


# ---- indicators -----------------------------------------------------------
def test_ema_tracks_a_constant_series():
    assert ema([5.0] * 50, 10) == pytest.approx([5.0] * 50)


def test_rsi_of_a_monotonic_rise_is_maxed():
    assert rsi([float(i) for i in range(1, 40)]) == pytest.approx(100.0)


def test_rsi_of_a_monotonic_fall_is_zero():
    assert rsi([float(100 - i) for i in range(1, 40)]) == pytest.approx(0.0)


def test_rsi_is_neutral_with_too_little_data():
    assert rsi([1.0, 2.0, 3.0]) == 50.0


def test_atr_is_positive_and_scales_with_range():
    calm = [Candle(i, 100, 101, 99, 100, 10) for i in range(30)]
    wild = [Candle(i, 100, 110, 90, 100, 10) for i in range(30)]
    assert atr(calm) == pytest.approx(2.0)
    assert atr(wild) > atr(calm) * 5


def test_swings_finds_local_extremes():
    c = [Candle(i, 100, 100 + (i % 5), 100 - (i % 5), 100, 1) for i in range(60)]
    highs, lows = swings(c, k=2)
    assert len(highs) > 0 and len(lows) > 0


# ---- the reader -----------------------------------------------------------
def test_reader_sees_an_uptrend():
    read = read_chart(trending_candles(n=200, up=True, seed=2))
    assert read.trend == "up"
    assert read.trend_strength > 0.5
    assert read.ema_fast > read.ema_slow


def test_reader_sees_a_downtrend():
    read = read_chart(trending_candles(n=200, up=False, seed=2))
    assert read.trend == "down"
    assert read.trend_strength < -0.5
    assert read.ema_fast < read.ema_slow


def test_reader_reports_position_within_the_range():
    read = read_chart(synthetic_candles(n=200, seed=3))
    assert 0.0 <= read.range_pos <= 1.0


def test_reader_places_support_below_and_resistance_above():
    read = read_chart(synthetic_candles(n=200, seed=3))
    assert read.support <= read.price <= read.resistance


def test_reader_narrates_in_plain_english():
    text = read_chart(synthetic_candles(n=120, seed=4)).narrate()
    assert "trend" in text and "momentum" in text and text.endswith(".")


def test_reader_needs_enough_candles():
    with pytest.raises(ValueError):
        read_chart(synthetic_candles(n=3))


def test_reader_detects_a_doji():
    c = synthetic_candles(n=40, seed=5)
    c.append(Candle(c[-1].ts + 300, 100.0, 100.5, 99.5, 100.01, 10.0))
    read = read_chart(c)
    assert "doji" in read.patterns


# ---- signals --------------------------------------------------------------
def test_no_trade_when_volatility_is_absurd():
    c = [Candle(i, 100, 130, 70, 100, 10) for i in range(60)]
    sig = evaluate(read_chart(c), 1000.0)
    assert sig.blocked and "volatility too high" in sig.blocked


def test_no_trade_when_volatility_is_dead():
    c = [Candle(i, 100.0, 100.01, 99.99, 100.0, 10) for i in range(60)]
    sig = evaluate(read_chart(c), 1000.0)
    assert sig.blocked and "volatility too low" in sig.blocked


def test_no_stack_of_trades_while_holding():
    read = read_chart(synthetic_candles(n=200, seed=3))
    sig = evaluate(read, 1000.0, has_position=True)
    assert not sig.tradeable
    assert sig.blocked == "in position"


def test_confidence_never_exceeds_the_number_of_checks():
    read = read_chart(synthetic_candles(n=200, seed=3))
    sig = evaluate(read, 1000.0)
    assert 0 <= sig.confidence <= 6


# ---- sizing ---------------------------------------------------------------
def test_sizing_is_capped_by_spot_capital_not_by_risk():
    """A 2% risk with a tight stop implies huge leverage; spot cannot."""
    c = synthetic_candles(n=120, seed=6, vol=0.002)
    read = read_chart(c)
    sig = evaluate(read, 100.0, SignalConfig(risk_pct=0.02, max_notional_pct=0.5))
    if sig.tradeable:
        assert sig.size * read.price <= 50.0 + 1e-6


def test_position_size_never_negative():
    from chartbot.signals import _position_size
    units, risk = _position_size(100.0, 0.0, 10.0, 0.02)
    assert units == 0.0 and risk == 0.0


# ---- broker safety --------------------------------------------------------
def test_paper_broker_round_trips_and_charges_fees():
    b = PaperBroker(1000.0, fee_pct=0.001)
    b.open_position("buy", 0.01, 50_000.0, 49_000.0, 52_000.0)
    assert b.position.is_open
    b.close_position(52_000.0)
    assert not b.position.is_open
    assert len(b.fills) == 2
    assert all(f.fee > 0 for f in b.fills)
    assert b.realised > 0


def test_broker_refuses_orders_above_the_notional_cap():
    b = PaperBroker(100.0, limits=RiskLimits(max_notional_pct=0.5))
    with pytest.raises(BrokerError, match="exceeds cap"):
        b.open_position("buy", 1.0, 100.0, 90.0, 120.0)  # $100 vs $50 cap


def test_broker_halts_after_the_daily_loss_limit():
    b = PaperBroker(100.0, fee_pct=0.0, limits=RiskLimits(max_daily_loss_pct=0.05))
    b.open_position("buy", 0.5, 100.0, 50.0, 200.0)
    b.close_position(85.0)  # -$7.50, over the 5% limit
    assert b.halted()


def test_live_binance_refuses_without_keys(monkeypatch):
    monkeypatch.delenv("BINANCE_API_KEY", raising=False)
    monkeypatch.delenv("BINANCE_API_SECRET", raising=False)
    with pytest.raises(BrokerError, match="not set"):
        BinanceBroker(100.0)


def test_live_binance_refuses_without_the_flag(monkeypatch):
    monkeypatch.setenv("BINANCE_API_KEY", "k")
    monkeypatch.setenv("BINANCE_API_SECRET", "s")
    with pytest.raises(BrokerError, match="allow_live"):
        BinanceBroker(100.0, testnet=False, allow_live=False)


def test_testnet_is_allowed_with_keys(monkeypatch):
    monkeypatch.setenv("BINANCE_API_KEY", "k")
    monkeypatch.setenv("BINANCE_API_SECRET", "s")
    b = BinanceBroker(100.0, testnet=True)
    assert b.testnet is True


# ---- runner / backtest ----------------------------------------------------
def test_runner_closes_on_stop():
    b = PaperBroker(1000.0, fee_pct=0.0, limits=RiskLimits(max_notional_pct=10.0))
    b.open_position("buy", 1.0, 100.0, 95.0, 120.0)
    r = Runner(b, RunnerConfig())
    r._manage(94.0, 1.0)
    assert not b.position.is_open


def test_runner_closes_on_target():
    b = PaperBroker(1000.0, fee_pct=0.0, limits=RiskLimits(max_notional_pct=10.0))
    b.open_position("buy", 1.0, 100.0, 95.0, 120.0)
    r = Runner(b, RunnerConfig())
    r._manage(121.0, 1.0)
    assert not b.position.is_open


def test_stop_is_checked_before_target():
    """A bar that touches both must count as a stop, not a win."""
    b = PaperBroker(1000.0, fee_pct=0.0, limits=RiskLimits(max_notional_pct=10.0))
    b.open_position("buy", 1.0, 100.0, 95.0, 120.0)
    r = Runner(b, RunnerConfig())
    r._manage(95.0, 1.0)
    assert r.closed[-1]["reason"] == "stop"


def test_backtest_actually_trades():
    res = backtest(synthetic_candles(n=900, seed=1), start_equity=100.0)
    assert res["steps"] > 800
    assert res["trades"] > 5


def test_backtest_on_a_random_walk_does_not_print_money():
    """No edge in the data means fees should dominate. Anything else is a bug."""
    res = backtest(synthetic_candles(n=900, seed=1), start_equity=100.0)
    assert -25.0 < res["return_pct"] < 25.0


def test_backtest_is_deterministic():
    a = backtest(synthetic_candles(n=400, seed=9), 100.0)
    b = backtest(synthetic_candles(n=400, seed=9), 100.0)
    assert a["final"] == b["final"]


# ---- data -----------------------------------------------------------------
def test_candle_parses_binance_row():
    row = [1700000000000, "100.0", "101.0", "99.0", "100.5", "12.5",
           1700000299999, "0", 1, "0", "0", "0"]
    c = candle_from_binance(row)
    assert c.ts == pytest.approx(1700000000.0)
    assert c.close == 100.5
    assert c.bullish


def test_synthetic_candles_have_valid_ohlc():
    for c in synthetic_candles(n=100, seed=2):
        assert c.low <= min(c.open, c.close) <= max(c.open, c.close) <= c.high
        assert c.volume > 0


# ---- render ---------------------------------------------------------------
def test_svg_renders_without_a_reader():
    svg = render_svg(synthetic_candles(n=50, seed=1))
    assert svg.startswith("<svg") and svg.endswith("</svg>")


def test_svg_includes_the_narration_and_decision():
    c = synthetic_candles(n=120, seed=3)
    read = read_chart(c)
    sig = evaluate(read, 100.0)
    svg = render_svg(c, read, sig, title="BTCUSDT 5m")
    assert "BTCUSDT" in svg
    assert "trend" in svg


def test_svg_escapes_markup():
    svg = render_svg(synthetic_candles(n=40, seed=1), title="<script>")
    assert "&lt;script&gt;" in svg
