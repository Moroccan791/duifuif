"""The real settlement rule, and the probability that follows from it.

THE RULE (verified: Polymarket docs, 13 Aug 2026 changelog, ~252k archived
windows):

    Up  iff  TWAP60(close) >= TWAP60(open)

where TWAP60(T) is the mean of the Chainlink BTC/USD stream over the 60 seconds
*ending at* T. Both ends are trailing averages; ties go Up.

This is NOT what `pm5m/settlement.py` models. That module assumes the average
runs over the whole 5-minute window, which produces the wrong fair value at every
point in the window. This module is the correction, and it is what the analysis
uses from here on.

WHAT IT MEANS

Let the window be [0, 300] seconds and the settlement averaging window be
[a, b] = [240, 300]. Then, writing W for the log-price path and s for the spot at
the current time t:

    L = (1/(b-a)) * integral_a^b W(u) du          <- what settles
    Up iff L >= B                                 <- B = strike (TWAP60 at open)

For a driftless Brownian motion, L is Gaussian. Its mean and variance
conditional on the current spot are all we need:

    E[L | W(t)=s] = s * Cov(L, W(t)) / t

    Cov(L, W(t)) = (1/(b-a)) * integral_a^b min(u, t) du
                 = t                            for t <= a
                 = [t^2 - a^2 + 2t(b-t)] / (2(b-a))   for a < t < b

    Var(L) = [b^3/3 - a^2 b + 2a^3/3] / (b-a)^2

    Var(L | W(t)=s) = Var(L) - Cov^2 / t

Three results fall out, and all three are intuitive once stated:

1. **At the open (t=0)** the conditional variance is just Var(L), so
   `SD = sigma * sqrt(260)` for a 300s window — about 0.165% of price on a 3%
   daily vol, i.e. ~$165 on a $100k BTC. E[L] is the current spot.
2. **Variance collapses as the window runs** (260 at t=0, 20 at t=240), because
   most of the future stops mattering. That is real convergence, but it is
   convergence over the *last 60 seconds*, not across the whole window.
3. **At the close it is still not zero** (Var 17 at t=300): knowing the price at
   the bell does not tell you the average over the preceding minute. This is why
   a final-second spike cannot decide the market — which was the entire point of
   the rule change.

The practical consequence for a bot: `g = spot - strike` at the open is a
~1.65-sigma-per-$165 signal. A $100 gap is x = 0.61, i.e. a 73% fair probability.
"""

from __future__ import annotations

import math

WINDOW_S = 300.0
TWAP_WINDOW_S = 60.0
# The settlement average spans the trailing 60s of the window.
A_S = WINDOW_S - TWAP_WINDOW_S  # 240
B_S = WINDOW_S  # 300


def norm_cdf(z: float) -> float:
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def var_of_average(a: float = A_S, b: float = B_S) -> float:
    """Var of (1/(b-a))*integral_a^b W du, in units of sigma^2 * seconds.

    For a=240, b=300 this is 260.0.
    """
    if b <= a:
        return 0.0
    return (b**3 / 3.0 - a * a * b + 2.0 * a**3 / 3.0) / ((b - a) ** 2)


def cov_with_spot(t: float, a: float = A_S, b: float = B_S) -> float:
    """Cov(L, W(t)) in sigma^2 * seconds."""
    if t <= 0 or b <= a:
        return 0.0
    if t <= a:
        return t
    if t >= b:
        return (b * b - a * a) / (2.0 * (b - a))
    return (t * t - a * a + 2.0 * t * (b - t)) / (2.0 * (b - a))


def conditional_moments(
    t: float, a: float = A_S, b: float = B_S
) -> tuple[float, float]:
    """(mean multiple of spot, variance in sigma^2*s) of L given only W(t).

    Path-BLIND version: it knows the current price but not the prices that have
    already printed inside the trailing window.

    The mean multiple is exactly **1.0**, not `cov/t`. W is a martingale, so
    E[W(u) | W(t)=s] = s for every u > t, and with driftless GBM the linear
    approximation E[L | W(t)=s] = s holds for the whole window. Writing
    `k = cov/t` (the first version of this function did) makes the mean shrink
    to 0.87*s at the close, which turns a $1 cushion in a decided market into a
    6-sigma DOWN call. That is a sign error that produces confident nonsense, so
    it is pinned by `tests/test_twap.py::test_mean_is_the_spot_at_every_time`.

    The variance is `var_of_average(t) - cov(t)^2 / t`, which is 260 at the open
    and 17 at the close. Note it does not reach zero: even knowing the final
    price, you do not know the average of the last 60s that produced it.

    If you have the tick history, use `conditional_moments_observed` instead --
    it is strictly better and drives `fair_p_up_observed`.
    """
    var_l = var_of_average(a, b)
    if t <= 0:
        return 1.0, var_l
    cov = cov_with_spot(t, a, b)
    var = max(var_l - cov * cov / t, 0.0)
    return 1.0, var


