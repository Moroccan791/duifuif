"""Tests for fees, sizing, risk gates, and the paper broker."""

from __future__ import annotations

import os

import pytest

from pm5m.broker import PaperBroker
from pm5m.config import Settings
from pm5m.fees import (
    fee_as_pct_of_stake,
    net_edge_per_share,
    taker_fee,
    taker_fee_per_share,
)
from pm5m.risk import RiskManager
from pm5m.settlement import SettlementIndex
from pm5m.strategy import evaluate, kelly_fraction_stake, size_signal
from pm5m.types import Book, Level, Market, Signal


# ---- fees -----------------------------------------------------------------
def test_fee_formula_known_values():
    # 100 shares at 50c: 100 * 0.07 * 0.25 = 1.75
    assert taker_fee(100, 0.50) == pytest.approx(1.75)
    # at 95c: 100 * 0.07 * 0.95 * 0.05 = 0.3325
    assert taker_fee(100, 0.95) == pytest.approx(0.3325)


def test_fee_is_symmetric_and_peaks_at_half():
    assert taker_fee_per_share(0.40) == pytest.approx(taker_fee_per_share(0.60))
    assert taker_fee_per_share(0.50) > taker_fee_per_share(0.30)
    assert fee_as_pct_of_stake(0.50) == pytest.approx(0.035)


def test_fee_as_pct_of_stake_falls_towards_the_extremes():
    assert fee_as_pct_of_stake(0.95) < fee_as_pct_of_stake(0.50)


def test_net_edge_subtracts_the_fee():
    # 60% true at 50c: gross edge 10c, fee 1.75c
    assert net_edge_per_share(0.60, 0.50) == pytest.approx(0.10 - 0.0175)


def test_fee_is_zero_for_zero_size():
    assert taker_fee(0, 0.5) == 0.0


# ---- kelly ----------------------------------------------------------------
def test_kelly_zero_at_fair_price():
    assert kelly_fraction_stake(0.5, 0.5) == pytest.approx(0.0)


def test_kelly_formula():
    # p=0.9, c=0.8 -> (0.9-0.8)/(1-0.8) = 0.5
    assert kelly_fraction_stake(0.90, 0.80) == pytest.approx(0.5)


def test_kelly_never_negative_for_bad_bets():
    assert kelly_fraction_stake(0.30, 0.50) == 0.0


# ---- risk -----------------------------------------------------------------
def _signal(price: float = 0.60, edge: float = 0.05) -> Signal:
    return Signal(
        market_slug="m",
        token_id="UP",
        side="up",
        limit_price=price,
        p_true=price + edge,
        edge_per_share=edge,
        reason="test",
    )


def test_kill_switch_blocks_everything(tmp_path):
    stop = tmp_path / "STOP"
    stop.write_text("stop")
    s = Settings(kill_switch_path=str(stop))
    r = RiskManager(settings=s)
    decision = r.check(_signal(), 1000.0)
    assert not decision.allowed
    assert "kill switch" in decision.reason


def test_price_band_rejects_expensive_contracts():
    s = Settings(kill_switch_path="", max_price=0.95)
    r = RiskManager(settings=s)
    assert r.check(_signal(price=0.97), 1000.0).allowed is False


def test_hourly_trade_cap():
    s = Settings(kill_switch_path="", max_trades_per_hour=2, max_price=0.99)
    r = RiskManager(settings=s)
    now = 10_000.0
    r.record_fill(now, 10.0)
    r.record_fill(now + 1, 10.0)
    assert r.check(_signal(), now + 2).allowed is False


def test_cooldown_after_loss():
    s = Settings(kill_switch_path="", cooldown_after_loss_s=60, max_price=0.99)
    r = RiskManager(settings=s)
    r.record_close(1000.0, -5.0, 5.0)
    assert not r.check(_signal(), 1030.0).allowed
    assert r.check(_signal(), 1070.0).allowed


