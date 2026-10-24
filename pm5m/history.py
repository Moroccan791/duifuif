"""Historical data acquisition for the BTC 5-minute windows.

Three inputs, three different jobs (see DATA.md §7):

    strike_true  <- Gamma `eventMetadata.priceToBeat`   exact, but published late
    final_true   <- Gamma `eventMetadata.finalPrice`    exact, same reason
    spot_path    <- Binance 1s klines                   a proxy for research only

The Gamma fields arrive as strings nested in a JSON-encoded `eventMetadata`
blob, and `outcomePrices`/`clobTokenIds` are JSON-encoded strings rather than
arrays -- a documented trap that breaks naive parsing.

Everything is cached to `data/history/` so a run is repeatable and does not
re-hit the API. There is deliberately no retry storm: these endpoints are public
and rate-limited.
"""

from __future__ import annotations

import csv
import json
import os
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable

import httpx

GAMMA = "https://gamma-api.polymarket.com"
BINANCE_API = "https://api.binance.com/api/v3/klines"
BINANCE_VISION = "https://data.binance.vision/data/spot/daily/klines/BTCUSDT/1s"


@dataclass
class Window:
    """One 5-minute market, with everything needed to score it."""

    slug: str
    start_ts: float
    end_ts: float
    outcome: str                  # "up" | "down"
    strike: float | None = None   # Price To Beat, from Gamma (exact)
    final_price: float | None = None
    volume: float = 0.0
    # Populated later from our own data:
    spot_at_open: float | None = None
    strike_proxy: float | None = None
    book_up_at_open: float | None = None

    @property
    def resolved_up(self) -> bool:
        return self.outcome == "up"


def _json_field(raw: Any) -> Any:
    """Gamma returns several fields JSON-encoded as strings."""
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return raw
    return raw


def parse_gamma_market(m: dict[str, Any], event: dict[str, Any] | None = None) -> Window | None:
    """Extract one window from a Gamma market object, or None if not scoreable."""
    slug = m.get("slug") or (event or {}).get("slug") or ""
    if len(slug) < 3:
        return None
    try:
        start = float(slug.rsplit("-", 1)[1])
    except (IndexError, ValueError):
        return None

    prices = _json_field(m.get("outcomePrices"))
    outcome = None
    if isinstance(prices, list) and len(prices) == 2:
        try:
            up_p, dn_p = float(prices[0]), float(prices[1])
            if up_p > 0.99 and dn_p < 0.01:
                outcome = "up"
            elif dn_p > 0.99 and up_p < 0.01:
                outcome = "down"
        except (TypeError, ValueError):
            pass
    if outcome is None:
        return None

    meta = m.get("eventMetadata") or (event or {}).get("eventMetadata") or {}
    if isinstance(meta, str):
        meta = _json_field(meta) or {}

    def _num(key: str) -> float | None:
        v = meta.get(key) if isinstance(meta, dict) else None
        if isinstance(v, dict):
            v = v.get("value")
        try:
            return float(v) if v is not None else None
        except (TypeError, ValueError):
            return None

    try:
        volume = float(m.get("volumeNum") or m.get("volume") or 0.0)
    except (TypeError, ValueError):
        volume = 0.0

    return Window(
        slug=slug,
        start_ts=start,
        end_ts=start + 300.0,
        outcome=outcome,
        strike=_num("priceToBeat"),
        final_price=_num("finalPrice"),
        volume=volume,
    )


