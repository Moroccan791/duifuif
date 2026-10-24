"""Win rate is a thing you BUY, not a thing you ACHIEVE.

This module exists to kill the most expensive misconception in binary trading:
that a high win rate is a sign of skill, and that 70-80% is a target to aim for.

In a binary market paying 1.00 or 0.00, the win rate you achieve is determined
almost entirely by the price you pay. Buy at 78c and you will win about 78% of
the time -- not because you are good, but because that is what the market
already believes and what the arithmetic delivers. A high win rate is a receipt
for the price you paid, not evidence of an edge.

The consequences are counter-intuitive and worth seeing in numbers:

1. **Win rate and profit are decoupled.** The same 78% win rate is profitable,
   break-even, or ruinous depending only on the entry price.
2. **Higher win rate means bigger losses.** At 78c a loss is 3.5x a win. At 95c
   a loss is 19x a win. The higher your win rate, the harder the tail bites.
3. **Streaks are guaranteed, not unlucky.** At 80% you should *expect* a 5-loss
   streak roughly every thousand trades. It is not a broken strategy; it is the
   binomial distribution, and it arrives on schedule.
4. **High prices need less absolute edge** but the edge must still exist, and it
   is much harder to *know* that it exists, because the events are near-certain
   and therefore well priced by everyone else.

The only quantity that matters is expectancy: true accuracy minus all-in cost.
Win rate is a property of the price, and the price is public information.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .fees import taker_fee_per_share


def all_in_cost(price: float, fee_rate: float = 0.07) -> float:
    """What one share costs, fees included. Also the break-even win rate."""
    return price + taker_fee_per_share(price, fee_rate)


def break_even_win_rate(price: float, fee_rate: float = 0.07) -> float:
    """The accuracy you need for the trade to be worth taking.

    Because a winning share pays exactly 1.00, the break-even win rate equals
    the all-in cost per share. That identity is worth memorising.
    """
    return all_in_cost(price, fee_rate)


def expectancy_per_share(win_rate: float, price: float, fee_rate: float = 0.07) -> float:
    return win_rate - all_in_cost(price, fee_rate)


def expectancy_on_stake(win_rate: float, price: float, fee_rate: float = 0.07) -> float:
    cost = all_in_cost(price, fee_rate)
    return (win_rate - cost) / cost if cost > 0 else 0.0


def fair_win_rate(price: float) -> float:
    """If the market is efficient, this is the win rate you get for this price."""
    return price


def loss_to_win_ratio(price: float) -> float:
    """A loss costs this many winning trades at the same size."""
    return price / (1.0 - price)


def expected_longest_loss_streak(win_rate: float, n_trades: int) -> float:
    """Longest run of consecutive losses expected in n trades.

    Approximation for the expected longest failure run: log(n)/log(1/q) where q
    is the failure probability. Accurate enough to make the point that streaks
    are scheduled, not exceptional.
    """
    q = 1.0 - win_rate
    if q <= 0 or q >= 1 or n_trades < 2:
        return 0.0
    return math.log(n_trades) / math.log(1.0 / q)


def prob_streak_at_least(win_rate: float, k: int, n_trades: int) -> float:
    """Probability of seeing at least one run of >= k losses in n trades."""
    q = 1.0 - win_rate
    if q <= 0:
        return 0.0
    if q >= 1:
        return 1.0
    # Expected number of run-starts, times the probability the tail completes.
    return min(1.0, n_trades * (q**k) * win_rate)


@dataclass(frozen=True)
class WinRateRow:
    price: float
    fair_win_rate: float
    break_even: float
    edge_needed_pp: float
    loss_win_ratio: float
    fee_pct_of_stake: float
    ev_if_2pp_better: float

    @property
    def verdict(self) -> str:
        if self.price < 0.10:
            return "lottery ticket: 1 loss wipes many wins"
        if self.price < 0.70:
            return "high variance, fee is the main obstacle"
        if self.price < 0.90:
            return "big losses per win; needs real accuracy"
        return "deep tail: one loss erases ~19 wins"


def table(
    prices: tuple[float, ...] = (0.50, 0.60, 0.70, 0.78, 0.80, 0.90, 0.95, 0.98),
    fee_rate: float = 0.07,
    edge_pp: float = 2.0,
) -> list[WinRateRow]:
    """The win-rate / profit decoupling, one row per entry price."""
    rows = []
    for p in prices:
        be = break_even_win_rate(p, fee_rate)
        # If you were genuinely edge_pp better than the market:
        true_w = min(0.999, p + edge_pp / 100.0)
        rows.append(
            WinRateRow(
                price=p,
                fair_win_rate=p,
                break_even=be,
                edge_needed_pp=(be - p) * 100.0,
                loss_win_ratio=loss_to_win_ratio(p),
                fee_pct_of_stake=taker_fee_per_share(p, fee_rate) / p * 100.0,
                ev_if_2pp_better=expectancy_per_share(true_w, p, fee_rate),
            )
        )
    return rows


def same_winrate_scenarios(win_rate: float = 0.78, shares: float = 100.0) -> list[str]:
    """The same win rate, three entry prices, three completely different outcomes.

    This is the heart of the module: identical accuracy, opposite results.
    """
    lines = [
        f"You win {win_rate:.0%} of your trades either way. {shares:.0f} shares "
        f"of risk per trade. What differs is the price you paid.",
        "",
    ]
    for price in (0.60, 0.78, 0.90):
        cost = all_in_cost(price) * shares
        wins = round(win_rate * 1000)
        losses = 1000 - wins
        gross = wins * (1.0 - all_in_cost(price)) * shares - losses * all_in_cost(price) * shares
        tag = "PROFIT" if gross > 0 else ("break-even" if abs(gross) < 1 else "LOSS")
        lines.append(
            f"  buy at {price*100:4.0f}c -> {tag:>10s} over 1,000 trades: "
            f"${gross:+,.0f}   (all-in {all_in_cost(price)*100:.2f}c/share, "
            f"loss = {loss_to_win_ratio(price):.1f}x a win)"
        )
    lines.append("")
    lines.append(
        "  At 78c the market already says 78%. Hitting 78% is not skill -- it is\n"
        "  the price. You only make money if you hit MORE than the price implies,\n"
        "  and at 60c that same 78% accuracy is a genuine 18-point edge."
    )
    return lines


def accuracy_you_need(price: float, fee_rate: float = 0.07) -> str:
    """Plain-language statement of the required edge at one price."""
    be = break_even_win_rate(price, fee_rate)
    return (
        f"At {price*100:.0f}c the market implies {price:.0%}. You need "
        f"{be:.2%} to break even ({(be - price) * 100:+.2f}pp of genuine edge) "
        f"and more than that to profit."
    )
