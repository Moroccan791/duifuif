"""Tests for the expectancy statistics.

These check the machinery that decides whether to size up, so they matter more
than most. The failure mode being guarded against is a statistic that flatters a
small sample -- every bug here would be a bug that tells someone to bet bigger.
"""

from __future__ import annotations

import math
import random

import pytest

from pm5m.expectancy import (
    TradeRecord,
    analyze,
    conservative_kelly,
    required_sample_size,
    t_cdf,
    t_ppf,
    _wilson,
)


def _trades(n: int, win_rate: float, price: float = 0.55, shares: float = 40.0, seed: int = 5):
    rng = random.Random(seed)
    out = []
    cost = price * shares
    for i in range(n):
        won = rng.random() < win_rate
        pnl = (shares if won else 0.0) - cost
        out.append(
            TradeRecord(
                ts=float(i), slug=f"m{i}", side="up", price=price,
                shares=shares, fee=0.0, won=won, pnl=pnl,
            )
        )
    return out


# ---- the t distribution ---------------------------------------------------
def test_t_cdf_symmetry_and_centre():
    assert t_cdf(0.0, 10) == pytest.approx(0.5)
    assert t_cdf(2.0, 10) + t_cdf(-2.0, 10) == pytest.approx(1.0)


def test_t_cdf_matches_known_critical_values():
    # Two-sided 5% critical value for df=10 is 2.228.
    assert t_cdf(2.228, 10) == pytest.approx(0.975, abs=1e-4)
    # df=30 -> 2.042
    assert t_cdf(2.042, 30) == pytest.approx(0.975, abs=1e-4)


def test_t_ppf_inverts_t_cdf():
    for p in (0.05, 0.5, 0.9, 0.975):
        t = t_ppf(p, 12)
        assert t_cdf(t, 12) == pytest.approx(p, abs=1e-4)


def test_heavier_tails_than_normal():
    # The t distribution puts MORE mass in the tails than the normal (0.0228).
    assert t_cdf(-2.0, 5) > 0.0228
    assert t_cdf(-2.0, 5) > t_cdf(-2.0, 200)


# ---- wilson interval ------------------------------------------------------
def test_wilson_interval_contains_the_point_estimate():
    lo, hi = _wilson(15, 25)
    assert lo < 0.60 < hi


def test_wilson_is_wide_at_small_n():
    lo, hi = _wilson(12, 20)  # 60% on 20 trades
    assert hi - lo > 0.35
    assert lo < 0.50, "60% on 20 trades must not exclude 50%"


def test_wilson_narrows_with_evidence():
    small = _wilson(12, 20)
    big = _wilson(1200, 2000)
    assert (big[1] - big[0]) < (small[1] - small[0]) / 5


# ---- required sample size -------------------------------------------------
def test_required_sample_size_scales_inversely_with_edge():
    small_edge = required_sample_size(0.5, 20.0)
    big_edge = required_sample_size(2.0, 20.0)
    assert big_edge < small_edge


def test_required_sample_size_is_unbounded_without_an_edge():
    assert required_sample_size(0.0, 20.0) >= 10**8
    assert required_sample_size(-1.0, 20.0) >= 10**8


# ---- analysis -------------------------------------------------------------
def test_empty_analysis_is_honest():
    exp = analyze([])
    assert exp.n == 0
    assert exp.p_value == 1.0
    assert "no completed trades" in exp.notes


def test_small_sample_is_never_called_significant():
    exp = analyze(_trades(20, 0.75))
    assert exp.n == 20
    assert exp.significant is False
    assert "INCONCLUSIVE" in exp.verdict()


def test_a_genuine_edge_needs_thousands_of_trades():
    """A real 58% edge at 55c is invisible for a very long time.

    The population expectation is +$1.20/trade with sd ~$19.7, so detecting it
    at 80% power needs on the order of 1,700 trades. This test pins that down:
    at 400 trades the observed result is routinely not significant.
    """
    exp = analyze(_trades(400, 0.58, seed=11))
    assert exp.n == 400
    assert exp.required_n > 1_000
    assert exp.significant is False


def test_a_big_edge_on_enough_trades_is_significant():
    # 70% win rate at 55c, 600 trades: unmistakable.
    exp = analyze(_trades(600, 0.70, seed=2))
    assert exp.significant is True
    assert exp.mean_pnl > 0
    assert exp.ci_low > 0
    assert "EDGE SUPPORTED" in exp.verdict()


def test_negative_expectancy_is_called_out():
    exp = analyze(_trades(300, 0.45, seed=9))
    assert exp.mean_pnl < 0
    assert exp.significant is False
    assert "NEGATIVE EXPECTANCY" in exp.verdict()


def test_break_even_win_rate_equals_the_all_in_cost():
    exp = analyze(_trades(100, 0.60))
    assert exp.breakeven_win_rate == pytest.approx(0.55, abs=0.01)


