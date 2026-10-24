"""Edge detection and Kelly sizing.

Philosophy: this module refuses to trade the three things that kill 5-minute
bots -- coin flips, late entries, and certainty that is only certain in your own
model.

* Coin flips: a 50c market with a 0.4c fee advantage is not an edge, it is a
  donation. Trades must clear `min_edge_cents` *after* fees and *after* a
  latency slippage assumption.
* Late entries: below `min_remaining_s` the settlement index is already frozen
  in practice but the book has not always caught up; that residual is a race
  against faster bots, not an edge.
* Model certainty: `min_z_lock` forces a real sigma cushion, and `max_price`
  caps what you will pay for it.

Sizing is fractional Kelly on the *all-in* cost per share (price plus fee), which
is the only cost basis that matters when you are paying a taker fee at 50c.
"""

from __future__ import annotations

import math

from .config import Settings
from .fees import taker_fee_per_share
from .model import ProbView, fair_up
from .settlement import SettlementIndex
from .types import Book, Market, Signal


def kelly_fraction_stake(p_true: float, cost_per_share: float) -> float:
    """Kelly fraction of bankroll for a binary paying 1.0, all-in cost c.

        f* = (p - c) / (1 - c)

    Clamped at zero; never leveraged.
    """
    c = min(max(cost_per_share, 0.0), 0.999)
    f = (p_true - c) / (1.0 - c)
    return max(0.0, f)


def _entry_price(book: Book, tick: float, slippage_ticks: float) -> float | None:
    ask = book.best_ask
    if ask is None:
        return None
    return min(0.999, ask + slippage_ticks * tick)


def evaluate(
    market: Market,
    state: SettlementIndex,
    books: dict[str, Book],
    spot: float,
    sigma: float,
    now: float,
    settings: Settings,
) -> list[Signal]:
    """Return zero or more candidate taker entries right now.

    `books` maps side ("up"/"down") to that side's own order book. Passing a
    single book for both sides is a classic way to buy the wrong contract at the
    right price, so the type makes it explicit.

    At most one signal is produced: the better of the two sides.
    """
    remaining = state.remaining_tau(now)
    if remaining < settings.min_remaining_s:
        return []
    if now < state.start_ts:
        return []

    view: ProbView = fair_up(
        state, spot, sigma, now, settings.drift_per_s, settings.z_cap
    )

    candidates: list[Signal] = []
    for side, p_true in (("up", view.p_up), ("down", view.p_down)):
        book = books.get(side)
        if book is None:
            continue
        entry = _entry_price(book, market.tick_size, settings.slippage_ticks)
        if entry is None:
            continue
        if entry > settings.max_price:
            continue
        fee_ps = taker_fee_per_share(entry, settings.taker_fee_rate)
        cost = entry + fee_ps
        edge = p_true - cost
        z = view.z if side == "up" else -view.z
        if z < settings.min_z_lock:
            continue
        if edge < settings.min_edge_cents / 100.0:
            continue
        candidates.append(
            Signal(
                market_slug=market.slug,
                token_id=market.token_for(side),
                side=side,
                limit_price=entry,
                p_true=p_true,
                edge_per_share=edge,
                reason=(
                    f"z={z:.2f} p={p_true:.4f} ask={entry:.3f} "
                    f"fee={fee_ps:.4f} edge={edge:.4f} tau={remaining:.0f}s"
                ),
                meta={
                    "z": z,
                    "cost_per_share": cost,
                    "fee_per_share": fee_ps,
                    "remaining_s": remaining,
                    "spot": spot,
                    "sigma": sigma,
                    "cushion": view.cushion_price,
                    "sd_price": view.sd_price,
                    "book_ts": book.ts,
                },
            )
        )

    if not candidates:
        return []
    best = max(candidates, key=lambda s: s.edge_per_share)
    return [best] if best.edge_per_share > 0 else []


def size_signal(
    signal: Signal,
    book: Book,
    settings: Settings,
    bankroll_usd: float,
    exposure_usd: float,
) -> tuple[float, str]:
    """Convert a signal into a share count. Returns (shares, explanation).

    Sizing is the *last* thing bounded, after liquidity: you cannot edge-trade
    more size than the book is showing, and pretending otherwise is how
    backtests turn into real losses.
    """
    cost = signal.meta.get("cost_per_share", signal.limit_price)
    f_star = kelly_fraction_stake(signal.p_true, cost)
    if f_star <= 0:
        return 0.0, "kelly<=0"

    stake = bankroll_usd * settings.kelly_fraction * f_star
    stake = min(stake, settings.max_position_usd)
    headroom = settings.max_total_exposure_usd - exposure_usd
    stake = min(stake, max(0.0, headroom))
    if stake < 1.0:
        return 0.0, f"stake {stake:.2f} below $1 minimum"

    shares_wanted = stake / max(cost, 1e-9)
    shares_fill, notional = book.walk_buy(
        signal.limit_price, shares_wanted, settings.max_book_fraction
    )
    if shares_fill <= 0:
        return 0.0, "no liquidity at limit"

    # Do not spend more than the stake allows even if the book is deeper.
    affordable = stake / max(notional / max(shares_fill, 1e-9), 1e-9)
    shares = min(shares_fill, affordable)
    if shares < 1.0:
        return 0.0, "size below 1 share"

    return math.floor(shares * 100) / 100.0, (
        f"kelly_f*={f_star:.3f} stake=${stake:.2f} shares={shares:.2f}"
    )
