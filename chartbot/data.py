"""Market data: candles from Binance, and a replay feed for offline work.

Binance is the venue because it has a real public API, deep liquidity, and no
strike to guess. Its klines are free and need no key, which is why the whole
read side works before you have an account.

The live order path is separate (`chartbot.broker`) and gated. Reading data and
placing orders are deliberately different capabilities.
"""

from __future__ import annotations

import math
import random
import time
from dataclasses import dataclass

import httpx

SPOT = "https://api.binance.com/api/v3"
TESTNET = "https://testnet.binance.vision/api/v3"

# Binance interval -> seconds
INTERVALS: dict[str, int] = {
    "1m": 60,
    "3m": 180,
    "5m": 300,
    "15m": 900,
    "30m": 1800,
    "1h": 3600,
    "4h": 14400,
    "1d": 86400,
}


@dataclass(frozen=True)
class Candle:
    ts: float          # open time, seconds
    open: float
    high: float
    low: float
    close: float
    volume: float

    @property
    def bullish(self) -> bool:
        return self.close >= self.open

    @property
    def range(self) -> float:
        return self.high - self.low

    @property
    def body(self) -> float:
        return abs(self.close - self.open)


def candle_from_binance(row: list) -> Candle:
    """Binance returns [openTime, o, h, l, c, vol, closeTime, ...] with strings."""
    return Candle(
        ts=int(row[0]) / 1000.0,
        open=float(row[1]),
        high=float(row[2]),
        low=float(row[3]),
        close=float(row[4]),
        volume=float(row[5]),
    )


class BinanceClient:
    """Public market data. No API key required, no order capability."""

    def __init__(self, base: str = SPOT, timeout: float = 15.0) -> None:
        self.base = base
        self.timeout = timeout

    def klines(self, symbol: str = "BTCUSDT", interval: str = "5m",
               limit: int = 200) -> list[Candle]:
        with httpx.Client(timeout=self.timeout) as c:
            r = c.get(f"{self.base}/klines",
                      params={"symbol": symbol, "interval": interval, "limit": limit})
            r.raise_for_status()
            return [candle_from_binance(row) for row in r.json()]

    def price(self, symbol: str = "BTCUSDT") -> float:
        with httpx.Client(timeout=self.timeout) as c:
            r = c.get(f"{self.base}/ticker/price", params={"symbol": symbol})
            r.raise_for_status()
            return float(r.json()["price"])

    def depth(self, symbol: str = "BTCUSDT", limit: int = 20) -> dict:
        with httpx.Client(timeout=self.timeout) as c:
            r = c.get(f"{self.base}/depth", params={"symbol": symbol, "limit": limit})
            r.raise_for_status()
            d = r.json()
            return {
                "bids": [(float(p), float(q)) for p, q in d["bids"]],
                "asks": [(float(p), float(q)) for p, q in d["asks"]],
            }


# ---------------------------------------------------------------------------
# replay / synthetic data
# ---------------------------------------------------------------------------
def synthetic_candles(
    n: int = 240,
    start: float = 1_760_000_000.0,
    interval_s: float = 300.0,
    price: float = 100_000.0,
    vol: float = 0.004,
    drift: float = 0.0,
    seed: int = 0,
) -> list[Candle]:
    """A believable OHLCV series: GBM path, intrabar wicks, volume clusters."""
    rng = random.Random(seed)
    out: list[Candle] = []
    p = price
    for i in range(n):
        o = p
        # Intrabar path of 8 steps so high/low are not just close prices.
        path = [o]
        for _ in range(8):
            path.append(path[-1] * math.exp(
                drift / 8.0 + vol / math.sqrt(8.0) * rng.gauss(0, 1)
            ))
        hi = max(path)
        lo = min(path)
        c = path[-1]
        v = abs(rng.gauss(1.0, 0.35)) * 1000.0
        out.append(Candle(
            ts=start + i * interval_s,
            open=o, high=hi, low=lo, close=c, volume=max(v, 1.0),
        ))
        p = c
    return out


def trending_candles(n: int = 200, up: bool = True, interval_s: float = 300.0,
                     seed: int = 1) -> list[Candle]:
    """A clean trend, for testing that the reader can actually see one."""
    return synthetic_candles(n=n, interval_s=interval_s, seed=seed,
                             vol=0.002, drift=0.0012 if up else -0.0012)


def live_replay(interval_s: float = 300.0, seed: int = 4):
    """Endless synthetic feed that advances one candle per call."""
    series = synthetic_candles(n=5000, interval_s=interval_s, seed=seed)
    i = 0

    def next_candles(limit: int = 200) -> list[Candle]:
        nonlocal i
        i += 1
        end = min(len(series), i + limit)
        return series[max(0, end - limit):end]

    return next_candles


def now() -> float:
    return time.time()