def test_daily_loss_limit_halts_for_the_day():
    s = Settings(kill_switch_path="", max_daily_loss_usd=10.0, cooldown_after_loss_s=0, max_price=0.99)
    r = RiskManager(settings=s)
    r.roll_day(1000.0)
    r.record_close(1000.0, -11.0, 11.0)
    d = r.check(_signal(), 1100.0)
    assert not d.allowed
    assert "daily loss" in d.reason


def test_exposure_cap():
    s = Settings(kill_switch_path="", max_total_exposure_usd=20.0, max_price=0.99)
    r = RiskManager(settings=s)
    r.record_fill(1.0, 20.0)
    assert not r.check(_signal(), 2.0).allowed


# ---- strategy -------------------------------------------------------------
def _market() -> Market:
    return Market(
        slug="btc-updown-5m-1000",
        condition_id="c",
        asset="btc",
        start_ts=1000.0,
        end_ts=1300.0,
        token_up="UP",
        token_down="DOWN",
    )


def _state(spot_series):
    st = SettlementIndex(start_ts=1000.0, end_ts=1300.0, open_price=spot_series[0][1])
    for ts, p in spot_series:
        st.add(ts, p)
    return st


def _book(ask: float, bid: float | None = None, size: float = 1000.0) -> Book:
    return Book(
        token_id="UP",
        ts=1280.0,
        bids=(Level(bid if bid is not None else ask - 0.02, size),),
        asks=(Level(ask, size),),
    )


def _books(ask: float, bid: float | None = None, size: float = 1000.0):
    """Both sides quoting the same price, for tests that only care about one."""
    return {"up": _book(ask, bid, size), "down": _book(ask, bid, size)}


def test_no_signal_on_a_coin_flip():
    """Perfectly priced book, early in the window -> refuse to trade."""
    s = Settings(kill_switch_path="")
    series = [(1000.0 + i, 100_000.0) for i in range(0, 60)]
    state = _state(series)
    sigs = evaluate(_market(), state, _books(0.51, 0.49), 100_000.0, 1e-4, 1059.0, s)
    assert sigs == []


def test_no_signal_when_too_little_time_remains():
    s = Settings(kill_switch_path="", min_remaining_s=20.0)
    series = [(1000.0 + i, 100_000.0) for i in range(0, 295)]
    state = _state(series)
    sigs = evaluate(_market(), state, _books(0.99, 0.97), 100_000.0, 1e-4, 1295.0, s)
    assert sigs == []


def test_flat_path_at_the_open_is_a_coin_flip_even_at_the_last_second():
    """Nothing decided means nothing to trade, however little time is left.

    A path that sits exactly at the open price has a running average of exactly
    the open price, so the remaining integral only has to beat ~zero. sd shrinks
    with tau, but the mean sits on the threshold, so p stays at 0.5. Bots that
    assume "no time left => decided" lose money here.
    """
    s = Settings(kill_switch_path="", min_z_lock=1.0, min_edge_cents=0.1)
    series = [(1000.0 + i, 100_000.0) for i in range(0, 290)]
    state = _state(series)
    view = evaluate(_market(), state, _books(0.55, 0.53), 100_000.0, 1e-4, 1289.0, s)
    assert view == []


def test_signal_fires_when_the_book_lags_a_locked_outcome():
    """Late in a window with a real cushion, the outcome is near-decided."""
    s = Settings(kill_switch_path="", min_z_lock=1.5, min_edge_cents=0.5)
    series = [(1000.0, 100_000.0)] + [(1002.0 + i, 100_800.0) for i in range(0, 278)]
    state = _state(series)
    sigs = evaluate(_market(), state, _books(0.90, 0.88), 100_800.0, 1e-4, 1279.0, s)
    assert len(sigs) == 1
    assert sigs[0].side == "up"
    assert sigs[0].p_true > 0.95


