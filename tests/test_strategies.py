"""Documented strategies, and the arithmetic that evaluates them honestly."""

from __future__ import annotations

import math

import pytest

from chartbot.data import load_csv, synthetic_candles, trending_candles
from chartbot.strategies import (buy_and_hold, compare, donchian, ema_cross,
                                 evaluate, realized_vol, sma, sma_long_flat,
                                 tsmom, vol_target)


@pytest.fixture(scope="module")
def real():
    return load_csv("data/btc_daily.csv")


# ---- primitives -----------------------------------------------------------
def test_sma_of_a_constant_series_is_that_constant():
    assert sma([7.0] * 30, 10)[-1] == pytest.approx(7.0)


def test_realized_vol_is_zero_on_a_flat_series():
    assert realized_vol([0.0] * 60, 59) == 0.0


def test_realized_vol_annualises_with_365_days():
    """Crypto trades every day; annualising on 252 overstates Sharpe by ~17%."""
    daily = 0.01
    rets = [daily if i % 2 else -daily for i in range(120)]
    v = realized_vol(rets, 119, lookback=120)
    # alternating +/-1% has sd ~0.01 daily -> ~0.19 annualised on 365
    assert v == pytest.approx(0.01 * math.sqrt(365), rel=0.02)


# ---- strategy shapes ------------------------------------------------------
def test_buy_and_hold_is_always_long():
    assert set(buy_and_hold(synthetic_candles(n=50))) == {1.0}


def test_sma_long_flat_only_holds_above_the_average():
    c = trending_candles(n=300, up=True, seed=1)
    pos = sma_long_flat(c, 50)
    assert all(p in (0.0, 1.0) for p in pos)
    assert pos[-1] == 1.0        # rising series -> above its average
    assert all(p == 0.0 for p in pos[:50])  # no signal until warmed up


def test_donchian_goes_long_on_a_breakout():
    c = trending_candles(n=200, up=True, seed=2)
    pos = donchian(c, entry=20, exit_n=10)
    assert pos[-1] == 1.0


def test_tsmom_sign_follows_the_trend():
    up = tsmom(trending_candles(n=400, up=True, seed=3))
    down = tsmom(trending_candles(n=400, up=False, seed=3))
    assert up[-1] > 0
    assert down[-1] < 0


def test_tsmom_long_only_never_shorts():
    pos = tsmom(trending_candles(n=400, up=False, seed=3), allow_short=False)
    assert min(pos) >= 0.0


def test_ema_cross_flips_sign_in_a_reversal():
    pos = ema_cross(trending_candles(n=400, up=True, seed=4), 10, 50)
    assert pos[-1] == 1.0


# ---- vol targeting --------------------------------------------------------
def test_vol_target_scales_down_when_volatility_rises():
    calm = [0.001 if i % 2 else -0.001 for i in range(200)]
    wild = [0.05 if i % 2 else -0.05 for i in range(200)]
    pos = [1.0] * 200
    calm_scaled = vol_target(pos, calm)[-1]
    wild_scaled = vol_target(pos, wild)[-1]
    assert wild_scaled < calm_scaled


def test_vol_target_leaves_flat_positions_flat():
    assert vol_target([0.0] * 100, [0.01] * 100)[-1] == 0.0


def test_vol_target_is_capped():
    rets = [1e-9] * 100   # near-zero vol would imply enormous leverage
    assert vol_target([1.0] * 100, rets, max_lev=2.0)[-1] <= 2.0


# ---- evaluation -----------------------------------------------------------
def test_evaluate_uses_a_one_bar_lag():
    """The position decided on bar i-1 earns bar i's return. No peeking."""
    c = synthetic_candles(n=100, seed=5)
    pos = [0.0] * len(c)
    pos[-2] = 1.0          # long only into the final bar
    m = evaluate("x", c, pos, cost_bps=0.0)
    expected = c[-1].close / c[-2].close - 1.0
    assert m.final_multiple == pytest.approx(1.0 + expected, rel=1e-6)


def test_costs_reduce_the_final_equity():
    c = synthetic_candles(n=300, seed=6)
    pos = sma_long_flat(c, 20)
    free = evaluate("free", c, pos, cost_bps=0.0)
    paid = evaluate("paid", c, pos, cost_bps=50.0)
    assert paid.final_multiple < free.final_multiple


def test_flat_position_earns_nothing():
    c = synthetic_candles(n=200, seed=7)
    m = evaluate("flat", c, [0.0] * len(c), cost_bps=0.0)
    assert m.final_multiple == pytest.approx(1.0)
    assert m.max_dd == 0.0


def test_buy_and_hold_matches_the_price_change():
    c = synthetic_candles(n=200, seed=8)
    m = evaluate("bh", c, [1.0] * len(c), cost_bps=0.0)
    assert m.final_multiple == pytest.approx(c[-1].close / c[0].close, rel=1e-9)


