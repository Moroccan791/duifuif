"""The live loop for the strategy that actually survived testing.

`chartbot.runner` implements the discretionary-style bot: RSI gates, pullback
entries, ATR stops. It lost 17.6% over eleven years of real Bitcoin, because it
trades 174 times on a weak signal with tight stops.

This module implements the thing that survived `chartbot.strategies`:

    position = 1 if close > SMA(50) else 0
    size     = position * target_vol / realized_vol        (capped at 1x)

Long only, no stops, ~10 trades a year. The evidence behind it is in RULES.md;
the short version is that it ranked first on BTC, ETH and SOL, and that most of
what it buys you is the drawdown, not the return.

There are no stop losses here, and that is deliberate rather than an omission.
A tight stop on a slow trend signal is what turned a mediocre strategy into a
-17% one. The exit condition is the moving average crossing, at daily granularity.
A stop that fires inside a day would be fighting the signal it is meant to protect.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from .broker import Broker, BrokerError
from .data import Candle
from .strategies import realized_vol, sma


@dataclass
class PositionConfig:
    sma_window: int = 50
    target_vol: float = 0.25
    vol_lookback: int = 60
    max_lev: float = 1.0        # spot: never borrow
    rebalance: float = 0.10     # only trade when the target moves >10% of equity
    min_trade_usd: float = 5.0


@dataclass
class PositionState:
    ts: float = 0.0
    price: float = 0.0
    sma: float = 0.0
    vol: float = 0.0
    target: float = 0.0         # target position, as a fraction of equity
    current: float = 0.0        # current position, same units
    action: str = ""
    note: str = ""


@dataclass
class PositionRunner:
    broker: Broker
    cfg: PositionConfig = field(default_factory=PositionConfig)
    history: list[PositionState] = field(default_factory=list)

    # -- helpers ------------------------------------------------------------
    def target_position(self, candles: list[Candle]) -> tuple[float, float, float]:
        """(target fraction of equity, sma value, realised vol)."""
        closes = [c.close for c in candles]
        if len(closes) < max(self.cfg.sma_window, self.cfg.vol_lookback) + 1:
            return 0.0, 0.0, 0.0
        line = sma(closes, self.cfg.sma_window)
        ma = line[-1]
        rets = [0.0] + [closes[i] / closes[i - 1] - 1.0 for i in range(1, len(closes))]
        v = realized_vol(rets, len(rets) - 1, self.cfg.vol_lookback)

        if closes[-1] <= ma or v <= 0:
            return 0.0, ma, v
        scale = min(self.cfg.max_lev, self.cfg.target_vol / v)
        return max(0.0, scale), ma, v

    def current_position(self, price: float) -> float:
        eq = self.broker.equity(price)
        if eq <= 0:
            return 0.0
        return abs(self.broker.position.qty) * price / eq

    # -- one decision -------------------------------------------------------
    def step(self, candles: list[Candle], ts: float | None = None) -> PositionState:
        price = candles[-1].close
        ts = ts if ts is not None else candles[-1].ts
        # Roll the calendar BEFORE anything reads `halted()`. Yesterday's close is
        # the right opening mark: comparing today's equity to today's own close
        # would report zero daily P&L and never trip the limit either.
        prev = candles[-2].close if len(candles) > 1 else price
        self.broker.begin_bar(ts, mark=prev)
        self.broker.mark_to(price)

        target, ma, vol = self.target_position(candles)

        # Respect the broker's hard notional cap by *sizing to it*, never by
        # trying an order that gets rejected. A rejected order leaves the bot
        # flat while its target stays high, so it retries and is rejected every
        # single bar -- silently flat for years, which is exactly what a $100
        # account did at BTC's 2021 lows: vol collapsed to 24%, the target went
        # to 100% of equity, the 50% cap refused it, and the bot sat out the
        # entire recovery.
        cap = self.broker.limits.max_notional_pct
        clipped = ""
        if target > cap:
            clipped = f" (target cut from {target:.2f} by the {cap:.0%} notional cap)"
            target = cap

        current = self.current_position(price)

        state = PositionState(ts=ts, price=price, sma=ma, vol=vol,
                              target=target, current=current)

        if self.broker.halted():
            state.note = f"halted: {self.broker.halted()}"
            self.history.append(state)
            return state

        delta = target - current
        equity = self.broker.equity(price)

        # Only act on a meaningful change. Rebalancing on every tick would turn a
        # 10-trade-a-year strategy into a fee machine.
        if abs(delta) < self.cfg.rebalance:
            state.note = f"holding ({current:.2f} vs target {target:.2f}){clipped}"
            self.history.append(state)
            return state

        notional_delta = abs(delta) * equity
        if notional_delta < self.cfg.min_trade_usd:
            state.note = f"below minimum trade size{clipped}"
            self.history.append(state)
            return state

        try:
            if target <= 0.0 and self.broker.position.is_open:
                self.broker.close_position(price, ts, "sma exit")
                state.action = "closed - price below SMA"
            elif target > 0.0:
                want_qty = target * equity / price
                have_qty = self.broker.position.qty
                diff = want_qty - have_qty
                if abs(diff) * price < self.cfg.min_trade_usd:
                    state.note = "difference below minimum"
                else:
                    self.broker.add_to_position(diff, price, ts=ts,
                                                reason="sma rebalance")
                    verb = "bought" if diff > 0 else "sold"
                    state.action = (f"{verb} {abs(diff):.6f} -> "
                                    f"{target:.2f} of equity")
        except BrokerError as e:
            state.action = f"blocked: {e}"

        self.history.append(state)
        return state

def describe(candles: list[Candle], cfg: PositionConfig | None = None) -> str:
    """Human-readable state of the position strategy, for the CLI and dashboard."""
    cfg = cfg or PositionConfig()
    closes = [c.close for c in candles]
    line = sma(closes, cfg.sma_window)
    ma = line[-1]
    rets = [0.0] + [closes[i] / closes[i - 1] - 1.0 for i in range(1, len(closes))]
    v = realized_vol(rets, len(rets) - 1, cfg.vol_lookback)
    above = closes[-1] > ma
    scale = min(cfg.max_lev, cfg.target_vol / v) if v > 0 else 0.0
    target = scale if above else 0.0
    lines = [
        f"price        {closes[-1]:>12,.2f}",
        f"SMA({cfg.sma_window})     {ma:>12,.2f}   "
        f"price is {'ABOVE' if above else 'BELOW'} it",
        f"realised vol {v*100:>11.1f}%   (target {cfg.target_vol*100:.0f}%)",
        f"target       {target*100:>11.0f}% of equity in BTC",
    ]
    return "\n".join(lines)
