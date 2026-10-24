"""The five-minute reversal signal: the arithmetic that decides the trade."""

from __future__ import annotations

import pytest

from pm5m.reversal import (
    MIN_MOVE,
    breakeven_ask,
    ev_per_dollar,
    fade_probability,
    fade_signal,
    kelly_fraction,
)


def test_breakeven_matches_the_fee_algebra():
    """EV is p - q - 0.07q(1-q); the price that zeroes it is the answer."""
    q = breakeven_ask(0.5391)
    assert q == pytest.approx(0.5216, abs=0.0002)
    assert ev_per_dollar(0.5391, q) == pytest.approx(0.0, abs=1e-9)


def test_ev_flips_sign_at_the_breakeven_price():
    assert ev_per_dollar(0.5391, 0.50) == pytest.approx(0.0432, abs=0.001)
    assert ev_per_dollar(0.5391, 0.52) > 0          # barely
    assert ev_per_dollar(0.5391, 0.53) < 0          # already losing


def test_fee_alone_needs_a_real_edge():
    """A coin flip bought at 50c is a 3.5%-of-stake loser. That is the fee.

    So a true 50% event has to be bought at 48.25c merely to break even.
    """
    assert ev_per_dollar(0.50, 0.50) == pytest.approx(-0.035, abs=0.001)
    assert breakeven_ask(0.50) == pytest.approx(0.4825, abs=0.002)


def test_probability_rises_with_move_size_and_is_flat_below_the_threshold():
    assert fade_probability(0.001) == 0.5
    assert fade_probability(0.0039) == 0.5
    assert fade_probability(MIN_MOVE) > 0.53
    assert fade_probability(0.009) > fade_probability(0.005)
    assert fade_probability(0.02) == pytest.approx(0.5761)


def test_signal_fades_the_move_rather_than_following_it():
    up = fade_signal(100.0, 101.0)      # +1% move
    assert up.direction == "down"
    assert up.move == pytest.approx(0.01)
    assert up.fires and up.p_true > 0.55

    down = fade_signal(100.0, 99.0)
    assert down.direction == "up"
    assert down.fires


def test_small_move_is_not_a_signal():
    s = fade_signal(100.0, 100.1)
    assert s.move == pytest.approx(0.001)
    assert not s.fires
    assert s.ev(0.40) == 0.0            # never fires, so never "cheap"
    assert not s.worth_taking(0.40)


def test_worth_taking_requires_price_below_breakeven():
    s = fade_signal(100.0, 101.0)
    assert s.worth_taking(0.49)
    assert not s.worth_taking(0.60)          # well above the 55.9c break-even
    assert not s.worth_taking(s.breakeven_ask)


def test_kelly_is_zero_on_a_negative_bet_and_sane_on_a_positive_one():
    assert kelly_fraction(0.50, 0.50) == 0.0
    assert kelly_fraction(0.5391, 0.53) == 0.0
    f = kelly_fraction(0.5391, 0.50)
    # ~4.5% of bankroll: the entry fee raises the effective cost from 50c to
    # 51.75c, which shrinks Kelly well below the fee-free 2p-1 = 7.8%.
    assert 0.03 < f < 0.07


def test_certainty_and_impossibility_do_not_crash():
    """A 100% event is worth paying up to 100c; a 0% event, nothing."""
    assert breakeven_ask(1.0) == pytest.approx(1.0, abs=1e-6)
    assert breakeven_ask(0.0) == pytest.approx(0.0, abs=1e-9)


def test_bad_inputs_raise():
    with pytest.raises(ValueError):
        fade_signal(0.0, 100.0)
    with pytest.raises(ValueError):
        breakeven_ask(1.5)
    with pytest.raises(ValueError):
        breakeven_ask(-0.1)
    with pytest.raises(ValueError):
        ev_per_dollar(0.55, 0.0)


# ---- the CLI must not be able to lie about it ----------------------------
def test_reversal_command_measures_a_known_planted_series(tmp_path, capsys):
    """Build klines with a deliberate reversal and check the command sees it.

    The signal uses the move that ALREADY happened. An earlier version of this
    command measured the forward move instead -- which is the outcome itself --
    and reported a 0% hit rate, because fading the outcome is wrong every time.
    That bug is invisible without a test like this one.
    """
    from pm5m.cli import main

    # The command's window grid starts at minute index 61, so the pattern has to
    # be built on a 10-minute cycle to land on it: minute 0 jumps +1.5%, minute
    # 1 gives it all back. Every window then has a big trailing move that fully
    # reverses inside the window, so a correct signal hits ~100%.
    rows, ts, px = [], 1_600_000_000_000, 100.0
    for i in range(4_500):
        if i % 10 == 0:
            px *= 1.015
        elif i % 10 == 1:
            px /= 1.015
        rows.append(f"{ts},x,{px},{px},{px},{px},1,1,1,0.5,0.5")
        ts += 60_000
    f = tmp_path / "klines.csv"
    f.write_text("timestamp,datetime_utc,open,high,low,close,volume,quote_volume,"
                 "trade_count,taker_buy_volume,taker_buy_quote_volume\n"
                 + "\n".join(rows) + "\n")

    assert main(["reversal", "--csv", str(f)]) == 0
    out = capsys.readouterr().out
    import re
    m = re.search(r"hit rate ([0-9.]+)%", out)
    assert m, out
    pct = float(m.group(1))
    assert pct > 90.0, out          # a planted reversal must be found
    assert "break-even price" in out
