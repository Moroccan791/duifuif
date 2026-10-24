"""Probability model for the 5-minute Up/Down settlement.

WHY THIS IS NOT JUST "IS SPOT ABOVE THE OPEN?"

Settlement compares the *average* of the price path over the window against the
open price. Write the window length as L, the integral of already-observed prices
as K, and the (unknown) integral of the remaining block as R. Then

    S = (K + R) / L        and        Up wins iff S >= P0

i.e. Up wins iff R >= P0 * L - K. So everything reduces to the distribution of
R, the integral of a geometric Brownian motion over the remaining time tau.

For GBM with zero drift, starting at P(t):

    E[R]      = P(t) * tau
    Var[R]    = P(t)^2 * 2 * [ (e^{s^2 tau} - 1)/s^4 - tau/s^2 - tau^2/2 ]

with s the per-root-second volatility (see the derivation in the test suite,
which checks these against Monte Carlo). The variance reduces to
P^2 s^2 tau^3 / 3 for small s, which is the well-known result for the integral
of Brownian motion.

Then P(Up) = 1 - Phi((P0*L - K - E[R]) / sd(R)).

Two properties make this model useful rather than decorative:

1. It is analytic -- no Monte Carlo noise, microseconds to evaluate, so it can
   run on every book update.
2. It captures the convergence that gives these markets their only tradeable
   structure: as tau -> 0, sd(R)/L -> 0 and the probability snaps to 0 or 1.
   The model knows the outcome is nearly decided while spot is still miles away
   from the strike, which a naive spot-vs-strike comparison gets badly wrong.

The failure mode is basis risk: our spot feed is not the Chainlink TWAP stream.
That is a *systematic* error, not a statistical one, and no amount of model
sophistication removes it. Hence the `z_haircut` knob and the hard `max_price`
cap in the strategy layer -- you buy near-certainty cheaply, or not at all.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .settlement import SettlementIndex


def norm_cdf(z: float) -> float:
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


@dataclass(frozen=True)
class ProbView:
    p_up: float
    z: float            # signed sigma-cushion for Up: + means Up is locked
    cushion_price: float  # how far the running average is above/below P0, in $/BTC
    sd_price: float       # 1-sigma of the settlement index, in $/BTC
    remaining_s: float
    mean_index: float

    @property
    def p_down(self) -> float:
        return 1.0 - self.p_up


def _integral_var_factor(u: float) -> float:
    """f(u) = (e^u - 1 - u - u^2/2) / u^2, evaluated without cancellation.

    Written naively, the bracket is the difference of numbers of order u^-2 and
    its float64 error swamps the answer for realistic crypto volatilities
    (u ~ 1e-6), producing a variance of exactly zero -- and therefore a
    probability of exactly 1.0, which is how a model talks itself into a
    catastrophic bet. Use the series f(u) = u/3! + u^2/4! + u^3/5! + ... for
    small u instead.
    """
    if abs(u) < 1e-2:
        term = u / 6.0
        total = 0.0
        n = 3
        while n < 25:
            total += term
            n += 1
            term *= u / n
        return total
    return (math.exp(u) - 1.0 - u - 0.5 * u * u) / (u * u)


def remaining_integral_moments(spot: float, tau: float, sigma: float) -> tuple[float, float]:
    """(mean, variance) of the integral of GBM over the next `tau` seconds.

    sigma is per sqrt(second), drift assumed zero (see module docstring: a
    directional view on BTC over five minutes is not an edge, it is a guess).

    Var[R] = 2 * P^2 * tau^2 * f(sigma^2 * tau), which reduces to
    P^2 * sigma^2 * tau^3 / 3 as sigma -> 0 -- the integral-of-Brownian-motion
    result, and the term that dominates in practice.
    """
    if tau <= 0 or spot <= 0:
        return 0.0, 0.0
    s2 = sigma * sigma
    if s2 <= 0:
        return spot * tau, 0.0
    mean = spot * tau
    var = 2.0 * spot * spot * tau * tau * _integral_var_factor(s2 * tau)
    return mean, max(var, 0.0)


def fair_up(
    state: SettlementIndex,
    spot: float,
    sigma: float,
    now: float,
    drift_per_s: float = 0.0,
    z_cap: float = 6.0,
) -> ProbView:
    """Fair probability that the market resolves Up, given the state at `now`."""
    length = state.duration
    if length <= 0:
        return ProbView(0.5, 0.0, 0.0, 0.0, 0.0, spot)

    known = state.known_integral(now)
    tau = state.remaining_tau(now)
    mean_r, var_r = remaining_integral_moments(spot, tau, sigma)
    if drift_per_s:
        mean_r *= math.exp(drift_per_s * tau)

    sd_r = math.sqrt(var_r)
    threshold = state.open_price * length - known

    if sd_r <= 1e-12:
        p_up = 1.0 if mean_r >= threshold else 0.0
        z = z_cap if p_up > 0.5 else -z_cap
        cushion = (mean_r - threshold) / length
        return ProbView(p_up, z, cushion, 0.0, tau, (known + mean_r) / length)

    # Up wins iff the remaining integral clears `threshold`:
    #   p_up = P(R >= threshold) = Phi((mean - threshold) / sd)
    z = (mean_r - threshold) / sd_r
    z = max(-z_cap, min(z_cap, z))
    p_up = norm_cdf(z)

    # Keep probabilities away from hard 0/1: model error is real and a 1.0
    # probability would make Kelly sizing infinite.
    eps = 1e-4
    p_up = min(max(p_up, eps), 1.0 - eps)

    cushion = (mean_r - threshold) / length
    sd_price = sd_r / length
    return ProbView(p_up, z, cushion, sd_price, tau, (known + mean_r) / length)


def p_up_from_analytic(
    open_price: float,
    known_integral: float,
    spot: float,
    tau: float,
    length: float,
    sigma: float,
) -> float:
    """Lower-level entry point, handy for tests and sweeps."""
    mean_r, var_r = remaining_integral_moments(spot, tau, sigma)
    threshold = open_price * length - known_integral
    sd_r = math.sqrt(var_r)
    if sd_r <= 1e-12:
        return 1.0 if mean_r >= threshold else 0.0
    return norm_cdf((mean_r - threshold) / sd_r)