class Cache:
    def __init__(self, root: str = "data/history") -> None:
        self.root = root
        os.makedirs(root, exist_ok=True)

    def path(self, name: str) -> str:
        return os.path.join(self.root, name)

    def has(self, name: str) -> bool:
        return os.path.exists(self.path(name))

    def save_json(self, name: str, obj: Any) -> None:
        with open(self.path(name), "w", encoding="utf-8") as fh:
            json.dump(obj, fh)

    def load_json(self, name: str) -> Any:
        with open(self.path(name), encoding="utf-8") as fh:
            return json.load(fh)

    def save_windows(self, name: str, windows: Iterable[Window]) -> None:
        rows = [asdict(w) for w in windows]
        with open(self.path(name), "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)

    def load_windows(self, name: str) -> list[Window]:
        out = []
        with open(self.path(name), newline="", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                out.append(
                    Window(
                        slug=r["slug"],
                        start_ts=float(r["start_ts"]),
                        end_ts=float(r["end_ts"]),
                        outcome=r["outcome"],
                        strike=float(r["strike"]) if r.get("strike") else None,
                        final_price=float(r["final_price"]) if r.get("final_price") else None,
                        volume=float(r.get("volume") or 0),
                        spot_at_open=float(r["spot_at_open"]) if r.get("spot_at_open") else None,
                        strike_proxy=float(r["strike_proxy"]) if r.get("strike_proxy") else None,
                        book_up_at_open=(
                            float(r["book_up_at_open"]) if r.get("book_up_at_open") else None
                        ),
                    )
                )
        return out


# ---------------------------------------------------------------------------
# Gamma
# ---------------------------------------------------------------------------
def fetch_gamma_windows(
    limit: int = 500,
    offset_start: int = 0,
    max_pages: int = 40,
    sleep: float = 0.15,
    timeout: float = 20.0,
    series_slug: str = "btc-up-or-down-5m",
) -> list[Window]:
    """Page through closed 5-minute markets.

    Uses `/events` with `series_slug` + `closed=true`: the `/markets?active=true`
    listing silently omits these markets.
    """
    out: list[Window] = []
    seen: set[str] = set()
    with httpx.Client(timeout=timeout) as client:
        for page in range(max_pages):
            offset = offset_start + page * limit
            r = client.get(
                f"{GAMMA}/events",
                params={
                    "series_slug": series_slug,
                    "closed": "true",
                    "limit": limit,
                    "offset": offset,
                    "order": "endDate",
                    "ascending": "false",
                },
            )
            if r.status_code != 200:
                break
            batch = r.json()
            if not isinstance(batch, list) or not batch:
                break
            added = 0
            for ev in batch:
                for mk in ev.get("markets", []) or []:
                    if not str(mk.get("slug", "")).startswith("btc-updown-5m-"):
                        continue
                    w = parse_gamma_market(mk, ev)
                    if w is None or w.slug in seen:
                        continue
                    if not any(s in ev.get("slug", "") for s in ("updown-5m",)):
                        continue
                    seen.add(w.slug)
                    out.append(w)
                    added += 1
            if added == 0:
                break
            time.sleep(sleep)
    return out


# ---------------------------------------------------------------------------
# Binance klines (research proxy for spot)
# ---------------------------------------------------------------------------
def fetch_binance_1s(start_ms: int, end_ms: int, timeout: float = 20.0) -> list[tuple[int, float]]:
    """1-second klines. Public endpoint, capped at 1000 candles per call."""
    out: list[tuple[int, float]] = []
    cursor = start_ms
    with httpx.Client(timeout=timeout) as client:
        while cursor < end_ms:
            r = client.get(
                BINANCE_API,
                params={
                    "symbol": "BTCUSDT",
                    "interval": "1s",
                    "startTime": cursor,
                    "endTime": end_ms,
                    "limit": 1000,
                },
            )
            if r.status_code != 200:
                break
            batch = r.json()
            if not batch:
                break
            for k in batch:
                out.append((int(k[0]), float(k[4])))  # close price
            cursor = int(batch[-1][0]) + 1000
            time.sleep(0.05)
    return out


def twap_proxy(klines: list[tuple[int, float]], upto_ms: int, window_s: float = 60.0) -> float | None:
    """Our approximation of the Chainlink 60s TWAP ending at `upto_ms`.

    Plain mean of the 1s closes in the trailing window. This is NOT what
    Chainlink computes -- their weighting is unpublished and Polymarket's docs
    explicitly advise against reproducing it. That is exactly why
    `pm5m.phase2.proxy_error` measures the gap instead of assuming it is zero.
    """
    lo = upto_ms - int(window_s * 1000)
    vals = [p for t, p in klines if lo < t <= upto_ms and p > 0]
    if not vals:
        return None
    return sum(vals) / len(vals)


def attach_spot(windows: list[Window], klines: list[tuple[int, float]]) -> None:
    """Fill `spot_at_open` and `strike_proxy` for each window from klines."""
    for w in windows:
        open_ms = int(w.start_ts * 1000)
        spot = None
        for t, p in klines:
            if t >= open_ms:
                spot = p
                break
        w.spot_at_open = spot
        w.strike_proxy = twap_proxy(klines, open_ms)


# ---------------------------------------------------------------------------
# fixtures, for testing without a network
# ---------------------------------------------------------------------------
def synthetic_windows(
    n: int = 300,
    seed: int = 5,
    sigma: float = 0.03,
    edge_bps: float = 0.0,
    market_edge_bps: float = 0.0,
    start_ts: float = 1_790_000_000.0,
) -> list[Window]:
    """Build windows whose outcome follows the real TWAP60 rule.

    `edge_bps` biases the realised outcome away from the model's probability
    (a model that is wrong). `market_edge_bps` makes the BOOK quote below fair
    value by that many basis points (a mispriced market). They are different
    failures and the analysis must distinguish them.
    """
    import math
    import random

    from .twap import fair_p_up, settlement_sd

    rng = random.Random(seed)
    sig_s = sigma / math.sqrt(86400)
    out: list[Window] = []
    price = 100_000.0
    for i in range(n):
        start = start_ts + i * 300
        strike = price
        # The strike is a 60s average; approximate with a small offset so g != 0.
        g = rng.gauss(0.0, settlement_sd(price, sig_s))
        spot = strike + g
        p_up = fair_p_up(spot, strike, sig_s, 0.0)
        p_used = min(0.999, max(0.001, p_up + edge_bps / 10_000.0))
        up = rng.random() < p_used
        # The book quotes UP at fair value minus the injected mispricing.
        book = min(0.99, max(0.01, p_up - market_edge_bps / 10_000.0))
        w = Window(
            slug=f"btc-updown-5m-{int(start)}",
            start_ts=start,
            end_ts=start + 300,
            outcome="up" if up else "down",
            strike=strike,
            volume=rng.uniform(20, 300),
            spot_at_open=spot,
            strike_proxy=strike * math.exp(rng.gauss(0.0, 1.5e-5)),
            book_up_at_open=book,
        )
        out.append(w)
        price = spot * math.exp(rng.gauss(0.0, sigma * math.sqrt(300 / 86400)))
    return out
