"""Sports: the data loaders, the ratings, and the arithmetic that decides money."""

from __future__ import annotations

import pytest

from sports.backtest import accuracy, log_loss, roi
from sports.data import AWAY, DRAW, HOME, Match, parse_date
from sports.edge import (breakeven_price, devig, ev_per_dollar, measure_sides,
                         overround)
from sports.elo import EloModel, margin_multiplier


def match(home="A", away="B", hg=2, ag=1, odds=(2.0, 3.4, 3.6), day=1):
    return Match("E0", (2021, 1, day), home, away, hg, ag, list(odds))


# ---- dates: the bug that silently scrambles everything -------------------
def test_three_date_formats_parse_to_one_shape():
    assert parse_date("01/02/14") == (2014, 2, 1)
    assert parse_date("01/02/2014") == (2014, 2, 1)
    assert parse_date("2014-02-01") == (2014, 2, 1)


def test_dates_sort_chronologically_as_tuples():
    """A string sort puts 01/02/14 after 31/12/2022 and hands Elo the future."""
    ds = [parse_date("31/12/2022"), parse_date("01/02/14"), parse_date("01/02/2020")]
    assert sorted(ds) == [parse_date("01/02/14"), parse_date("01/02/2020"),
                          parse_date("31/12/2022")]


def test_bad_date_is_an_error_not_a_default():
    with pytest.raises(ValueError):
        parse_date("not a date")


# ---- odds maths ---------------------------------------------------------
def test_devig_removes_the_margin_and_sums_to_one():
    probs = devig([2.0, 3.4, 3.6])
    assert sum(probs) == pytest.approx(1.0)
    assert overround([2.0, 3.4, 3.6]) > 0


def test_devig_rejects_impossible_odds():
    with pytest.raises(ValueError):
        devig([1.0, 3.0, 3.0])
    with pytest.raises(ValueError):
        devig([2.0, 3.0])


def test_ev_and_breakeven_agree():
    p = 0.40
    q = breakeven_price(p)
    assert q == pytest.approx(p)                 # no fee: break even at p
    assert ev_per_dollar(p, q) == pytest.approx(0.0, abs=1e-12)
    assert ev_per_dollar(p, q - 0.02) > 0
    assert ev_per_dollar(p, q + 0.02) < 0


def test_a_fee_pushes_the_breakeven_price_down():
    assert breakeven_price(0.50, 0.07) < 0.50
    assert breakeven_price(0.50, 0.0) == pytest.approx(0.50)


def test_measure_sides_flags_only_the_cheap_side():
    sharp = [1.50, 4.50, 7.00]                   # strong home favourite
    home_p = devig(sharp)[0]
    rows = measure_sides(sharp, {HOME: home_p - 0.05, AWAY: 0.40})
    by = {r["side"]: r for r in rows}
    assert by["home"]["take"] is True            # bought below fair value
    assert by["away"]["take"] is False           # 40c for a ~10% chance


# ---- the ratings model --------------------------------------------------
def test_goal_margin_multiplier_grows_with_the_margin():
    assert margin_multiplier(0) == margin_multiplier(1) == 1.0
    assert margin_multiplier(2) == 1.5
    assert margin_multiplier(5) > margin_multiplier(3)


def test_walking_updates_both_teams_in_opposite_directions():
    m = EloModel()
    m.walk([match(hg=3, ag=0)])
    assert m.rating("A") > 1500 > m.rating("B")


def test_fit_must_come_after_walk_or_every_match_lands_in_one_bin():
    """The bug that made the model score 42.74% -- worse than always-home."""
    history = [match(home=f"H{i%6}", away=f"V{i%6}", hg=i % 3, ag=(i + 1) % 3,
                     day=1 + i % 28) for i in range(120)]

    wrong = EloModel().fit(history)              # fit first: all ratings at 1500
    assert len(wrong.table) == 1

    right = EloModel()
    right.walk(history)
    right.fit(history)
    assert len(right.table) > 1


def test_predict_returns_a_distribution():
    m = EloModel()
    hist = [match(home=f"H{i%8}", away=f"V{i%8}", hg=i % 4, ag=(i + 2) % 4,
                  day=1 + i % 28) for i in range(200)]
    m.walk(hist)
    m.fit(hist)
    probs = m.predict("H1", "V2")
    assert len(probs) == 3 and sum(probs) == pytest.approx(1.0)


# ---- scoring ------------------------------------------------------------
def test_accuracy_and_logloss_on_a_trivial_case():
    rows = [([0.7, 0.2, 0.1], HOME), ([0.7, 0.2, 0.1], AWAY)]
    assert accuracy(rows) == 0.5
    assert log_loss(rows) > 0


def test_roi_accounts_for_the_stake():
    bets = [(2.0, True), (2.0, False)]
    n, acc, r = roi(bets)
    assert n == 2 and acc == 0.5 and r == pytest.approx(0.0)
