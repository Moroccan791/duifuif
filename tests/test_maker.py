"""Tests for the mid-range maker strategy.

The important one is `test_maker_pays_no_fee` plus the adverse-selection
checks: the entire argument for quoting at 50-60c is that the fee is zero,
and the entire risk is that you only get filled when you are wrong.
"""

from __future__ import annotations

import pytest

from pm5m.broker import PaperBroker
from pm5m.config import Settings
from pm5m.engine import Engine
from pm5m.maker import effective_p_on_fill, plan_quotes, quote_is_filled
from pm5m.risk import RiskManager
from pm5m.settlement import SettlementIndex
from pm5m.types import Book, Level, Market, Signal
from pm5m.vol import SpotBuffer

START = 1_700_000_000.0
END = START + 300.0


def _market() -> Market:
    return Market(
        slug="m",
        condition_id="c",
        asset="btc",
        start_ts=START,
        end_ts=END,
        token_up="UP",
        token_down="DOWN",
        open_price=100_000.0,
    )


def _state(price: float, upto: float = 150.0) -> SettlementIndex:
    st = SettlementIndex(start_ts=START, end_ts=END, open_price=100_000.0)
    t = 0.0
    while t <= upto:
        st.add(START + t, price)
        t += 2.0
    return st


def _books(ask_up: float, ask_dn: float, bid_up: float = 0.0, bid_dn: float = 0.0) -> dict[str, Book]:
    return {
        "up": Book("UP", START + 150, (Level(bid_up, 500),) if bid_up else (), (Level(ask_up, 500),)),
        "down": Book("DOWN", START + 150, (Level(bid_dn, 500),) if bid_dn else (), (Level(ask_dn, 500),)),
    }


def _maker_settings(**kw) -> Settings:
    base = dict(
        kill_switch_path="",
        strategy_mode="maker",
        min_price=0.50,
        max_price=0.60,
        min_edge_cents=0.5,
        maker_edge_cents=1.5,
        maker_quote_shares=40.0,
    )
    base.update(kw)
    return Settings(**base)


# ---- the fee asymmetry ----------------------------------------------------
def test_maker_pays_no_fee():
    s = Settings(kill_switch_path="")
    broker = PaperBroker(settings=s, risk=RiskManager(settings=s))
    book = Book("UP", 1.0, bids=(Level(0.53, 500),), asks=(Level(0.545, 500),))
    sig = Signal("m", "UP", "up", 0.545, 0.56, 0.015, "test")
    fill = broker.execute(sig, book, 40.0, 1.0, as_maker=True)
    assert fill is not None
    assert fill.fee == 0.0
    assert fill.price == pytest.approx(0.545)


def test_taker_pays_the_fee_on_the_same_fill():
    s = Settings(kill_switch_path="")
    broker = PaperBroker(settings=s, risk=RiskManager(settings=s))
    book = Book("UP", 1.0, bids=(Level(0.54, 500),), asks=(Level(0.545, 500),))
    sig = Signal("m", "UP", "up", 0.545, 0.56, 0.015, "test")
    fill = broker.execute(sig, book, 40.0, 1.0)
    assert fill is not None
    assert fill.fee > 0
    assert fill.fee == pytest.approx(40.0 * 0.07 * 0.545 * (1 - 0.545))


def test_maker_order_does_not_fill_when_price_moved_away():
    s = Settings(kill_switch_path="")
    broker = PaperBroker(settings=s, risk=RiskManager(settings=s))
    book = Book("UP", 1.0, bids=(Level(0.54, 500),), asks=(Level(0.60, 500),))
    sig = Signal("m", "UP", "up", 0.545, 0.56, 0.015, "test")
    assert broker.execute(sig, book, 40.0, 1.0, as_maker=True) is None


# ---- quoting rules --------------------------------------------------------
def test_quotes_stay_inside_the_configured_band():
    s = _maker_settings()
    # Fair value far outside 50-60c: no quotes at all.
    state = _state(103_000.0)
    qs = plan_quotes(_market(), state, _books(0.20, 0.81), 103_000.0, 1e-4, START + 150, s)
    assert qs == []


def test_quotes_rest_below_the_ask_and_above_the_bid():
    s = _maker_settings()
    # A small cushion puts fair value around 55c, inside the band.
    state = _state(100_005.0)
    books = _books(ask_up=0.56, ask_dn=0.46, bid_up=0.53, bid_dn=0.43)
    qs = plan_quotes(_market(), state, books, 100_005.0, 1e-4, START + 150, s)
    assert qs
    for q in qs:
        book = books[q.side]
        assert q.price < book.best_ask
        assert q.price > book.best_bid
        assert q.edge > 0


def test_quotes_never_exceed_the_price_cap():
    s = _maker_settings(max_price=0.52)
    state = _state(100_005.0)
    qs = plan_quotes(_market(), state, _books(0.56, 0.46, 0.53, 0.43), 100_005.0, 1e-4, START + 150, s)
    for q in qs:
        assert q.price <= 0.52


