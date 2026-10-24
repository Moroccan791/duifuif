"""Probability model for the 5-minute Up/Down settlement.

THE RULE, STATED CORRECTLY

Polymarket resolves these markets as

    Up  iff  TWAP60(close) >= TWAP60(open)        (ties go Up)

Both endpoints are Chainlink's 60-second *lookback* TWAP, not spot prints. Ties
resolve Up. See `pm5m.twap` for the derivation and the Monte Carlo check.

This replaced an earlier version of this module which modelled settlement as the
average of spot over the whole 300s window. **That rule does not exist.** It was
structurally wrong in two ways that both make the market look easier than it is:
it gave the window's early prices weight they do not have, and it made the
settlement converge gradually through the window rather than sharply in the last
minute. Every number this module produced on the old rule was fiction. Do not
reintroduce it.

WHAT THE CORRECT RULE BUYS YOU

Settlement is a *trailing 60s average anchored at each end*, so the object that
matters is not "is spot above the strike" but

    g = spot - strike

scaled by the standard deviation of the trailing average. That SD is
`Var(L) = 260 * sigma^2` at the open, collapsing to `17 * sigma^2` by the close:

  * at the open, a $100 gap on $100k is worth z = 0.61 -> 73%, so a book quoting
    50c there is wrong by a mile -- IF your strike is the real strike;
  * in the last 60s, `g` is largely frozen into the answer, which is why the
    last few seconds cannot flip a decided market.

The failure mode is basis risk, and it moved rather than vanished: we now need
the *right strike*. Gamma publishes it exactly, which is why the live path waits
for `priceToBeat` instead of inferring it. With an inferred strike the model is
guessing at its single most important input.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from . import twap
from .settlement import SettlementIndex


def norm_cdf(z: float) -> float:
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


@dataclass(frozen=True)
class ProbView:
    p_up: float
    z: float              # signed sigma-cushion for Up: + means Up is locked
    cushion_price: float  # spot minus strike, in $/BTC  (this is `g`)
    sd_price: float       # 1-sigma of the settlement average, in $/BTC
    remaining_s: float
    mean_index: float     # best estimate of TWAP60(close), in $/BTC

    @property
    def p_down(self) -> float:
        return 1.0 - self.p_up


def fair_up(
    state: SettlementIndex,
    spot: float,
    sigma: float,
    now: float,
    drift_per_s: float = 0.0,
    z_cap: float = 6.0,
) -> ProbView:
    """Fair probability that the market resolves Up, given the state at `now`.

    `sigma` is per-root-second. `state.open_price` is the strike: the 60s TWAP
    stamped at the window open, which is what Polymarket compares against.

    Settlement is

        L = TWAP60(close),   Up iff L >= strike

    and given the spot path up to `t`, L is normal with

        E[L | W(t)=s] = k(t) * s,      Var(L) = var(t) * sigma^2

    with (k, var) from `pm5m.twap.conditional_moments`. Both collapse as t
    approaches the close, which is the entire structure of the trade.
    """
    strike = state.open_price
    t = min(max(0.0, now - state.start_ts), state.duration)
    tau = max(0.0, state.end_ts - now)

    if spot <= 0 or sigma <= 0:
        p = 1.0 if spot >= strike else 0.0
        p = min(max(p, 1e-4), 1.0 - 1e-4)
        return ProbView(p, z_cap if p > 0.5 else -z_cap, spot - strike, 0.0, tau, spot)

    if t <= twap.A_S:
        # The trailing 60s window has not opened yet; only the spot matters.
        mean_l = spot
        var = max(twap.var_of_average() - t, 0.0)
    else:
        # Part of the trailing average has already printed: lock it in.
        lo = state.start_ts + twap.A_S
        hi = min(state.start_ts + t, state.end_ts)
        observed = state.integral_between(lo, hi)
        mean_l, var = twap.conditional_moments_observed(t, observed, spot, strike)

    if drift_per_s:
        mean_l *= math.exp(drift_per_s * t)

    sd = spot * sigma * math.sqrt(var)
    cushion = spot - strike

    if sd <= 1e-12:
        p_up = 1.0 if mean_l >= strike else 0.0
        z = z_cap if p_up > 0.5 else -z_cap
        return ProbView(p_up, z, cushion, 0.0, tau, mean_l)

    z = (mean_l - strike) / sd
    z = max(-z_cap, min(z_cap, z))
    p_up = min(max(norm_cdf(z), 1e-4), 1.0 - 1e-4)
    return ProbView(p_up, z, cushion, sd, tau, mean_l)


def p_up_from_analytic(
    open_price: float,
    known_integral: float,
    spot: float,
    tau: float,
    length: float,
    sigma: float,
) -> float:
    """Deprecated compatibility shim.

    `known_integral` is ignored: under the real rule settlement is not an
    integral of spot across the window. The signature is kept so older call
    sites and tests keep running; new code should call `twap.fair_p_up`.
    """
    t = max(0.0, length - tau)
    return twap.fair_p_up(spot, open_price, sigma, t)


def remaining_integral_moments(spot: float, tau: float, sigma: float) -> tuple[float, float]:
    """Deprecated. Superseded by `twap.conditional_moments`.

    Retained so imports do not break. These values describe the integral of GBM
    over `tau` seconds, which is *not* how these markets settle.
    """
    if tau <= 0 or spot <= 0:
        return 0.0, 0.0
    return spot * tau, max(spot * spot * sigma * sigma * tau ** 3 / 3.0, 0.0)


def _integral_var_factor(u: float) -> float:
    """Deprecated. Kept for import compatibility; see module docstring."""
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
