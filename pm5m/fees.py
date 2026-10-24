"""Polymarket fee maths, verified against the published schedule (Oct 2026).

Taker fee for the crypto category:

    fee = shares * fee_rate * p * (1 - p)      with fee_rate = 0.07

Notes that matter for a 5-minute bot:

* The fee is *symmetric* around p = 0.50 and peaks there: 1.75c per share at
  50c, i.e. 3.5% of the amount staked. On a coin-flip this is the entire edge
  and then some.
* The fee is small at the extremes: at p = 0.98 it is 0.14c per share on 2c of
  upside -- roughly 7% of gross profit. This is why "buy the near-certain side"
  is the only fee-tolerant taker structure.
* Makers pay zero and receive a share of collected taker fees, which is why
  market making is structurally favoured here.
"""

from __future__ import annotations

DEFAULT_TAKER_RATE = 0.07


def taker_fee(shares: float, price: float, rate: float = DEFAULT_TAKER_RATE) -> float:
    """Absolute taker fee in USDC for a fill of `shares` at `price`."""
    if shares <= 0:
        return 0.0
    p = min(max(price, 0.0), 1.0)
    return shares * rate * p * (1.0 - p)


def taker_fee_per_share(price: float, rate: float = DEFAULT_TAKER_RATE) -> float:
    p = min(max(price, 0.0), 1.0)
    return rate * p * (1.0 - p)


def fee_as_pct_of_stake(price: float, rate: float = DEFAULT_TAKER_RATE) -> float:
    """Fee expressed as a fraction of the cash paid. Useful for sanity checks."""
    p = min(max(price, 0.0), 1.0)
    if p == 0:
        return 0.0
    return taker_fee_per_share(p, rate) / p


def net_edge_per_share(
    p_true: float, price: float, rate: float = DEFAULT_TAKER_RATE
) -> float:
    """Expected profit per share of a taker buy, in USDC, after fees.

    A contract bought at `price` settles at 1.0 with probability `p_true` and
    0.0 otherwise, so gross EV per share is `p_true - price`.
    """
    return p_true - price - taker_fee_per_share(price, rate)


def maker_rebate(
    shares: float, price: float, rate: float = DEFAULT_TAKER_RATE, share: float = 0.20
) -> float:
    """Indicative maker rebate credit, proportional to the fee that counterparty paid."""
    return taker_fee(shares, price, rate) * share


def round_trip_fee_drag(entry: float, exit_price: float, rate: float = DEFAULT_TAKER_RATE) -> float:
    """Total taker fee per share for entering at `entry` and exiting at `exit_price`."""
    return taker_fee_per_share(entry, rate) + taker_fee_per_share(exit_price, rate)
