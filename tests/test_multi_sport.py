"""Tests for the 50-65c band engine, the second sport, and live parsing."""

import json
import math

import pytest

from sports import band, live, tennis


# --------------------------------------------------------------------------
# band arithmetic -- these are the numbers that decide real money
# --------------------------------------------------------------------------
def test_breakeven_is_never_below_price():
    """With a fee it exceeds the price; with no fee (sports) it equals it."""
    for c in range(1, 100):
        p = c / 100
        assert band.breakeven(p) >= p
        assert band.breakeven(p, band.FEE_COEF_POLITICS) > p


def test_sports_breakeven_is_just_the_price():
    """Sports markets are fee-free, so breakeven == price. Verified on gamma."""
    assert band.FEE_COEF == 0.0
    for c in (0.50, 0.585, 0.65):
        assert band.breakeven(c) == pytest.approx(c, abs=1e-12)


def test_breakeven_with_a_politics_style_fee():
    # if a fee of 0.07 did apply, 58.5c would need ~60.2%
    assert band.breakeven(0.585, band.FEE_COEF_POLITICS) == pytest.approx(0.602, abs=0.002)
    assert band.breakeven(0.50, band.FEE_COEF_POLITICS) == pytest.approx(0.5175, abs=0.002)
    assert band.breakeven(0.65, band.FEE_COEF_POLITICS) == pytest.approx(0.6659, abs=0.002)


def test_a_58_percent_model_loses_at_band_prices():
    """The central economic fact: being right 58% of the time is not enough.

    Note this holds with NO fee at all -- the price is simply the truth.
    """
    p = 0.585
    assert band.ev_per_dollar(0.58, p) < 0
    assert band.ev_per_dollar(0.62, p) > 0
    # and it is worse once a fee applies
    assert band.ev_per_dollar(0.62, p, band.FEE_COEF_POLITICS) < band.ev_per_dollar(0.62, p)


def test_fee_is_zero_at_the_extremes():
    f = band.FEE_COEF_POLITICS
    assert band.fee_per_share(0.0, f) == pytest.approx(0.0)
    assert band.fee_per_share(1.0, f) == pytest.approx(0.0)
    assert band.fee_per_share(0.5, f) == pytest.approx(0.0175)
    # sports charges nothing anywhere
    assert band.fee_per_share(0.5) == pytest.approx(0.0)


def test_ev_and_kelly_agree_on_sign():
    for price in (0.52, 0.58, 0.63):
        be = band.breakeven(price)
        assert band.ev_per_dollar(be - 0.05, price) < 0
        assert band.ev_per_dollar(be + 0.05, price) > 0
        assert band.kelly_fraction(be - 0.05, price) == 0.0
        assert band.kelly_fraction(be + 0.05, price) > 0


def test_kelly_never_exceeds_one():
    for price in (0.05, 0.3, 0.5, 0.8):
        assert 0 <= band.kelly_fraction(0.99, price) <= 1.0


def test_band_membership_is_inclusive():
    assert band.in_band(0.50) and band.in_band(0.65)
    assert not band.in_band(0.49) and not band.in_band(0.66)


def test_rank_filters_and_orders():
    legs = [
        band.Leg("e", "a", 0.55, 0.62),   # edge +
        band.Leg("e", "b", 0.55, 0.50),   # edge -
        band.Leg("e", "c", 0.80, 0.95),   # good edge, outside band
    ]
    good = band.rank(legs, min_edge=0.02)
    assert [l.name for l in good] == ["a"]


def test_verdict_separates_skip_from_pass():
    assert "SKIP" in band.Leg("e", "x", 0.80, 0.90).verdict()
    assert "PASS" in band.Leg("e", "x", 0.55, 0.556).verdict()
    assert "BUY" in band.Leg("e", "x", 0.55, 0.70).verdict()


# --------------------------------------------------------------------------
# tennis ratings
# --------------------------------------------------------------------------
def test_update_is_symmetric_for_equal_players():
    """Regression: the loser must drop by their OWN expectancy, not the winner's.

    The original code applied -k*(1-E_winner) to the loser, so a heavy favourite
    winning dropped the underdog ~9x too far, inflating rating spread and making
    the model wildly overconfident.
    """
    r = tennis.TennisRatings()
    r.update("A", "B", "Hard", 20240101)
    gain_a = r.overall["A"] - 1500.0
    loss_b = 1500.0 - r.overall["B"]
    # equal ratings -> both expect 0.5 -> symmetric movement
    assert gain_a == pytest.approx(loss_b, rel=1e-9)
    assert gain_a > 0


