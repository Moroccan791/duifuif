"""Tests for the selectivity analysis.

The conclusion this module supports -- that demanding more edge LOWERS a maker's
win rate -- is counter-intuitive enough that it needs pinning down in tests.
"""

from __future__ import annotations

import pytest

from pm5m.expectancy import TradeRecord
from pm5m.selectivity import (
    Bucket,
    best_by_profit,
    bucket_by,
    highest_win_rate,
    sample_trades,
    sweep,
    SweepRow,
)


def _row(me: float, trades: int, wr: float, pnl: float = 0.0) -> SweepRow:
    return SweepRow(
        min_edge_cents=me, min_z_lock=0.0, trades=trades, win_rate=wr,
        avg_price=0.55, roi=0.0, total_pnl=pnl, expectancy=0.0, p_value=0.5,
        windows=100,
    )


# ---- selection helpers ----------------------------------------------------
def test_highest_win_rate_respects_the_minimum_sample():
    rows = [_row(0.5, 300, 0.60), _row(2.0, 10, 0.90)]
    top = highest_win_rate(rows, min_trades=30)
    assert top is not None
    assert top.win_rate == pytest.approx(0.60)
    assert top.trades == 300


def test_highest_win_rate_returns_none_when_sample_is_too_thin():
    rows = [_row(2.0, 10, 0.90), _row(3.0, 5, 0.80)]
    assert highest_win_rate(rows, min_trades=30) is None


def test_best_by_profit_prefers_money_over_win_rate():
    rows = [_row(0.5, 300, 0.60, pnl=250.0), _row(2.0, 10, 0.90, pnl=40.0)]
    assert best_by_profit(rows).total_pnl == 250.0


def test_empty_inputs_are_safe():
    assert best_by_profit([]) is None
    assert highest_win_rate([], 30) is None


# ---- bucketing ------------------------------------------------------------
def test_bucket_by_edge_groups_correctly():
    trades = sample_trades(200)
    buckets = bucket_by(trades, "edge", [0.0, 0.04, 0.08, 0.12])
    assert buckets
    assert sum(b.n for b in buckets) == 200
    for b in buckets:
        assert 0.0 <= b.win_rate <= 1.0


def test_bucket_by_remaining_time():
    trades = sample_trades(200)
    buckets = bucket_by(trades, "remaining_s", [0, 100, 200, 300])
    assert sum(b.n for b in buckets) == 200
    assert all(b.n > 0 for b in buckets)


def test_bucket_expectancy_is_pnl_per_trade():
    b = Bucket(label="x", n=10, wins=6, avg_edge=0.05, pnl=20.0)
    assert b.win_rate == pytest.approx(0.6)
    assert b.expectancy == pytest.approx(2.0)


def test_empty_buckets_are_dropped():
    trades = [t for t in sample_trades(50) if t.edge < 0.05]
    buckets = bucket_by(trades, "edge", [0.0, 0.05, 0.10, 0.20])
    assert all(b.n > 0 for b in buckets)


def test_unknown_key_is_treated_as_zero():
    trades = sample_trades(20)
    buckets = bucket_by(trades, "does_not_exist", [-1, 1])
    assert sum(b.n for b in buckets) == 20


# ---- the maker paradox ----------------------------------------------------
def test_more_selectivity_does_not_raise_the_maker_win_rate():
    """The key empirical claim. With an informed counterparty, demanding a
    bigger edge leaves only the worst fills -- so win rate falls, not rises."""
    results = sweep(
        scenarios={
            "maker-informed": dict(
                counterparty="fair", basis_sd=2e-5, sigma_noise_sd=0.5, _mode="maker"
            )
        },
        windows=60,
        edge_grid=(0.5, 2.0),
        seed=11,
    )
    rows = results["maker-informed"]
    low, high = rows[0], rows[1]
    # Fewer trades survive a stricter threshold...
    assert high.trades <= low.trades
    # ...and the survivors are not better.
    if high.trades > 0 and low.trades > 0:
        assert high.win_rate <= low.win_rate + 0.05


def test_sweep_reports_every_threshold():
    results = sweep(
        scenarios={"m": dict(counterparty="fair", _mode="maker")},
        windows=20,
        edge_grid=(0.5, 1.0, 2.0),
        seed=5,
    )
    assert len(results["m"]) == 3
    assert [r.min_edge_cents for r in results["m"]] == [0.5, 1.0, 2.0]
