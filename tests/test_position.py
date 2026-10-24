"""The live position runner, and the broker bug it exposed."""

from __future__ import annotations

import pytest

from chartbot.broker import PaperBroker, RiskLimits
from chartbot.data import load_csv, synthetic_candles, trending_candles
from chartbot.position import PositionConfig, PositionRunner, describe
from chartbot.strategies import realized_vol, sma


def _broker(eq=10_000.0):
    # Zero fee AND zero slippage: these tests are about position bookkeeping,
    # not execution cost. Leaving PaperBroker's default 2bp slippage on made
    # exact-arithmetic assertions fail by 0.02%.
    return PaperBroker(eq, fee_pct=0.0, slippage_pct=0.0, limits=RiskLimits(
        max_notional_pct=1.0, max_daily_loss_pct=0.0, max_trades_per_day=500))


# ---- the bug that cost 99.9% of the account ------------------------------
def test_add_to_position_accumulates_instead_of_replacing():
    """open_position REPLACES. Adding 0.1 to 0.5 must leave you with 0.6.

    Using open_position for rebalancing left the runner holding only the last
    increment while having paid for all of them: $1,000 became $1.12 on BTC.
    """
    b = _broker()
    b.add_to_position(0.5, 100.0, ts=1.0)
    b.add_to_position(0.1, 200.0, ts=2.0)
    assert b.position.qty == pytest.approx(0.6)


def test_add_to_position_weights_the_entry_price():
    b = _broker()
    b.add_to_position(1.0, 100.0, ts=1.0)
    b.add_to_position(1.0, 200.0, ts=2.0)
    assert b.position.entry == pytest.approx(150.0)


def test_add_to_position_reduces_and_realises_pnl():
    b = _broker()
    b.add_to_position(1.0, 100.0, ts=1.0)
    b.add_to_position(-0.5, 150.0, ts=2.0)
    assert b.position.qty == pytest.approx(0.5)
    assert b.position.entry == pytest.approx(100.0)  # cost basis unchanged
    assert b.realised == pytest.approx(25.0)         # 0.5 * (150-100)


def test_add_to_position_closes_flat():
    b = _broker()
    b.add_to_position(1.0, 100.0, ts=1.0)
    b.add_to_position(-1.0, 110.0, ts=2.0)
    assert not b.position.is_open
    assert b.realised == pytest.approx(10.0)


def test_cash_tracks_the_asset_bought():
    """The invariant the bug broke: cash + holdings must equal equity."""
    b = _broker(1000.0)
    b.add_to_position(5.0, 100.0, ts=1.0)
    assert b.equity(100.0) == pytest.approx(1000.0)
    assert b.cash == pytest.approx(500.0)


# ---- target position ------------------------------------------------------
def test_target_is_zero_below_the_average():
    c = trending_candles(n=300, up=False, seed=1)
    r = PositionRunner(_broker(), PositionConfig())
    target, ma, _ = r.target_position(c)
    assert target == 0.0
    assert c[-1].close < ma


def test_target_is_positive_above_the_average():
    c = trending_candles(n=300, up=True, seed=1)
    r = PositionRunner(_broker(), PositionConfig())
    target, ma, _ = r.target_position(c)
    assert target > 0.0
    assert c[-1].close > ma


def test_target_shrinks_when_volatility_rises():
    """Same trend, higher vol -> smaller position. This is the whole overlay."""
    calm = synthetic_candles(n=300, seed=2, vol=0.005, drift=0.002)
    wild = synthetic_candles(n=300, seed=2, vol=0.05, drift=0.02)
    rc, rw = PositionRunner(_broker(), PositionConfig()), PositionRunner(_broker(), PositionConfig())
    tc, _, vc = rc.target_position(calm)
    tw, _, vw = rw.target_position(wild)
    assert vw > vc
    assert tw < tc


def test_target_never_exceeds_one_on_spot():
    """No leverage: target is capped at 100% even when vol is tiny."""
    c = [type("C", (), {"ts": i, "open": 100.0, "high": 100.001,
                        "low": 99.999, "close": 100.0 + i * 0.001,
                        "volume": 1.0})() for i in range(300)]
    r = PositionRunner(_broker(), PositionConfig())
    target, _, _ = r.target_position(c)
    assert target <= 1.0


def test_not_enough_history_means_no_position():
    r = PositionRunner(_broker(), PositionConfig())
    target, _, _ = r.target_position(synthetic_candles(n=20))
    assert target == 0.0


# ---- the runner as a whole ------------------------------------------------
def test_runner_buys_when_price_crosses_above():
    c = trending_candles(n=300, up=True, seed=3)
    b = _broker(10_000.0)
    r = PositionRunner(b, PositionConfig(min_trade_usd=1.0))
    for i in range(60, len(c)):
        r.step(c[:i + 1])
    assert b.position.is_open
    assert b.position.qty > 0


def test_runner_closes_when_price_crosses_below():
    c = trending_candles(n=400, up=False, seed=4)
    b = _broker(10_000.0)
    r = PositionRunner(b, PositionConfig(min_trade_usd=1.0))
    b.add_to_position(1.0, 500.0, ts=1.0)   # start long
    for i in range(60, len(c)):
        r.step(c[:i + 1])
    assert not b.position.is_open


def test_rebalance_threshold_prevents_churn():
    """Tiny target changes must not generate trades, or fees eat the edge."""
    c = trending_candles(n=400, up=True, seed=5)
    b = _broker(10_000.0)
    r = PositionRunner(b, PositionConfig(rebalance=0.50, min_trade_usd=1.0))
    for i in range(60, len(c)):
        r.step(c[:i + 1])
    assert len(b.fills) <= 3


def test_halted_broker_stops_the_runner():
    b = PaperBroker(1000.0, fee_pct=0.0,
                    limits=RiskLimits(max_notional_pct=1.0,
                                      max_daily_loss_pct=0.0001,
                                      max_trades_per_day=500))
    b.add_to_position(5.0, 100.0, ts=1.0)
    b.close_position(50.0, 2.0)
    c = trending_candles(n=300, up=True, seed=6)
    r = PositionRunner(b, PositionConfig(min_trade_usd=1.0))
    st = r.step(c)
    assert "halted" in st.note
    assert not st.action


# ---- live runner must agree with the backtest ----------------------------
@pytest.mark.parametrize("path", ["data/btc_daily.csv", "data/eth_daily.csv"])
def test_live_runner_matches_the_backtest(path):
    """The whole point of sharing code: no drift between sim and live.

    This is the test that would have caught the open_position bug -- the
    backtest never calls the broker, so it looked fine while the runner
    destroyed the account.
    """
    c = load_csv(path)
    b = _broker(1000.0)
    r = PositionRunner(b, PositionConfig(min_trade_usd=1.0))
    for i in range(60, len(c)):
        r.step(c[:i + 1])
    if b.position.is_open:
        b.close_position(c[-1].close, c[-1].ts, "end")
    final = b.equity(c[-1].close)
    assert final > 200.0, f"live runner lost the account: ${final:,.2f}"
    years = (len(c) - 60) / 365
    cagr = (final / 1000.0) ** (1 / years) - 1
    assert 0.10 < cagr < 1.20, f"cagr {cagr:.2f} is not in the backtested range"


def test_describe_reports_the_moving_average():
    text = describe(trending_candles(n=300, up=True, seed=7))
    assert "SMA" in text and "ABOVE" in text
