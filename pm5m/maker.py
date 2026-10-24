"""Market-making strategy for the 50-60c band.

WHY MAKER AND NOT TAKER

At 55c the taker fee is 1.73c per share -- 3.15% of stake. To overcome it you
need to be 1.73 percentage points more accurate than the market on *every*
trade, forever. Break-even win rate 56.73% against a market implying 55%. That
is a demanding forecasting requirement, and it is the reason the `taker` mode
should be switched off in this band.

A maker pays nothing and receives a rebate, so break-even drops to the quote
price itself. That is the entire argument for quoting instead of taking.

The catch, and it is severe: a resting bid only fills when someone wants to sell
to you, and people want to sell to you precisely when the thing is about to be
worth less than your bid. This is adverse selection, and it is what kills naive
market makers. The fill mechanism in `pm5m.sim` reproduces it endogenously --
you get hit when the counterparty's idea of fair value falls below your bid --
rather than hiding it behind a fudge factor.

So the maker strategy here is not "quote anything and collect rebates". It is:
quote only when you believe the counterparty's fair value is wrong, and size
small enough to survive being right about the mechanism and wrong about the
magnitude.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .config import Settings
from .model import ProbView, fair_up
from .risk import RiskManager
from .settlement import SettlementIndex
from .types import Book, Market


@dataclass(frozen=True)
class Quote:
    """A resting limit bid. `fair` is our belief; `price` is what we offer."""

    side: str
    token_id: str
    price: float
    shares: float
    fair: float
    edge: float
    ts: float

    @property
    def edge_after_adverse_selection(self) -> float:
        return self.edge


def effective_p_on_fill(fair: float, adverse_cents: float) -> float:
    """Probability of winning *given that our resting bid was hit*.

    Unconditional fair value overstates a maker's win rate: fills are not a
    random sample of moments, they are the moments when informed flow disagrees
    with you. `adverse_cents` shifts the conditional probability down.

    Measure this from your own fill history. It is not derivable from the model,
    and it is the number that decides whether market making in this band is
    profitable at all.
    """
    return max(0.0, fair - adverse_cents / 100.0)


def plan_quotes(
    market: Market,
    state: SettlementIndex,
    books: dict[str, Book],
    spot: float,
    sigma: float,
    now: float,
    settings: Settings,
    risk: RiskManager | None = None,
    held: set[str] | None = None,
) -> list[Quote]:
    """Decide where to rest bids this instant.

    Refuses to quote:
      * outside the configured price band (mid-range only, by design)
      * on a side we already hold (no adding to a losing inventory in a 300s game)
      * where the required edge is not there
      * across the spread (we stay a maker; crossing would mean paying the fee)
      * below the best bid by less than a tick (that is not a quote, that is noise)
    """
    remaining = state.remaining_tau(now)
    if remaining < settings.min_remaining_s or now < state.start_ts:
        return []

    view: ProbView = fair_up(
        state, spot, sigma, now, settings.drift_per_s, settings.z_cap
    )
    held = held or set()
    quotes: list[Quote] = []
    max_shares = settings.maker_quote_shares
    if risk is not None:
        headroom = settings.max_total_exposure_usd - risk.exposure_usd
        affordable = headroom / max(settings.min_price, 1e-6)
        max_shares = min(max_shares, affordable)
    if max_shares < 1.0:
        return []

    for side, fair in (("up", view.p_up), ("down", view.p_down)):
        book = books.get(side)
        if book is None or book.best_ask is None:
            continue
        token = market.token_for(side)
        if token in held:
            continue

        # Floor to the tick, never round up: rounding toward the spread is how a
        # "maker" quote accidentally becomes a taker order.
        raw = fair - settings.maker_edge_cents / 100.0
        price = math.floor(raw / market.tick_size + 1e-9) * market.tick_size
        price = round(price, 4)
        if not (settings.min_price <= price <= settings.max_price):
            continue
        # Stay strictly inside the spread: no crossing, no joining a worse level.
        if price >= book.best_ask - 1e-9:
            continue
        if book.best_bid is not None and price <= book.best_bid:
            continue
        edge = fair - price
        if edge < settings.min_edge_cents / 100.0:
            continue

        quotes.append(
            Quote(
                side=side,
                token_id=token,
                price=price,
                shares=round(max_shares, 2),
                fair=fair,
                edge=edge,
                ts=now,
            )
        )
    return quotes


def quote_is_filled(quote: Quote, book: Book) -> bool:
    """Our resting bid was hit when the best ask comes down to meet it."""
    return book.best_ask is not None and book.best_ask <= quote.price + 1e-9
