"""Live Polymarket scanning: turn open sports markets into priced legs.

Run this on a machine with normal internet access. The Arena sandbox cannot
reach polymarket.com (verified: HTTP 000 to polymarket.com, gamma-api and
clob.polymarket.com), so nothing here is exercised live in CI -- the parsing is
tested against recorded JSON instead.

Two endpoints are used:

    gamma-api.polymarket.com/markets   -> market metadata, outcomes, best bid/ask
    clob.polymarket.com/prices-history -> historical price series

Usage from the CLI:

    python -m sports scan --sport tennis

Design notes that matter for correctness:

* A two-way Polymarket market quotes two legs that routinely sum to **101c**,
  not 100c. That 1c is structural cost, not an edge. Never infer one leg's
  price as ``1 - other``: read both sides independently.
* ``bestBid`` is what you can sell at; ``bestAsk`` is what you can buy at. We
  buy, so we use the ask. Using the mid manufactures a half-spread of free
  money that does not exist.
* Prices return as strings in ``outcomePrices``; treat every field as untrusted.
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request

GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
TIMEOUT = 20


def _get(url: str) -> object:
    req = urllib.request.Request(url, headers={"User-Agent": "sports-predictor/1.0"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return json.loads(r.read().decode())


def _num(x, default=None):
    try:
        if x is None or x == "":
            return default
        return float(x)
    except (TypeError, ValueError):
        return default


def _json_list(x):
    if isinstance(x, list):
        return x
    if isinstance(x, str):
        try:
            v = json.loads(x)
            return v if isinstance(v, list) else []
        except json.JSONDecodeError:
            return []
    return []


def fetch_markets(closed: bool = False, limit: int = 100, tag_slug: str | None = None,
                  offset: int = 0) -> list[dict]:
    params = {"closed": str(closed).lower(), "limit": limit, "offset": offset}
    if tag_slug:
        params["tag_slug"] = tag_slug
    return _get(f"{GAMMA}/markets?{urllib.parse.urlencode(params)}")  # type: ignore[return-value]


def fetch_price_history(token_id: str, interval: str = "max", fidelity: int = 60) -> list[dict]:
    q = urllib.parse.urlencode({"market": token_id, "interval": interval, "fidelity": fidelity})
    data = _get(f"{CLOB}/prices-history?{q}")
    return data.get("history", []) if isinstance(data, dict) else []


def legs_from_market(market: dict) -> list[dict]:
    """Both sides of a two-way market as buyable legs, using the ask price."""
    outcomes = _json_list(market.get("outcomes"))
    prices = _json_list(market.get("outcomePrices"))
    ask = _num(market.get("bestAsk"))
    bid = _num(market.get("bestBid"))
    out = []
    for i, name in enumerate(outcomes):
        p = _num(prices[i]) if i < len(prices) else None
        # bestAsk is the market-level ask for the YES side; per-outcome prices
        # fall back to outcomePrices when the book fields are absent
        price = ask if (i == 0 and ask is not None) else p
        if price is None:
            continue
        out.append({
            "event": market.get("question", market.get("slug", "?")),
            "name": str(name),
            "price": price,
            "bid": bid if i == 0 else (None if bid is None else 1 - bid),
            "slug": market.get("slug", ""),
            "volume": _num(market.get("volumeNum"), _num(market.get("volume"), 0.0)),
            "market_id": market.get("id"),
        })
    return out


def sports_markets(limit: int = 200, offset: int = 0) -> list[dict]:
    """Open markets tagged sports. Filtrate further in the caller."""
    return fetch_markets(closed=False, limit=limit, tag_slug="sports", offset=offset)


def summarise(legs: list[dict], lo: float = 0.50, hi: float = 0.65) -> dict:
    """How many legs sit in the band, and what the pair sums look like."""
    inband = [l for l in legs if l["price"] is not None and lo <= l["price"] <= hi]
    return {
        "legs": len(legs),
        "in_band": len(inband),
        "avg_pair_sum": (sum(l["price"] for l in legs) / len(legs) * 2) if legs else None,
    }