def test_loser_drops_by_their_own_expectancy_not_the_winners():
    """The exact bug: loser must fall by k*E_loser, not k*(1-E_winner).

    For a 1900 vs 1500 pairing E_loser = 0.0909, so the correct drop is
    ~11.9 points. The buggy version subtracted k*0.909 and dropped ~119.
    """
    r = tennis.TennisRatings(overall={"fav": 1900.0, "dog": 1500.0})
    k_before = r.k("dog")
    e_loser = 1.0 / (1.0 + 10 ** (-(1500.0 - 1900.0) / 400.0))
    r.update("fav", "dog", "Hard", 20240101)
    drop = 1500.0 - r.overall["dog"]
    assert drop == pytest.approx(k_before * e_loser, rel=1e-9)
    assert drop < k_before * 0.5      # the old bug would fail this


def test_upset_moves_ratings_a_lot():
    r = tennis.TennisRatings(overall={"fav": 1900.0, "dog": 1500.0})
    r.update("dog", "fav", "Hard", 20240101)
    assert r.overall["dog"] > 1500.0
    assert r.overall["fav"] < 1900.0


def test_calibration_shrinks_toward_half():
    assert tennis.calibrate(0.5) == pytest.approx(0.5, abs=1e-6)
    assert tennis.calibrate(0.60) < 0.60
    assert tennis.calibrate(0.40) > 0.40
    assert 0.5 < tennis.calibrate(0.60) < 0.60


def test_fit_shrink_recovers_overconfidence():
    # data where truth is 55% but the model claims 65%
    rows = [(0.65, True)] * 55 + [(0.65, False)] * 45
    a, ll = tennis.fit_shrink(rows)
    assert a < 1.0
    assert tennis.calibrate(0.65, a) < 0.65


def test_fit_shrink_is_near_one_when_already_calibrated():
    rows = [(0.60, True)] * 60 + [(0.60, False)] * 40
    a, _ = tennis.fit_shrink(rows)
    assert a == pytest.approx(1.0, abs=0.06)


def test_rating_is_symmetric_in_players():
    r = tennis.TennisRatings(overall={"A": 1800.0, "B": 1600.0})
    assert r.predict("A", "B") + r.predict("B", "A") == pytest.approx(1.0)


def test_unknown_players_default_to_even():
    r = tennis.TennisRatings()
    assert r.predict("who1", "who2") == pytest.approx(0.5)


# --------------------------------------------------------------------------
# inactivity decay
# --------------------------------------------------------------------------
def test_decay_pulls_inactive_players_toward_average():
    r = tennis.TennisRatings(overall={"p": 2000.0}, last={"p": 20200101})
    near = tennis.decayed_rating(r, "p", 20200101)
    later = tennis.decayed_rating(r, "p", 20210101)
    much = tennis.decayed_rating(r, "p", 20250101)
    assert near == pytest.approx(2000.0)
    assert much < later < near
    assert much > 1500.0


def test_decay_ignores_future_dates():
    r = tennis.TennisRatings(overall={"p": 2000.0}, last={"p": 20240101})
    assert tennis.decayed_rating(r, "p", 20230101) == pytest.approx(2000.0)


def test_decay_half_life_is_one_year():
    r = tennis.TennisRatings(overall={"p": 2000.0}, last={"p": 20200101})
    one_year = tennis.decayed_rating(r, "p", 20210101)   # 366 days, leap year
    assert one_year == pytest.approx(1750.0, rel=0.01)


def test_predict_asof_matches_predict_when_fresh():
    r = tennis.TennisRatings(overall={"A": 1800.0, "B": 1600.0},
                             last={"A": 20240101, "B": 20240101})
    a = tennis.predict_asof(r, "A", "B", 20240101)
    assert a == pytest.approx(tennis.calibrate(r.predict("A", "B")))


# --------------------------------------------------------------------------
# name joining -- Polymarket truncates, Sackmann does not
# --------------------------------------------------------------------------
def test_match_player_handles_full_name():
    known = {"Carlos Alcaraz": 2281.0, "Jannik Sinner": 2367.0}
    assert tennis.match_player("Carlos Alcaraz", known) == ("Carlos Alcaraz", 1.0)


