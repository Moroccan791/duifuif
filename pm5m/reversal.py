"""The one measurable edge in the five-minute markets: short-horizon reversal.

Everything here is measured, not assumed. The measurement is in `REVERSAL.md`
and it is reproducible with `python -m pm5m reversal --csv '1m/*.csv'`.

The claim, precisely:

    After the price has moved more than 0.4% in five minutes, the probability
    that the NEXT five-minute window settles *against* that move is 53.9%,
    not 50%.

Measured on Binance 1-minute klines converted to the market's actual
settlement rule (Up iff TWAP60(close) >= TWAP60(open), ties Up):

    BTC 2022-2026   53.91%   [53.05, 54.76]   13,128 windows
    ETH 2025-2026   54.46%   [53.45, 55.47]    9,372
    SOL 2025-2026   53.48%   [52.65, 54.31]   13,732

and it gets stronger as the move gets bigger (55.3% above 0.8%, 57.6% above
1.0%), which is the signature of a real mechanism -- forced selling and
liquidations overshooting -- rather than a data artifact. It is present in
every calendar year from 2022 to 2026.

WHAT THIS MODULE IS NOT

It is not a promise of profit. The hit rate above is the probability of the
*event*. Your profit is (probability - price - fee). At the 7% taker fee the
market charges on crypto, a 53.91% event is only worth buying below **52.16c**.
At 52c it is worth +0.31% per trade -- break-even in practice, not a business.
At 50c it is worth +4.32%. At 53c it loses 1.57% a trade and the fee eats you.
The price is not a detail; it is the whole trade.

So the entire question -- the only one that actually decides this trade -- is
what the reversion side costs on Polymarket in the seconds after a 0.4% move.
That number cannot be computed from klines. It has to be recorded off the order
book, which is what `pm5m record` exists for. Until it is measured, this module
tells you whether a *given* price is worth taking, and nothing more.
"""

from __future__ import annotations

from dataclasses import dataclass

# The fee the crypto category charges takers, verified in `pm5m.fees`.
DEFAULT_FEE_RATE = 0.07

# Move threshold for a signal. Below this the hit rate decays toward the
# coin flip: 50.2% under 0.05%, 50.8% at 0.05-0.10%, 52.0% at 0.10-0.20%.
MIN_MOVE = 0.004

# Measured hit rates for fading a move of at least this size. Deliberately a
# lookup rather than a fitted curve: these are counts, and a fit would imply
# precision the sample does not support.
_BANDS: tuple[tuple[float, float, int], ...] = (
    # (minimum |move|, hit rate, sample size) -- BTC 2022-2026
    (0.0040, 0.5391, 13_128),
    (0.0060, 0.5425, 4_479),
    (0.0080, 0.5529, 1_948),
    (0.0100, 0.5761, 927),
)


def fade_probability(abs_move: float) -> float:
    """Historical probability that a window settles against a move this big.

    Returns 0.5 when the move is too small to be a signal -- i.e. no view.
    """
    rate = 0.5
    for threshold, hit, _n in _BANDS:
        if abs_move >= threshold:
            rate = hit
    return rate


def band_note(abs_move: float) -> str:
    for threshold, hit, n in reversed(_BANDS):
        if abs_move >= threshold:
            return f"{hit:.1%} historically (n={n:,} windows at >= {threshold:.1%})"
    return "below the 0.40% threshold - no evidence of an edge"


def breakeven_ask(p_true: float, rate: float = DEFAULT_FEE_RATE) -> float:
    """Highest price at which buying this side is not a loss.

    Fee is `rate * p * (1 - p)` per share, so EV per share is
    `p_true - ask - rate*ask*(1-ask)`. Setting that to zero:

        0.07*q^2 - 1.07*q + p_true = 0

    and the small root is the break-even ask. At p_true = 0.5391 that is 52.16c.
    """
    if not 0.0 <= p_true <= 1.0:
        raise ValueError("p_true must be a probability between 0 and 1")
    a, b, c = rate, -(1.0 + rate), p_true
    disc = b * b - 4.0 * a * c
    if disc < 0:
        return 0.0
    return (-b - disc ** 0.5) / (2.0 * a)


def ev_per_dollar(p_true: float, ask: float, rate: float = DEFAULT_FEE_RATE) -> float:
    """Expected profit per dollar staked, buying one side at `ask`."""
    if ask <= 0.0:
        raise ValueError("ask must be positive")
    fee = rate * ask * (1.0 - ask)
    return (p_true - ask - fee) / ask


def kelly_fraction(p_true: float, ask: float, rate: float = DEFAULT_FEE_RATE) -> float:
    """Fraction of bankroll Kelly says to stake. Zero when the bet is negative."""
    if ask <= 0.0:
        raise ValueError("ask must be positive")
    cost = ask + rate * ask * (1.0 - ask)
    if cost >= p_true:
        return 0.0
    # win `1 - cost` per share, lose `cost`; b = payout per unit risked
    b = (1.0 - cost) / cost
    return max(0.0, (p_true * (1.0 + b) - 1.0) / b)


@dataclass(frozen=True)
class FadeSignal:
    """What the last five minutes say, and what that is worth at a given price."""

    direction: str          # "down" or "up" -- the side to BUY
    move: float             # signed five-minute move
    p_true: float           # measured probability that this side settles
    breakeven_ask: float    # pay below this and the trade is +EV
    reason: str

    @property
    def fires(self) -> bool:
        return self.p_true > 0.5

    def ev(self, ask: float, rate: float = DEFAULT_FEE_RATE) -> float:
        return ev_per_dollar(self.p_true, ask, rate) if self.fires else 0.0

    def worth_taking(self, ask: float, rate: float = DEFAULT_FEE_RATE) -> bool:
        """Fire only if the side is cheap enough to survive the fee."""
        return self.fires and ask < self.breakeven_ask


def fade_signal(close_start: float, close_now: float) -> FadeSignal:
    """Signal from the two settlement-relevant prices.

    `close_start` is the 60-second TWAP at the start of the last window and
    `close_now` the one at its close -- in practice the last two 1-minute
    closes, which is what the settlement comparison actually uses.
    """
    if close_start <= 0 or close_now <= 0:
        raise ValueError("prices must be positive")
    move = close_now / close_start - 1.0
    size = abs(move)
    p = fade_probability(size)
    direction = "down" if move > 0 else "up"
    return FadeSignal(
        direction=direction,
        move=move,
        p_true=p,
        breakeven_ask=breakeven_ask(p) if p > 0.5 else 0.5,
        reason=band_note(size),
    )
