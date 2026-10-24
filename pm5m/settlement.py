"""The settlement index: what actually determines Up vs Down.

Polymarket's 5-minute crypto markets resolve on the Chainlink BTC/USD 60-second
TWAP stream, averaged across the window, compared against the value of that
stream at the window's open. Ties resolve Up.

That detail is the whole game. It means the outcome is *not* a comparison of two
spot prices -- it is a comparison of a path average against a starting value. A
path average is a slow-moving object: after four minutes, the remaining one
minute can only drag it so far. This is the only structural feature of these
markets that a public-data bot can plausibly trade, and it is exactly what the
model in `pm5m.model` exploits.

Implementation choice (documented honestly): we approximate the settlement index
as the plain time-average of our own spot feed over the window, integrated with
the trapezoid rule. Averaging a 60s TWAP across 300s differs from averaging spot
across 300s only through a mild edge weighting; the difference is second order
compared to the vol of the path, but it is NOT zero, and it is not symmetric with
whatever feed you happen to use. `scripts/validate.py` measures the residual
model error against real resolutions. Do not skip that step.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class SettlementIndex:
    """Trapezoidal integral of spot over [start_ts, end_ts], built tick by tick.

    `known_integral` only ever uses prices with timestamp <= the moment of the
    query, so replaying recorded data cannot peek at the future.
    """

    start_ts: float
    end_ts: float
    open_price: float
    _pts: list[tuple[float, float]] = field(default_factory=list)

    @property
    def duration(self) -> float:
        return self.end_ts - self.start_ts

    def add(self, ts: float, price: float) -> None:
        if price <= 0:
            return
        if ts < self.start_ts:
            return
        if self._pts and ts <= self._pts[-1][0]:
            return
        self._pts.append((min(ts, self.end_ts), price))

    def known_integral(self, now: float) -> float:
        """Integral of the price path from start up to `now` (clamped to window)."""
        if not self._pts:
            return 0.0
        cutoff = min(now, self.end_ts)
        if cutoff <= self.start_ts:
            return 0.0

        total = 0.0
        prev_t = self.start_ts
        prev_p = self.open_price
        for ts, price in self._pts:
            if ts <= self.start_ts:
                prev_p = price
                continue
            if ts >= cutoff:
                break
            total += (ts - prev_t) * (prev_p + price) / 2.0
            prev_t, prev_p = ts, price
        # Extrapolate flat from the last known price to the cutoff.
        if cutoff > prev_t:
            total += (cutoff - prev_t) * prev_p
        return total

    def remaining_tau(self, now: float) -> float:
        return max(0.0, self.end_ts - now)

    def realized_average(self, now: float | None = None) -> float | None:
        """Average so far. Only meaningful once the window has closed."""
        end = self.end_ts if now is None else min(now, self.end_ts)
        span = end - self.start_ts
        if span <= 0:
            return None
        return self.known_integral(end) / span

    def resolved_outcome(self) -> str:
        """Up if the window average >= open price, else Down. Ties go Up."""
        avg = self.realized_average(self.end_ts)
        if avg is None:
            raise ValueError("window has no data")
        return "up" if avg >= self.open_price else "down"