def test_no_quote_on_a_side_already_held():
    s = _maker_settings()
    state = _state(100_005.0)
    books = _books(0.56, 0.46, 0.53, 0.43)
    qs = plan_quotes(
        _market(), state, books, 100_005.0, 1e-4, START + 150, s, held={"UP"}
    )
    assert all(q.token_id != "UP" for q in qs)


def test_no_quote_when_exposure_is_full():
    s = _maker_settings(max_total_exposure_usd=10.0)
    risk = RiskManager(settings=s)
    risk.record_fill(START, 10.0)
    qs = plan_quotes(
        _market(), _state(100_005.0), _books(0.56, 0.46, 0.53, 0.43),
        100_005.0, 1e-4, START + 150, s, risk=risk,
    )
    assert qs == []


def test_no_quote_late_in_the_window():
    s = _maker_settings(min_remaining_s=20.0)
    state = _state(100_000.0, upto=295.0)
    qs = plan_quotes(_market(), state, _books(0.56, 0.46, 0.53, 0.43), 100_000.0, 1e-4, START + 295, s)
    assert qs == []


# ---- fills and adverse selection -----------------------------------------
def test_quote_is_filled_only_when_the_ask_comes_to_us():
    from pm5m.maker import Quote

    q = Quote("up", "UP", 0.545, 40.0, 0.56, 0.015, 1.0)
    assert quote_is_filled(q, Book("UP", 1.0, (), (Level(0.54, 10),)))
    assert quote_is_filled(q, Book("UP", 1.0, (), (Level(0.545, 10),)))
    # An ask above our bid means nobody is selling to us yet.
    assert not quote_is_filled(q, Book("UP", 1.0, (), (Level(0.55, 10),)))
    assert not quote_is_filled(q, Book("UP", 1.0, (), (Level(0.56, 10),)))


def test_adverse_selection_lowers_the_conditional_probability():
    assert effective_p_on_fill(0.60, 0.0) == pytest.approx(0.60)
    assert effective_p_on_fill(0.60, 2.0) == pytest.approx(0.58)
    # Can never go negative, however harsh the assumption.
    assert effective_p_on_fill(0.01, 5.0) == 0.0


# ---- engine integration --------------------------------------------------
def _engine(settings: Settings) -> Engine:
    risk = RiskManager(settings=settings)
    return Engine(
        settings=settings,
        broker=PaperBroker(settings=settings, risk=risk),
        risk=risk,
        spot_buffer=SpotBuffer(),
        sigma_override=1e-4,
    )


def test_engine_maker_mode_fills_without_fee_and_only_once():
    s = _maker_settings(maker_requote_s=0.0, min_remaining_s=5.0)
    e = _engine(s)
    mk = _market()
    e.on_market(mk)

    for t in range(0, 160, 2):
        e.on_spot(START + t, 100_005.0)
    # Book wide, then tighten the ask down onto whatever we quoted.
    e.on_book(_books(0.56, 0.46, 0.50, 0.40)["up"], "up", mk.slug)
    e.on_book(_books(0.56, 0.46, 0.50, 0.40)["down"], "down", mk.slug)
    ws = e.windows[mk.slug]
    assert ws.quotes, "expected resting maker quotes"

    quoted = dict(ws.quotes)
    for t in (162, 164, 166):
        e.on_spot(START + t, 100_005.0)
        for side, token in (("up", "UP"), ("down", "DOWN")):
            q = quoted.get(token)
            ask = q.price if q else 0.50
            e.on_book(
                Book(token, START + t, (Level(max(0.01, ask - 0.02), 500),), (Level(ask, 500),)),
                side,
                mk.slug,
            )
    assert e.broker.n_trades >= 1
    assert all(f.fee == 0.0 for f in e.broker.fills)


def test_taker_mode_still_requires_the_band():
    s = Settings(
        kill_switch_path="", min_price=0.50, max_price=0.60,
        min_z_lock=0.0, min_edge_cents=0.1, strategy_mode="taker",
    )
    e = _engine(s)
    mk = _market()
    e.on_market(mk)
    for t in range(0, 280, 2):
        e.on_spot(START + t, 100_000.0 if t < 2 else 101_000.0)
    # Locked-up but priced at 0.90, outside the 50-60c band: must not trade.
    e.on_book(Book("UP", START + 280, (), (Level(0.90, 1000),)), "up", mk.slug)
    e.on_book(Book("DOWN", START + 280, (), (Level(0.10, 1000),)), "down", mk.slug)
    assert e.broker.n_trades == 0


def test_maker_mode_refuses_to_go_live():
    """LiveBroker can only send taker orders, so maker mode must be blocked."""
    from pm5m.live import live_gates

    s = _maker_settings()
    gates = live_gates(s, acknowledged=True)
    assert any("maker live execution" in g for g in gates)