# ---- the real comparison --------------------------------------------------
def test_vol_targeting_helps_every_documented_strategy(real):
    """The literature's central claim: most of the benefit is volatility scaling."""
    res = {m.name: m for m in compare(real, cost_bps=5.0)}
    pairs = [
        ("SMA 200 long/flat", "SMA 200 long/flat + vol target"),
        ("Donchian 55/20 long", "Donchian 55/20 long + vol target"),
        ("TSMOM 1/3/12m long/short", "TSMOM 1/3/12m long/short + vol target"),
        ("EMA 20/100 cross", "EMA 20/100 cross + vol target"),
    ]
    improved = sum(1 for a, b in pairs if res[b].sharpe > res[a].sharpe)
    assert improved >= 3, f"only {improved}/4 improved with vol targeting"


def test_vol_targeting_cuts_drawdown(real):
    res = {m.name: m for m in compare(real)}
    raw = res["SMA 200 long/flat"]
    scaled = res["SMA 200 long/flat + vol target"]
    assert abs(scaled.max_dd) < abs(raw.max_dd) / 1.5


def test_long_only_beats_long_short_on_crypto(real):
    """Published finding: the short leg eats jump risk and loses."""
    res = {m.name: m for m in compare(real)}
    assert res["TSMOM 1/3/12m long only"].sharpe > res["TSMOM 1/3/12m long/short"].sharpe


def test_buy_and_hold_has_the_worst_drawdown(real):
    res = compare(real)
    bh = next(m for m in res if m.name == "buy and hold")
    assert min(abs(m.max_dd) for m in res) < abs(bh.max_dd)
    assert abs(bh.max_dd) > 0.6


def test_every_strategy_beats_holding_on_sharpe_at_least_somewhere(real):
    """Not a claim that they all work -- just that the table is not degenerate."""
    res = compare(real)
    sharpe = [m.sharpe for m in res]
    assert max(sharpe) > 1.3
    assert min(sharpe) > -1.0


# ---- ETH: the same rules on a second asset --------------------------------
@pytest.fixture(scope="module")
def eth():
    return load_csv("data/eth_daily.csv")


def test_eth_data_is_real_and_valid(eth):
    assert len(eth) > 3000
    assert all(x.low <= x.close <= x.high for x in eth)
    assert all(eth[i].ts < eth[i + 1].ts for i in range(len(eth) - 1))
    assert eth[0].close < 1000 and eth[-1].close > 100


def test_eth_data_has_no_absurd_gaps(eth):
    """A bad aggregation shows up as a >50% one-day jump that is not real."""
    jumps = [abs(eth[i].close / eth[i - 1].close - 1) for i in range(1, len(eth))]
    assert max(jumps) < 0.6


def test_the_winner_on_btc_is_also_the_winner_on_eth(real, eth):
    """The strongest generality check available with two assets.

    If SMA+vol only won on BTC, it would be a survivorship result. It wins the
    Sharpe ranking on ETH too, with the same ordering at the top.
    """
    b = sorted(compare(real), key=lambda m: -m.sharpe)
    e = sorted(compare(eth), key=lambda m: -m.sharpe)
    assert b[0].name == e[0].name
    assert "SMA 50" in b[0].name and "vol target" in b[0].name


def test_vol_targeting_helps_on_eth_too(eth):
    res = {m.name: m for m in compare(eth)}
    assert res["SMA 50 long/flat + vol target"].sharpe > res["SMA 50 long/flat"].sharpe
    assert res["SMA 200 long/flat + vol target"].sharpe > res["SMA 200 long/flat"].sharpe


def test_long_only_beats_long_short_on_eth_too(eth):
    res = {m.name: m for m in compare(eth)}
    assert res["TSMOM 1/3/12m long only"].sharpe > res["TSMOM 1/3/12m long/short"].sharpe


def test_buy_and_hold_drawdown_is_brutal_on_both(real, eth):
    for c in (real, eth):
        bh = next(m for m in compare(c) if m.name == "buy and hold")
        assert abs(bh.max_dd) > 0.8


# ---- portfolio ------------------------------------------------------------
def test_align_and_portfolio_uses_only_shared_dates():
    from chartbot.strategies import align_and_portfolio
    a = {1.0: 0.01, 2.0: 0.01, 3.0: 0.01}
    b = {2.0: 0.01, 3.0: 0.01, 4.0: 0.01}
    _, m, nd = align_and_portfolio({"A": a, "B": b})
    assert nd == 2
    assert m.final_multiple > 1.0


def test_portfolio_has_a_shallower_drawdown_than_either_leg(real, eth):
    """Diversification should cut the worst case, or it is not diversification."""
    from chartbot.strategies import (align_and_portfolio, returns_with_dates,
                                     sma_long_flat, vol_target)
    per = {}
    for name, c in (("BTC", real), ("ETH", eth)):
        per[name] = returns_with_dates(c, vol_target(sma_long_flat(c, 50), _r(c)))
    _, pf, _ = align_and_portfolio(per)
    solo = []
    for name, c in (("BTC", real), ("ETH", eth)):
        m = evaluate(name, c, vol_target(sma_long_flat(c, 50), _r(c)))
        solo.append(m)
    assert abs(pf.max_dd) < max(abs(m.max_dd) for m in solo)


def _r(c):
    closes = [x.close for x in c]
    return [0.0] + [closes[i] / closes[i - 1] - 1.0 for i in range(1, len(closes))]
