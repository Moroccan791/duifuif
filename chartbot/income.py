"""The simple version: one decision a day, and the truth about the money.

Two functions, because they answer the only two questions that matter once the
strategy is chosen:

    today()    -> what do I do with my money right now?
    capital_for() -> how much money does $X a day actually take?

`today()` is intentionally the simplest possible interface to the tested
strategy. No indicators to read, no stops to place, no charts to interpret: one
of three answers, once a day, after the daily candle closes.

The second function exists because the arithmetic is the part people skip. A
strategy that returns 40% a year turns $100 into $41 of profit over twelve
months. That is $0.11 a day. Getting to $10 a day is not a strategy problem --
it is a capital problem, and no signal, stop loss or leverage trick moves it
without also moving the risk of losing everything.
"""

from __future__ import annotations

from dataclasses import dataclass

from .data import Candle
from .position import PositionConfig, PositionRunner
from .strategies import realized_vol, sma


@dataclass(frozen=True)
class DailyCall:
    """What to do today, in one line, plus the numbers behind it."""

    action: str          # "buy", "sell", "hold"
    target: float        # fraction of equity that should be in BTC
    distance: float      # how far price sits above/below the average
    sma_value: float
    price: float
    vol: float
    reason: str

    def line(self, equity: float) -> str:
        if self.action == "sell":
            return "SELL EVERYTHING - stay in cash"
        if self.action == "buy":
            return f"BUY {self.target:.0%} of equity  (${self.target * equity:,.2f})"
        return "HOLD - no change"


def today(candles: list[Candle], equity: float = 100.0,
          cfg: PositionConfig | None = None) -> DailyCall:
    """The whole strategy, as one decision.

    Long when the close is above its 50-day average, and sized so that the
    position's expected volatility is 25% a year. That is it. The position is
    re-checked once a day; intraday wiggles are noise to this strategy and
    trading them only pays fees.
    """
    cfg = cfg or PositionConfig()
    closes = [c.close for c in candles]
    if len(closes) < max(cfg.sma_window, cfg.vol_lookback) + 1:
        raise ValueError(f"need at least {cfg.sma_window + 1} candles")

    price = closes[-1]
    ma = sma(closes, cfg.sma_window)[-1]
    rets = [0.0] + [closes[i] / closes[i - 1] - 1.0 for i in range(1, len(closes))]
    vol = realized_vol(rets, len(rets) - 1, cfg.vol_lookback)
    distance = price / ma - 1.0 if ma > 0 else 0.0

    if price <= ma or vol <= 0:
        return DailyCall("sell", 0.0, distance, ma, price, vol,
                         f"price {distance:+.1%} vs its {cfg.sma_window}-day average")
    target = min(cfg.max_lev, cfg.target_vol / vol)
    return DailyCall("buy", target, distance, ma, price, vol,
                     f"price {distance:+.1%} above its {cfg.sma_window}-day average, "
                     f"vol {vol:.0%}")


def daily_from_annual(capital: float, annual_return: float) -> float:
    """Average profit per day from a compounded annual return."""
    if capital <= 0:
        return 0.0
    return capital * annual_return / 365.0


def capital_for(target_per_day: float, annual_return: float) -> float:
    """Capital needed to average `target_per_day`, at a given annual return.

    Deliberately un-compounded: it answers "what float produces this much
    income", not "what will I have in ten years". Any plan that needs the
    profit to compound *and* pay you has to satisfy both, which is why drawing
    an income slows the account down.
    """
    if annual_return <= 0:
        return float("inf")
    return target_per_day * 365.0 / annual_return


def months_to_double(annual_return: float) -> float:
    if annual_return <= 0:
        return float("inf")
    import math
    return math.log(2) / math.log(1.0 + annual_return) * 12.0
