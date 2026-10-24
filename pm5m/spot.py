"""Spot price streams.

CRITICAL: none of these are the settlement source. Polymarket settles on
Chainlink's BTC/USD 60s TWAP. Every feed below is a *proxy*, and the difference
between a proxy and Chainlink is a systematic error that shows up as losing
trades even when the model is perfectly calibrated on the proxy. That is why the
framework tracks basis risk explicitly and refuses to buy above `max_price`.

Two feed shapes are supported because venues differ in latency:
  * trade stream  (binance btcusdt@trade)  - per-trade prints
  * ticker stream (coinbase ticker)        - quote updates

Whichever you pick, record it. A backtest against a feed you did not record is a
backtest of a different market.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Callable

FEEDS = {
    "binance": "wss://stream.binance.com:9443/ws/btcusdt@trade",
    "binance-futures": "wss://fstream.binance.com/ws/btcusdt@aggTrade",
    "coinbase": "wss://ws-feed.exchange.coinbase.com",
    "okx": "wss://ws.okx.com:8443/ws/v5/public",
    "bybit": "wss://stream.bybit.com/v5/public/linear",
}


def parse_binance(msg: dict) -> float | None:
    if "p" in msg:
        return float(msg["p"])
    return None


def parse_coinbase(msg: dict) -> float | None:
    if msg.get("type") == "ticker" and "price" in msg:
        return float(msg["price"])
    return None


def parse_okx(msg: dict) -> float | None:
    data = msg.get("data") or []
    if data and "last" in data[0]:
        return float(data[0]["last"])
    return None


def parse_bybit(msg: dict) -> float | None:
    data = msg.get("data") or {}
    if isinstance(data, dict) and "lastPrice" in data:
        return float(data["lastPrice"])
    return None


PARSERS = {
    "binance": parse_binance,
    "binance-futures": parse_binance,
    "coinbase": parse_coinbase,
    "okx": parse_okx,
    "bybit": parse_bybit,
}


def _subscribe_message(venue: str) -> str | None:
    if venue == "coinbase":
        return json.dumps(
            {"type": "subscribe", "product_ids": ["BTC-USD"], "channels": ["ticker"]}
        )
    if venue == "okx":
        return json.dumps(
            {"op": "subscribe", "args": [{"channel": "tickers", "instId": "BTC-USDT"}]}
        )
    if venue == "bybit":
        return json.dumps({"op": "subscribe", "args": ["tickers.BTCUSDT"]})
    return None


async def stream_spot(
    venue: str, on_tick: Callable[[float, float], None], on_raw=None
) -> None:
    """Connect to `venue` and call `on_tick(ts, price)` for each print."""
    import websockets

    if venue not in FEEDS:
        raise ValueError(f"unknown venue {venue!r}; choose from {sorted(FEEDS)}")
    url = FEEDS[venue]
    parser = PARSERS[venue]

    async with websockets.connect(url, ping_interval=20, ping_timeout=20) as ws:
        sub = _subscribe_message(venue)
        if sub:
            await ws.send(sub)
        while True:
            raw = await ws.recv()
            ts = time.time()
            if on_raw:
                on_raw(raw)
            try:
                msg = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                continue
            msgs = msg if isinstance(msg, list) else [msg]
            for m in msgs:
                if not isinstance(m, dict):
                    continue
                price = parser(m)
                if price and price > 0:
                    on_tick(ts, price)


@dataclass
class SyntheticSpot:
    """Deterministic GBM feed for tests and dry runs.

    Exists so the whole engine can be exercised end to end without a network,
    and so the model can be checked against a process whose true parameters are
    known. Never use it to justify a live trade.
    """

    spot0: float = 100_000.0
    sigma: float = 8.5e-5
    seed: int = 7
    t0: float | None = None
    _last: tuple[float, float] | None = None

    def __post_init__(self) -> None:
        if self.t0 is None:
            self.t0 = time.time()
        if self._last is None:
            self._last = (self.t0, self.spot0)
        import random

        self._rng = random.Random(self.seed)

    def step(self, ts: float) -> tuple[float, float]:
        """Advance the synthetic path to `ts` and return (ts, price)."""
        assert self._last is not None
        t_prev, p_prev = self._last
        dt = max(0.0, ts - t_prev)
        if dt <= 0:
            return self._last
        import math

        shock = self._rng.gauss(0.0, 1.0)
        p = p_prev * math.exp(
            -0.5 * self.sigma**2 * dt + self.sigma * math.sqrt(dt) * shock
        )
        self._last = (ts, p)
        return self._last
