"""Core value types shared across the framework."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Level:
    price: float
    size: float


@dataclass(frozen=True)
class Book:
    """One side of the market: bids are sorted descending, asks ascending.

    Only the levels we were given are stored; a snapshot with a single level per
    side is still a valid Book and is what the recorder captures by default.
    """

    token_id: str
    ts: float
    bids: tuple[Level, ...] = ()
    asks: tuple[Level, ...] = ()

    @property
    def best_bid(self) -> float | None:
        return self.bids[0].price if self.bids else None

    @property
    def best_ask(self) -> float | None:
        return self.asks[0].price if self.asks else None

    @property
    def mid(self) -> float | None:
        if self.best_bid is None or self.best_ask is None:
            return None
        return (self.best_bid + self.best_ask) / 2.0

    @property
    def spread(self) -> float | None:
        if self.best_bid is None or self.best_ask is None:
            return None
        return self.best_ask - self.best_bid

    @property
    def ask_size(self) -> float:
        return sum(l.size for l in self.asks)

    @property
    def bid_size(self) -> float:
        return sum(l.size for l in self.bids)

    def walk_buy(self, limit_price: float, max_shares: float, book_fraction: float = 1.0) -> tuple[float, float]:
        """Simulate a marketable buy up to `limit_price`.

        Returns (shares_filled, notional_paid). Never returns a price better than
        what is actually displayed -- this function is the difference between a
        believable backtest and a fantasy one.
        """
        remaining = max_shares
        notional = 0.0
        filled = 0.0
        for lvl in self.asks:
            if lvl.price > limit_price + 1e-12 or remaining <= 0:
                break
            take = min(lvl.size * book_fraction, remaining)
            if take <= 0:
                continue
            notional += take * lvl.price
            filled += take
            remaining -= take
        return filled, notional

    def walk_sell(self, limit_price: float, max_shares: float, book_fraction: float = 1.0) -> tuple[float, float]:
        """Simulate a marketable sell (exit) down to `limit_price`."""
        remaining = max_shares
        notional = 0.0
        filled = 0.0
        for lvl in self.bids:
            if lvl.price < limit_price - 1e-12 or remaining <= 0:
                break
            take = min(lvl.size * book_fraction, remaining)
            if take <= 0:
                continue
            notional += take * lvl.price
            filled += take
            remaining -= take
        return filled, notional


@dataclass(frozen=True)
class Market:
    slug: str
    condition_id: str
    asset: str
    start_ts: float
    end_ts: float
    token_up: str
    token_down: str
    event_id: str = ""
    tick_size: float = 0.01
    open_price: float | None = None

    def token_for(self, side: str) -> str:
        return self.token_up if side == "up" else self.token_down

    @property
    def duration(self) -> float:
        return self.end_ts - self.start_ts


@dataclass(frozen=True)
class Signal:
    """A proposed taker entry. The engine, not the strategy, decides the size."""

    market_slug: str
    token_id: str
    side: str
    limit_price: float
    p_true: float
    edge_per_share: float
    reason: str
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class Fill:
    ts: float
    market_slug: str
    token_id: str
    side: str
    shares: float
    price: float
    fee: float
    notional: float
    reason: str = ""

    @property
    def cost(self) -> float:
        """Total cash out the door."""
        return self.notional + self.fee


@dataclass
class Position:
    token_id: str
    market_slug: str
    side: str
    shares: float = 0.0
    cost: float = 0.0      # cash out the door: notional + fees
    notional: float = 0.0  # price paid for shares only, fees excluded

    @property
    def avg_price(self) -> float:
        """Price paid per share, NOT including fees.

        Keeping fees out of this matters: if they are folded in, the recorded
        fee is zero by construction and the expectancy model prices every trade
        as fee-free.
        """
        return self.notional / self.shares if self.shares else 0.0

    @property
    def fees(self) -> float:
        return max(0.0, self.cost - self.notional)
