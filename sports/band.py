"""The 50-65 cent band: what a price has to be worth for buying it to pay.

Everything here is arithmetic, not opinion. The one number that matters is the
**breakeven win rate**: buy a share at price ``p`` and you either get 1 or 0, so
you need to win more than ``p`` of the time. Fees push that up.

    breakeven(p) = p + fee

Polymarket's sports fee is charged on notional at roughly ``0.07 * p * (1 - p)``
per share, which peaks near 50c and is small at the extremes. At 58.5c that is
about 1.7c, so a 58.5c leg needs a 60.2% win rate to break even.

This is why an "accurate" model is not the same as a profitable one. A model
that is right 57.7% of the time on 58.5c legs loses money. The band test in
``FINDINGS.md`` measures exactly that, and the answer is that the band does not
pay with the models in this repo.
"""

from __future__ import annotations

from dataclasses import dataclass

BAND_LO = 0.50
BAND_HI = 0.65

# Polymarket taker fee coefficient on notional (see FINDINGS for the source).
FEE_COEF = 0.07


def fee_per_share(price: float, coef: float = FEE_COEF) -> float:
    """Fee paid per share bought at ``price``. Peaks near 50c, ~0 at the tails."""
    p = min(max(price, 0.0), 1.0)
    return coef * p * (1.0 - p)


def breakeven(price: float, coef: float = FEE_COEF) -> float:
    """Win rate needed for buying at ``price`` to be exactly break-even."""
    return min(1.0, price + fee_per_share(price, coef))


def payout_per_dollar(price: float, coef: float = FEE_COEF) -> float:
    """Net profit on a win, per dollar staked."""
    cost = price + fee_per_share(price, coef)
    if cost <= 0:
        return 0.0
    return (1.0 - cost) / cost


def ev_per_dollar(prob: float, price: float, coef: float = FEE_COEF) -> float:
    """Expected profit per dollar staked, given your win probability."""
    cost = price + fee_per_share(price, coef)
    if cost <= 0:
        return 0.0
    return prob * (1.0 - cost) / cost - (1.0 - prob)


def kelly_fraction(prob: float, price: float, coef: float = FEE_COEF) -> float:
    """Full-Kelly stake as a fraction of bankroll. Negative means do not bet.

    Deliberately returns full Kelly so callers must choose to shrink it. In this
    repo we scale it down a lot, because the probabilities carry calibration
    error and Kelly on a miscalibrated number is a fast way to ruin.
    """
    cost = price + fee_per_share(price, coef)
    if cost <= 0 or cost >= 1:
        return 0.0
    b = (1.0 - cost) / cost
    f = (prob * (b + 1.0) - 1.0) / b
    return max(0.0, f)


def in_band(price: float, lo: float = BAND_LO, hi: float = BAND_HI) -> bool:
    return lo <= price <= hi


@dataclass
class Leg:
    """One side of one market, priced, with your probability attached."""

    event: str
    name: str
    price: float
    prob: float

    @property
    def ev(self) -> float:
        return ev_per_dollar(self.prob, self.price)

    @property
    def breakeven(self) -> float:
        return breakeven(self.price)

    @property
    def edge(self) -> float:
        return self.prob - self.breakeven

    @property
    def banded(self) -> bool:
        return in_band(self.price)

    def verdict(self, min_edge: float = 0.02) -> str:
        if not self.banded:
            return f"SKIP (outside {BAND_LO:.0%}-{BAND_HI:.0%})"
        if self.edge < min_edge:
            return f"PASS (edge {self.edge:+.1%} < {min_edge:.0%})"
        return f"BUY (edge {self.edge:+.1%})"

    def line(self) -> str:
        return (f"{self.event:<28} {self.name:<14} {self.price*100:5.1f}c "
                f"model {self.prob*100:5.1f}%  need {self.breakeven*100:5.1f}%  "
                f"edge {self.edge*100:+5.1f}%  {self.verdict()}")


def rank(legs: list[Leg], min_edge: float = 0.02) -> list[Leg]:
    """Banded legs with enough edge, best first. Empty list means do nothing."""
    good = [l for l in legs if l.banded and l.edge >= min_edge]
    return sorted(good, key=lambda l: l.edge, reverse=True)


def band_arithmetic() -> list[tuple[float, float, float]]:
    """(price, breakeven, payout per $1) across the band, for display."""
    out = []
    for c in range(int(BAND_LO * 100), int(BAND_HI * 100) + 1, 5):
        p = c / 100
        out.append((p, breakeven(p), payout_per_dollar(p)))
    return out