def test_price_cap_blocks_paying_up_for_certainty():
    s = Settings(kill_switch_path="", min_z_lock=1.0, max_price=0.90, min_edge_cents=0.1)
    series = [(1000.0 + i, 100_800.0) for i in range(0, 280)]
    state = _state(series)
    sigs = evaluate(_market(), state, _books(0.95, 0.93), 100_800.0, 1e-4, 1279.0, s)
    assert sigs == []


def test_sizing_respects_book_liquidity():
    s = Settings(kill_switch_path="", max_book_fraction=0.5, max_position_usd=10_000)
    sig = _signal(price=0.60, edge=0.20)
    sig.meta["cost_per_share"] = 0.61
    book = _book(0.60, 0.58, size=10.0)  # only 10 shares displayed
    shares, why = size_signal(sig, book, s, bankroll_usd=1_000.0, exposure_usd=0.0)
    assert shares <= 5.0 + 1e-9
    assert "kelly" in why


def test_sizing_respects_position_cap():
    s = Settings(kill_switch_path="", max_position_usd=5.0, max_book_fraction=1.0)
    sig = _signal(price=0.90, edge=0.09)
    sig.meta["cost_per_share"] = 0.91
    book = _book(0.90, 0.88, size=1_000.0)
    shares, _ = size_signal(sig, book, s, bankroll_usd=100_000.0, exposure_usd=0.0)
    assert shares * 0.90 <= 5.0 + 0.02


# ---- broker ---------------------------------------------------------------
def test_paper_fill_charges_fees_and_walks_the_book():
    s = Settings(kill_switch_path="", max_book_fraction=1.0)
    risk = RiskManager(settings=s)
    broker = PaperBroker(settings=s, risk=risk)
    book = Book(
        token_id="UP",
        ts=1.0,
        bids=(),
        asks=(Level(0.60, 10.0), Level(0.62, 10.0)),
    )
    fill = broker.execute(_signal(price=0.62, edge=0.1), book, 15.0, 1.0)
    assert fill is not None
    assert fill.shares == pytest.approx(15.0)
    assert fill.price > 0.60  # had to pay up for the second level
    assert fill.fee == pytest.approx(15.0 * 0.07 * fill.price * (1 - fill.price))


def test_half_book_fraction_limits_take_size():
    s = Settings(kill_switch_path="", max_book_fraction=0.5)
    broker = PaperBroker(settings=s, risk=RiskManager(settings=s))
    book = Book(token_id="UP", ts=1.0, bids=(), asks=(Level(0.60, 20.0),))
    fill = broker.execute(_signal(price=0.60, edge=0.1), book, 20.0, 1.0)
    assert fill is not None
    assert fill.shares == pytest.approx(10.0)


def test_paper_does_not_fill_beyond_the_book():
    s = Settings(kill_switch_path="", max_book_fraction=1.0)
    broker = PaperBroker(settings=s, risk=RiskManager(settings=s))
    book = Book(token_id="UP", ts=1.0, bids=(), asks=(Level(0.60, 5.0),))
    fill = broker.execute(_signal(price=0.60, edge=0.1), book, 100.0, 1.0)
    assert fill is not None
    assert fill.shares == pytest.approx(5.0)


def test_settlement_pays_winners_and_zeroes_losers():
    s = Settings(kill_switch_path="")
    risk = RiskManager(settings=s)
    broker = PaperBroker(settings=s, risk=risk)
    m = _market()
    book = _book(0.60, 0.58)
    broker.execute(_signal(price=0.60, edge=0.1), book, 10.0, 1.0)
    pnl = broker.settle_market(m, "up", 1300.0)
    assert pnl > 0

    broker2 = PaperBroker(settings=s, risk=RiskManager(settings=s))
    broker2.execute(_signal(price=0.60, edge=0.1), book, 10.0, 1.0)
    pnl2 = broker2.settle_market(m, "down", 1300.0)
    assert pnl2 < 0
    assert pnl2 == pytest.approx(-(10.0 * 0.60 + taker_fee(10.0, 0.60)))
