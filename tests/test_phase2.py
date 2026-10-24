"""Phase 2: the decisive test.

The most important test here is `test_control_shows_no_edge`. The first version
of `Bucket.is_actionable` used the point estimate and duly reported
"EDGE CANDIDATE" on 63 windows of pure noise. A tool that finds edges in noise is
worse than no tool, so the significance criterion is pinned down.
"""

from __future__ import annotations

import pytest

from pm5m.history import Window, parse_gamma_market, synthetic_windows, twap_proxy
from pm5m.phase2 import Bucket, analyse, calibration, proxy_error
from pm5m.twap import gap_in_sigmas


def _b(n, wins, market, lo=None, hi=None):
    b = Bucket(
        lo=lo if lo is not None else 0.0,
        hi=hi if hi is not None else 1.0,
        n=n,
        wins=wins,
        mean_fair=0.6,
        mean_market=market,
        mean_price=100_000.0,
    )
    return b


# ---- the significance gate ------------------------------------------------
def test_point_estimate_alone_is_not_actionable():
    """60% realised against a 50c market, on 40 trades, proves nothing."""
    b = _b(40, 24, 0.50)
    assert b.edge_vs_market > 0
    assert b.is_actionable() is False


def test_small_sample_is_never_actionable():
    b = _b(50, 50, 0.50)  # 100% win rate, plenty of apparent edge
    assert b.edge_vs_market > 0.4
    assert b.is_actionable() is False


def test_established_edge_is_actionable():
    b = _b(2000, 1300, 0.55)  # 65% vs 55c, n=2000
    assert b.is_actionable() is True


def test_fee_is_subtracted_from_the_bar():
    b = _b(800, 560, 0.68)  # 70% vs 68c: 2pp gross, ~1.5pp fee
    assert b.edge_vs_market > 0
    assert b.is_actionable() is False  # too thin after fees


def test_needed_n_grows_as_the_edge_shrinks():
    big = _b(500, 350, 0.55)   # 70% vs 55c
    small = _b(500, 300, 0.55)  # 60% vs 55c
    assert big.needed_n() < small.needed_n()


def test_needed_n_is_unbounded_when_there_is_no_gap():
    b = _b(500, 280, 0.56)  # below the market, so no gap at all
    assert b.needed_n() >= 10**8


# ---- the control must not hallucinate ------------------------------------
def test_control_shows_no_edge():
    """No mispricing injected -> the analysis must not claim one."""
    windows = synthetic_windows(n=1500, seed=3, market_edge_bps=0.0)
    report = analyse(windows)
    assert report.actionable() == []
    assert "NOT PROVEN" in report.verdict() or "NO EDGE" in report.verdict()


def test_a_large_injected_edge_is_detected():
    windows = synthetic_windows(n=3000, seed=3, market_edge_bps=2000)
    report = analyse(windows)
    act = report.actionable()
    assert act, "a 20pp mispricing on 3000 windows must be detectable"
    assert "EDGE CANDIDATE" in report.verdict()


def test_model_error_without_a_market_gap_is_not_tradeable():
    """The model can be badly wrong with nothing to trade.

    Realised 70%, the book quotes 70c, our model said 55%. The model error is
    +15pp and worth knowing about -- but trading our view against a correctly
    priced book earns exactly nothing.
    """
    b = _b(2000, 1400, 0.70)
    b.mean_fair = 0.55
    assert abs(b.model_error) > 0.1          # the model IS wrong
    assert b.edge_vs_market <= b.fee()       # but there is no gap to trade
    assert b.is_actionable() is False


# ---- proxy validation -----------------------------------------------------
def test_proxy_error_is_tiny_on_the_fixture():
    windows = synthetic_windows(n=800, seed=7)
    check = proxy_error(windows)
    assert check.n == 800
    assert check.usable
    assert check.ratio < 0.1


def test_proxy_error_detects_a_bad_proxy():
    windows = synthetic_windows(n=200, seed=7)
    for w in windows:
        w.strike_proxy = w.strike * 1.001  # 10bp off
    check = proxy_error(windows)
    assert not check.usable


def test_no_proxy_data_is_not_usable():
    windows = synthetic_windows(n=50, seed=1)
    for w in windows:
        w.strike_proxy = None
    check = proxy_error(windows)
    assert check.n == 0
    assert not check.usable


# ---- calibration ----------------------------------------------------------
def test_calibration_buckets_cover_all_windows():
    windows = synthetic_windows(n=600, seed=9)
    buckets = calibration(windows)
    assert sum(b.n for b in buckets) == 600


def test_model_tracks_realised_on_the_fixture():
    """The fixture is generated FROM the model, so calibration must agree."""
    windows = synthetic_windows(n=4000, seed=9)
    report = analyse(windows)
    for b in report.buckets:
        if b.n >= 200:
            assert abs(b.model_error) < 0.07, f"bucket {b.lo}..{b.hi} off by {b.model_error:.3f}"


def test_gap_ordering_is_monotonic():
    windows = synthetic_windows(n=2000, seed=10)
    buckets = calibration(windows)
    rates = [b.realised for b in buckets]
    # Higher x must mean a higher up rate.
    assert rates == sorted(rates)


# ---- parsing --------------------------------------------------------------
def test_parse_gamma_market_extracts_the_essentials():
    m = {
        "slug": "btc-updown-5m-1790000000",
        "outcomePrices": '["1", "0"]',
        "volumeNum": 123.4,
        "eventMetadata": '{"priceToBeat": "100000.5", "finalPrice": "100040.2"}',
    }
    w = parse_gamma_market(m)
    assert w is not None
    assert w.outcome == "up"
    assert w.strike == pytest.approx(100000.5)
    assert w.final_price == pytest.approx(100040.2)
    assert w.start_ts == 1790000000.0


def test_parse_rejects_unresolved_markets():
    m = {"slug": "btc-updown-5m-1790000000", "outcomePrices": '["0.5", "0.5"]'}
    assert parse_gamma_market(m) is None


def test_parse_rejects_junk_slugs():
    assert parse_gamma_market({"slug": "not-a-window", "outcomePrices": '["1","0"]'}) is None


def test_twap_proxy_averages_the_trailing_window():
    klines = [(i * 1000, 100.0 + i) for i in range(0, 200)]
    # Klines at t = 41s..100s inclusive carry prices 141..200 -> mean 170.5
    assert twap_proxy(klines, 100_000) == pytest.approx(170.5)


def test_twap_proxy_returns_none_without_data():
    assert twap_proxy([], 100_000) is None