def conditional_moments_observed(
    t: float,
    observed_integral: float,
    spot: float,
    window_start_price: float,
    a: float = A_S,
    b: float = B_S,
) -> tuple[float, float]:
    """(mean of L, variance in sigma^2*s) using the path already observed.

    `observed_integral` is the area under the price path over
    `[window_start + a, window_start + t]`, in price*seconds -- i.e. the part of
    the trailing average that has already printed.

    Before a=240 the trailing window has not started, so the answer is just the
    spot with the full open variance. After that, the observed area is locked in
    and only `b - t` seconds remain unknown, so the variance falls as
    `(b-t)^3 / (3*(b-a)^2)` -- which reaches **zero at the bell**. That is
    correct and is why a decided market cannot be flipped by a last-second
    spike: by then the average has already been taken.

    "Zero variance at the close" applies to the observation of the *rule*, not
    to the state of the world; it is the honest answer only because the earlier
    part of the tail window is genuinely known.
    """
    span = b - a
    if span <= 0:
        return spot, 0.0
    t_eff = max(0.0, min(t, b))
    if t_eff <= a:
        # Nothing of the trailing window has happened yet.
        return spot, max(var_of_average(a, b) - t_eff, 0.0)
    remaining = b - t_eff
    mean = (observed_integral + remaining * spot) / span
    var = remaining**3 / (3.0 * span * span)
    return mean, var


def fair_p_up_observed(
    observed_integral: float,
    spot: float,
    strike: float,
    sigma: float,
    t: float = 0.0,
    z_cap: float = 6.0,
) -> float:
    """P(Up) using the tick path, not just the current price."""
    if spot <= 0 or sigma <= 0:
        return 1.0 if spot >= strike else 0.0
    mean, var = conditional_moments_observed(t, observed_integral, spot, strike)
    sd = spot * sigma * math.sqrt(var)
    if sd <= 0:
        return 1.0 if mean >= strike else 0.0
    z = max(-z_cap, min(z_cap, (mean - strike) / sd))
    return min(max(norm_cdf(z), 1e-4), 1.0 - 1e-4)


def settlement_sd(spot: float, sigma: float, t: float = 0.0) -> float:
    """1-sigma of the settlement value, in price units, at time t in the window."""
    _, var = conditional_moments(t)
    return spot * sigma * math.sqrt(var)


def fair_p_up(
    spot: float,
    strike: float,
    sigma: float,
    t: float = 0.0,
    z_cap: float = 6.0,
) -> float:
    """P(Up) = P(L >= strike) given the current spot at time t in the window."""
    if spot <= 0 or sigma <= 0:
        return 1.0 if spot >= strike else 0.0
    k, var = conditional_moments(t)
    mean = k * spot
    sd = spot * sigma * math.sqrt(var)
    if sd <= 0:
        return 1.0 if mean >= strike else 0.0
    z = (mean - strike) / sd
    z = max(-z_cap, min(z_cap, z))
    eps = 1e-4
    return min(max(norm_cdf(z), eps), 1.0 - eps)


def gap_in_sigmas(spot: float, strike: float, sigma: float, t: float = 0.0) -> float:
    """x = (spot - strike) adjusted for the conditional spread.

    The quantity plotted on the x-axis of the Phase 2 calibration curve.
    """
    _, var = conditional_moments(t)
    sd = spot * sigma * math.sqrt(var)
    if sd <= 0:
        return 0.0
    k, _ = conditional_moments(t)
    return (k * spot - strike) / sd


def implied_edge(spot: float, strike: float, sigma: float, market_price: float, t: float = 0.0) -> float:
    """Fair probability minus the market's price. Positive = buy Up."""
    return fair_p_up(spot, strike, sigma, t) - market_price