def test_match_player_handles_truncation():
    known = {"Daniel Altmaier": 1717.0}
    name, conf = tennis.match_player("altmaie", known)
    assert name == "Daniel Altmaier" and conf > 0.5


def test_match_player_strips_polymarket_digit_suffix():
    known = {"Alex Molcan": 1874.0}
    name, conf = tennis.match_player("molcan1", known)
    assert name == "Alex Molcan" and conf == 1.0


def test_match_player_rejects_garbage():
    name, conf = tennis.match_player("zzzzqqq", {"Carlos Alcaraz": 1.0})
    assert name is None and conf == 0.0


def test_match_player_rejects_too_short_queries():
    assert tennis.match_player("ab", {"Ab Cd": 1.0}) == (None, 0.0)


def test_ambiguous_surname_does_not_claim_certainty():
    known = {"Juan Carlos Ferrero": 1.0, "Carlos Moya": 1.0}
    name, conf = tennis.match_player("carlos", known)
    assert conf < 0.7


# --------------------------------------------------------------------------
# live parsing
# --------------------------------------------------------------------------
def _market(**kw):
    d = {
        "question": "Q", "slug": "s", "id": "1",
        "outcomes": json.dumps(["A", "B"]),
        "outcomePrices": json.dumps(["0.55", "0.46"]),
        "volumeNum": "100", "bestAsk": 0.56, "bestBid": 0.54,
    }
    d.update(kw)
    return d


def test_legs_use_ask_not_mid():
    legs = live.legs_from_market(_market())
    assert len(legs) == 2
    assert legs[0]["price"] == pytest.approx(0.56)


def test_legs_keep_both_sides_independent():
    """A 55/46 pair sums to 101c; we must not derive one side from the other."""
    legs = live.legs_from_market(_market(bestAsk=None))
    assert sum(l["price"] for l in legs) == pytest.approx(1.01, abs=1e-9)


def test_legs_from_market_tolerates_bad_json():
    legs = live.legs_from_market(_market(outcomes="not json"))
    assert legs == []


def test_legs_from_market_handles_list_prices():
    legs = live.legs_from_market(_market(outcomePrices=["0.5", "0.5"]))
    assert len(legs) == 2


def test_summarise_counts_band_legs():
    legs = [{"price": 0.55}, {"price": 0.45}, {"price": 0.62}]
    s = live.summarise(legs)
    assert s["legs"] == 3 and s["in_band"] == 2


# --------------------------------------------------------------------------
# CLI wiring
# --------------------------------------------------------------------------
def test_scan_runs_on_fixture(tmp_path, capsys):
    from sports.cli import main
    ratings = tennis.TennisRatings(
        overall={"Carlos Alcaraz": 2100.0, "Jannik Sinner": 2300.0},
        last={"Carlos Alcaraz": 20260101, "Jannik Sinner": 20260101})
    rp = tmp_path / "r.json"
    tennis.save_ratings(ratings, str(rp))
    snap = tmp_path / "s.json"
    snap.write_text(json.dumps([_market(outcomes=json.dumps(["Carlos Alcaraz", "Jannik Sinner"]),
                                       outcomePrices=json.dumps(["0.55", "0.46"]))]))
    rc = main(["scan", "--file", str(snap), "--ratings", str(rp), "--asof", "20260115"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "legs in" in out


def test_predict_needs_data(tmp_path):
    from sports.cli import main
    assert main(["predict", "--dir", str(tmp_path), "--home", "A", "--away", "B"]) == 2


def test_ratings_roundtrip(tmp_path):
    r = tennis.TennisRatings(overall={"A": 1700.0}, played={"A": 12},
                             last={"A": 20250101})
    r.surface[("A", "Clay")] = 1750.0
    p = tmp_path / "r.json"
    tennis.save_ratings(r, str(p))
    back = tennis.load_ratings(str(p))
    assert back.overall["A"] == pytest.approx(1700.0)
    assert back.surface[("A", "Clay")] == pytest.approx(1750.0)
    assert back.last["A"] == 20250101


def test_band_arithmetic_table_is_monotone_in_price():
    rows = band.band_arithmetic()
    assert rows[0][0] == pytest.approx(0.50)
    assert all(b >= p for p, b, _ in rows)
    # payout per dollar falls as price rises
    assert rows[0][2] > rows[-1][2]
