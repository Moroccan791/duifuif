"""Window state: the strike at the open, the spot path since.

THE RULE (verified, not assumed)

    Up  iff  TWAP60(close) >= TWAP60(open)

Both ends are Chainlink's 60-second lookback TWAP. Ties resolve Up. Polymarket
used a 30s TWAP for 5m markets from 2026-08-07 and switched to 60s on
2026-08-14; anything before that is a different rule and is not comparable.

An earlier version of this module modelled settlement as the trapezoidal
average of spot over the whole 300s window. **That rule does not exist.** It is
deleted, not deprecated, in spirit: `known_integral` survives only because
callers still pass it around, and `pm5m.model.fair_up` ignores it.

WHAT THIS CLASS IS FOR NOW

`open_price` is the *strike*, not the previous print. It should be taken from
Gamma's `eventMetadata.priceToBeat`, which equals the previous market's
`finalPrice` exactly and is published 30-300s after a window closes. Inferring
it from a spot feed is the single largest source of error in the whole system,
which is what `pm5m.phase2.proxy_error` exists to measure.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .twap import TWAP_WINDOW_S


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

    def _price_at(self, ts: float) -> float:
        """Last observed price at or before `ts`, flat-extrapolated."""
        p = self.open_price
        for t, price in self._pts:
            if t > ts:
                break
            p = price
        return p

    def integral_between(self, lo: float, hi: float) -> float:
        """Area under the observed price path over [lo, hi], in price*seconds.

        Trapezoid between ticks, flat before the first and after the last. Only
        prices with timestamp <= `hi` contribute, so this cannot see the future.
        """
        if hi <= lo:
            return 0.0
        total = 0.0
        prev_t = lo
        prev_p = self._price_at(lo)
        for ts, price in self._pts:
            if ts <= lo:
                continue
            if ts >= hi:
                break
            total += (ts - prev_t) * (prev_p + price) / 2.0
            prev_t, prev_p = ts, price
        if hi > prev_t:
            total += (hi - prev_t) * prev_p
        return total

    def known_integral(self, now: float) -> float:
        """Deprecated: area from the window open to `now`.

        Kept for backward compatibility. Under the real settlement rule this
        quantity is meaningless -- settlement is a trailing 60s average, not an
        average over the whole window. Use `integral_between` instead.
        """
        return self.integral_between(self.start_ts, min(now, self.end_ts))

    def remaining_tau(self, now: float) -> float:
        return max(0.0, self.end_ts - now)

    def realized_average(self, now: float | None = None) -> float | None:
        """Average so far. Only meaningful once the window has closed."""
        end = self.end_ts if now is None else min(now, self.end_ts)
        span = end - self.start_ts
        if span <= 0:
            return None
        return self.known_integral(end) / span

    def twap_at(self, ts: float, window_s: float = 60.0) -> float | None:
        """Mean of the observed prices in the trailing `window_s` before `ts`."""
        pts = [(t, p) for t, p in self._pts if ts - window_s < t <= ts]
        if not pts:
            return None
        return sum(p for _, p in pts) / len(pts)

    def resolved_outcome(self) -> str:
        """Up iff TWAP60(close) >= strike. Ties go Up.

        This is the real rule. It is NOT a comparison of window averages: the
        close end is a trailing 60s mean, so a market that has been above the
        strike all window can still resolve Down if the last minute collapses.
        """
        close_twap = self.twap_at(self.end_ts, TWAP_WINDOW_S)
        if close_twap is None:
            raise ValueError("window has no data")
        return "up" if close_twap >= self.open_price else "down"
