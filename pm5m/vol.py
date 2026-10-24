"""Rolling volatility estimation from a spot tick buffer.

Short-horizon crypto vol is autocorrelated and regime-dependent, so a bot that
hard-codes one number will be wrong most of the time. We estimate the variance
rate from recent ticks and clamp it into a sane band: a bad estimate must never
be allowed to produce a 50-sigma trade.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence

MIN_SIGMA = 1e-6   # per sqrt(second)
MAX_SIGMA = 3e-3   # per sqrt(second), ~ 2.8% per minute -- extreme but possible


def realized_sigma(
    samples: Sequence[tuple[float, float]],
    lookback_s: float = 1800.0,
    fallback: float = 8.5e-5,
) -> float:
    """Estimate sigma in units of sqrt(seconds) from (ts, price) samples.

    Uses the quadratic-variation estimator sum(r^2) / sum(dt), which is robust to
    missing ticks and does not require a drift estimate.
    """
    if not samples or len(samples) < 3:
        return fallback
    now = samples[-1][0]
    window = [(t, p) for t, p in samples if t >= now - lookback_s and p > 0]
    if len(window) < 3:
        return fallback

    var_sum = 0.0
    dt_sum = 0.0
    for (t0, p0), (t1, p1) in zip(window, window[1:]):
        dt = t1 - t0
        if dt <= 0 or p0 <= 0 or p1 <= 0:
            continue
        r = math.log(p1 / p0)
        var_sum += r * r
        dt_sum += dt

    if dt_sum <= 0 or var_sum <= 0:
        return fallback
    sigma = math.sqrt(var_sum / dt_sum)
    return min(max(sigma, MIN_SIGMA), MAX_SIGMA)


@dataclass
class SpotBuffer:
    """Bounded ring of (ts, price) with cheap window queries."""

    capacity: int = 20_000
    _samples: list[tuple[float, float]] | None = None

    def __post_init__(self) -> None:
        if self._samples is None:
            self._samples = []

    def add(self, ts: float, price: float) -> None:
        assert self._samples is not None
        if price <= 0:
            return
        if self._samples and ts < self._samples[-1][0]:
            # Out-of-order tick: keep the series monotonic, drop the straggler.
            return
        self._samples.append((ts, price))
        if len(self._samples) > self.capacity:
            del self._samples[: len(self._samples) - self.capacity]

    @property
    def samples(self) -> list[tuple[float, float]]:
        assert self._samples is not None
        return self._samples

    @property
    def last(self) -> tuple[float, float] | None:
        return self.samples[-1] if self.samples else None

    def price_at(self, ts: float) -> float | None:
        """Latest price at or before `ts` (no lookahead)."""
        lo, hi = 0, len(self.samples)
        while lo < hi:
            mid = (lo + hi) // 2
            if self.samples[mid][0] <= ts:
                lo = mid + 1
            else:
                hi = mid
        return self.samples[lo - 1][1] if lo > 0 else None
