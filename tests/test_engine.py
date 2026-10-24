"""End-to-end engine tests, including regressions for two bugs that produced
fake profits in an earlier version of the simulator.
"""

from __future__ import annotations

import pytest

from pm5m.broker import PaperBroker
from pm5m.config import Settings
from pm5m.engine import Engine
from pm5m.risk import RiskManager
from pm5m.types import Book, Level, Market
from pm5m.vol import SpotBuffer

START = 1_700_000_000.0
END = START + 300.0
OPEN = 100_000.0


def _engine(**overrides) -> Engine:
    s = Settings(kill_switch_path="", min_z_lock=1.0, min_edge_cents=0.1, **overrides)
    risk = RiskManager(settings=s)
    return Engine(
        settings=s,
        broker=PaperBroker(settings=s, risk=risk),
        risk=risk,
        spot_buffer=SpotBuffer(),
        sigma_override=1e-4,
    )


def _market() -> Market:
    return Market(
        slug=f"btc-updown-5m-{int(START)}",
        condition_id="c",
        asset="btc",
        start_ts=START,
        end_ts=END,
        token_up="UP",
        token_down="DOWN",
        open_price=OPEN,
    )


def _book(token: str, ask: float, ts: float, size: float = 1000.0) -> Book:
    return Book(
        token_id=token,
        ts=ts,
        bids=(Level(max(0.01, ask - 0.02), size),),
        asks=(Level(ask, size),),
    )


def _feed(engine: Engine, mk: Market, price_fn, from_s: float = 0.0, to_s: float = 290.0, step: float = 2.0):
    t = from_s
    while t <= to_s:
        engine.on_spot(START + t, price_fn(t))
        t += step


def test_engine_does_not_trade_a_coin_flip():
    e = _engine()
    mk = _market()
    e.on_market(mk)
    _feed(e, mk, lambda t: OPEN)
    for t in (100.0, 200.0, 280.0):
        e.on_book(_book("UP", 0.51, START + t), "up", mk.slug)
        e.on_book(_book("DOWN", 0.51, START + t), "down", mk.slug)
    assert e.broker.n_trades == 0


def test_engine_buys_the_locked_side_when_book_lags():
    e = _engine()
    mk = _market()
    e.on_market(mk)
    # Price jumps up and stays: the running average is far above the open.
    _feed(e, mk, lambda t: OPEN if t < 2 else OPEN * 1.008)
    e.on_book(_book("UP", 0.90, START + 280.0), "up", mk.slug)
    e.on_book(_book("DOWN", 0.10, START + 280.0), "down", mk.slug)
    assert e.broker.n_trades == 1
    fill = e.broker.fills[0]
    assert fill.side == "up"
    assert fill.token_id == "UP"


def test_regression_down_signal_uses_the_down_book():
    """A down-side entry must price against the DOWN book, not the UP one."""
    e = _engine()
    mk = _market()
    e.on_market(mk)
    _feed(e, mk, lambda t: OPEN if t < 2 else OPEN * 0.992)
    e.on_book(_book("UP", 0.12, START + 280.0), "up", mk.slug)
    e.on_book(_book("DOWN", 0.88, START + 280.0), "down", mk.slug)
    assert e.broker.n_trades == 1
    fill = e.broker.fills[0]
    assert fill.side == "down"
    assert fill.token_id == "DOWN"
    assert fill.price == pytest.approx(0.88, abs=0.011)


def test_settlement_books_pnl_and_clears_the_position():
    e = _engine()
    mk = _market()
    e.on_market(mk)
    _feed(e, mk, lambda t: OPEN if t < 2 else OPEN * 1.008)
    e.on_book(_book("UP", 0.90, START + 280.0), "up", mk.slug)
    e.on_book(_book("DOWN", 0.10, START + 280.0), "down", mk.slug)
    assert e.broker.positions
    e.tick(END + 1.0)
    assert not e.broker.positions
    assert e.broker.realized_pnl > 0
    assert mk.slug not in e.windows


def test_no_settlement_without_an_observed_path():
    e = _engine()
    mk = _market()
    e.on_market(mk)
    e.tick(END + 1.0)
    assert mk.slug not in e.windows
    assert e.broker.realized_pnl == 0.0


def test_stale_book_is_ignored():
    e = _engine()
    mk = _market()
    e.on_market(mk)
    _feed(e, mk, lambda t: OPEN if t < 2 else OPEN * 1.008)
    # Book timestamped 60s in the past relative to the decision time.
    e.on_book(_book("UP", 0.90, START + 200.0), "up", mk.slug)
    e.on_book(_book("DOWN", 0.10, START + 200.0), "down", mk.slug)
    # The engine evaluates at the book's own timestamp, which is consistent,
    # but only once. Re-evaluating later with the same stale book must not fire.
    n = e.broker.n_trades
    e.on_book(_book("UP", 0.90, START + 200.0), "up", mk.slug)
    assert e.broker.n_trades == n


def test_engine_never_trades_twice_in_a_window():
    e = _engine()
    mk = _market()
    e.on_market(mk)
    _feed(e, mk, lambda t: OPEN if t < 2 else OPEN * 1.008)
    for t in (240.0, 260.0, 280.0):
        e.on_book(_book("UP", 0.80, START + t), "up", mk.slug)
        e.on_book(_book("DOWN", 0.20, START + t), "down", mk.slug)
    assert e.broker.n_trades == 1


def test_kill_switch_stops_a_live_engine(tmp_path):
    stop = tmp_path / "STOP"
    stop.write_text("halt")
    s = Settings(kill_switch_path=str(stop), min_z_lock=1.0, min_edge_cents=0.1)
    risk = RiskManager(settings=s)
    e = Engine(
        settings=s,
        broker=PaperBroker(settings=s, risk=risk),
        risk=risk,
        spot_buffer=SpotBuffer(),
        sigma_override=1e-4,
    )
    mk = _market()
    e.on_market(mk)
    _feed(e, mk, lambda t: OPEN if t < 2 else OPEN * 1.008)
    e.on_book(_book("UP", 0.80, START + 280.0), "up", mk.slug)
    e.on_book(_book("DOWN", 0.20, START + 280.0), "down", mk.slug)
    assert e.broker.n_trades == 0
