"""Is this price wrong enough to be worth taking?

This is the only module that decides whether money moves, and it decides it by
comparison rather than by prediction:

    edge = sharp_probability - price_you_pay - fee

A model that agrees with the sharp book is worthless -- the book already said it
and the price already contains it. What pays is the gap between a *sharp
reference price* (Pinnacle's close, de-vigged) and Polymarket's price, when that
gap is bigger than the cost of being wrong.

Three cautions, all learned the hard way elsewhere in this repository:

* The reference must be de-vigged. Comparing a raw decimal odd to a probability
  invents an edge out of the bookmaker's margin.
* The comparison must be like-for-like in time. A sharp line from kickoff minus
  two hours compared to a Polymarket price ninety seconds before kickoff is not
  an arbitrage, it is a news event.
* Only the *closing* line is a fair benchmark. Beating an opening line means
  nothing; the close is where the money settles.
"""

from __future__ import annotations

HOME, DRAW, AWAY = 0, 1, 2


def devig(odds: list[float]) -> list[float]:
    """Decimal odds -> probabilities that sum to 1, proportionally."""
    if len(odds) != 3 or any(o <= 1.0 for o in odds):
        raise ValueError("need three decimal odds above 1.0")
    inv = [1.0 / o for o in odds]
    total = sum(inv)
    return [x / total for x in inv]


def overround(odds: list[float]) -> float:
    return sum(1.0 / o for o in odds) - 1.0


def ev_per_dollar(p_true: float, price: float, fee_rate: float = 0.0) -> float:
    """Expected profit per dollar staked, buying a binary at `price`.

    Polymarket's taker fee, where one applies, is `rate * price * (1 - price)`
    per share, so the all-in cost is `price + rate*price*(1-price)`.
    """
    if not 0.0 < price < 1.0:
        raise ValueError("price must be between 0 and 1")
    fee = fee_rate * price * (1.0 - price)
    return (p_true - price - fee) / price


def breakeven_price(p_true: float, fee_rate: float = 0.0) -> float:
    """The highest price at which buying is not a loss.

    Solves `p = q + rate*q*(1-q)` for the small root of q.
    """
    if not 0.0 <= p_true <= 1.0:
        raise ValueError("p_true must be a probability")
    if fee_rate <= 0:
        return p_true
    a, b, c = fee_rate, -(1.0 + fee_rate), p_true
    disc = b * b - 4.0 * a * c
    if disc < 0:
        return 0.0
    return (-b - disc ** 0.5) / (2.0 * a)


def required_disagreement(price: float, sharp_odds: list[float],
                          fee_rate: float = 0.0) -> float:
    """How far Polymarket's price must sit below the sharp line, in probability.

    Returns the gap for the side that is cheapest to buy. A result near zero
    means the two venues agree and there is nothing to do.
    """
    sharp = devig(sharp_odds)
    gaps = []
    for i in range(3):
        q = price if i == HOME else (1.0 - price)   # placeholder for symmetry
        gaps.append(sharp[i] - breakeven_price(sharp[i], fee_rate))
    return min(gaps)


def measure_sides(sharp_odds: list[float],
                  prices: dict[int, float],
                  fee_rate: float = 0.0) -> list[dict]:
    """Score each side: what the sharp book says, what you would pay, the edge."""
    sharp = devig(sharp_odds)
    out = []
    for side, name in ((HOME, "home"), (DRAW, "draw"), (AWAY, "away")):
        if side not in prices:
            continue
        q = prices[side]
        p = sharp[side]
        out.append({
            "side": name,
            "sharp_prob": p,
            "price": q,
            "edge": ev_per_dollar(p, q, fee_rate),
            "breakeven": breakeven_price(p, fee_rate),
            "take": q < breakeven_price(p, fee_rate),
        })
    return out