def test_multiple_testing_correction_raises_the_p_value():
    base = analyze(_trades(200, 0.62, seed=4))
    corrected = analyze(_trades(200, 0.62, seed=4), strategies_tried=10)
    assert corrected.p_value >= base.p_value
    assert any("Bonferroni" in n for n in corrected.notes)


def test_bootstrap_ci_brackets_the_point_estimate():
    exp = analyze(_trades(300, 0.60, seed=6), n_boot=500)
    assert exp.boot_ci[0] <= exp.mean_pnl <= exp.boot_ci[1]


def test_profit_factor_and_averages_are_consistent():
    exp = analyze(_trades(200, 0.60, seed=7))
    assert exp.avg_win > 0
    assert exp.avg_loss < 0
    assert exp.profit_factor > 0
    # Mean pnl must equal the weighted mix of wins and losses.
    implied = exp.win_rate * exp.avg_win + (1 - exp.win_rate) * exp.avg_loss
    assert implied == pytest.approx(exp.mean_pnl, abs=1e-6)


def test_regime_shift_is_flagged():
    good = _trades(200, 0.85, seed=1)
    bad = _trades(200, 0.40, seed=2)
    for i, t in enumerate(bad):
        bad[i] = TradeRecord(t.ts + 1000, t.slug, t.side, t.price, t.shares, t.fee, t.won, t.pnl)
    exp = analyze(good + bad)
    assert any("edge changed between halves" in n for n in exp.notes)


# ---- sizing ---------------------------------------------------------------
def test_conservative_kelly_refuses_to_size_on_weak_evidence():
    """If the lower CI bound is below break-even, the correct size is zero."""
    exp = analyze(_trades(30, 0.60))
    assert conservative_kelly(exp) == 0.0


def test_conservative_kelly_sizes_when_evidence_is_strong():
    exp = analyze(_trades(2000, 0.65, seed=3))
    f = conservative_kelly(exp)
    assert f > 0
    assert f < 0.25  # fractional Kelly, never full


def test_conservative_kelly_is_smaller_than_point_kelly():
    exp = analyze(_trades(2000, 0.65, seed=3))
    c = exp.breakeven_win_rate
    point_kelly = (exp.win_rate - c) / (1 - c)
    assert conservative_kelly(exp) < point_kelly


# ---- persistence round trip ----------------------------------------------
def test_trades_survive_a_store_round_trip(tmp_path):
    from pm5m.broker import PaperBroker
    from pm5m.config import Settings
    from pm5m.risk import RiskManager
    from pm5m.storage import Store
    from pm5m.types import Book, Level, Market, Signal

    s = Settings(kill_switch_path="")
    store = Store(str(tmp_path / "t.sqlite3"))
    broker = PaperBroker(settings=s, risk=RiskManager(settings=s))
    mk = Market("slug1", "c", "btc", 0.0, 300.0, "UP", "DOWN")
    book = Book("UP", 0.0, (Level(0.53, 500),), (Level(0.55, 500),))
    sig = Signal("slug1", "UP", "up", 0.55, 0.62, 0.07, "t")
    broker.execute(sig, book, 100.0, 0.0)
    broker.settle_market(mk, "down", 300.0)

    assert len(broker.closed) == 1
    store.add_trade(broker.closed[0])
    records = store.trade_records()
    store.close()

    assert len(records) == 1
    r = records[0]
    assert r.slug == "slug1"
    assert r.won is False
    assert r.pnl < 0
    assert r.fee > 0


# ---- the significance criterion must not contradict the reported interval ---
def test_significance_matches_the_reported_lower_bound():
    """If the tool says 'significant', the 95% lower bound must clear zero.

    A one-sided test reported next to a two-sided interval produced a page that
    said EDGE SUPPORTED while the visible interval contained zero. That is the
    kind of contradiction that makes a risk tool untrustworthy.
    """
    for n, wr, seed in ((240, 0.60, 11), (600, 0.70, 2), (400, 0.58, 11), (300, 0.45, 9)):
        exp = analyze(_trades(n, wr, seed=seed))
        assert exp.significant == (exp.p_value < 0.05 and exp.ci_low_1s > 0)
        if exp.significant:
            assert exp.ci_low_1s > 0
        else:
            assert exp.ci_low_1s <= 0


def test_one_sided_bound_is_above_the_two_sided_lower_bound():
    exp = analyze(_trades(300, 0.62, seed=5))
    assert exp.ci_low_1s > exp.ci_low
    assert exp.ci_low_1s < exp.mean_pnl


def test_conservative_kelly_needs_an_established_edge():
    """No one-sided significance -> no size at all."""
    weak = analyze(_trades(60, 0.62, seed=8))
    assert weak.ci_low_1s <= 0
    assert conservative_kelly(weak) == 0.0
