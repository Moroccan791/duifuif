"""Phase 2: the decisive test.

Two questions, in order, and the second is only worth asking if the first passes.

**2a — Is our proxy good enough?**
Gamma publishes the true strike. We can compute a proxy from Binance 1s klines.
Measure `|proxy − truth|` in basis points and compare it to `sigma_settle`, which
is ~16.5bp. If the proxy error is a large fraction of that, every downstream
number is noise and the analysis must stop.

**2b — Is the book wrong?**
For each window, at the open:

    x = (spot - strike) / sigma_settle        <- what the rule implies
    p_fair = Phi(x)                            <- our probability
    p_market = book price for UP at the open   <- the crowd's probability

Bucket by `x`, and compare the two curves. The realised up-rate must track
`p_fair` for the model to be valid at all. Then the gap between `p_fair` and
`p_market`, minus the fee, is the edge in cents per share.

This module deliberately reports the calibration table even when there is no
edge, because a model that cannot predict its own settlement is not a model.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from .expectancy import _wilson
from .twap import fair_p_up, gap_in_sigmas, settlement_sd, TWAP_WINDOW_S

# Mean absolute proxy error, in basis points of price, at which the analysis
# stops being meaningful. sigma_settle is ~16.5bp, so 2bp is ~12% of the signal.
PROXY_ERROR_LIMIT_BP = 2.0


@dataclass
class ProxyCheck:
    n: int
    error_bp_mean: float
    error_bp_p95: float
    error_bp_max: float
    sigma_settle_bp: float

    @property
    def ratio(self) -> float:
        return self.error_bp_mean / self.sigma_settle_bp if self.sigma_settle_bp else math.inf

    @property
    def usable(self) -> bool:
        return self.error_bp_mean <= PROXY_ERROR_LIMIT_BP

    def pretty(self) -> str:
        verdict = (
            "USABLE - proxy error is small relative to the signal"
            if self.usable
            else "NOT USABLE - proxy error is too large; use Chainlink history"
        )
        return "\n".join(
            [
                f"windows with both strike and proxy : {self.n}",
                f"proxy error, mean                 : {self.error_bp_mean:.2f} bp",
                f"proxy error, p95                  : {self.error_bp_p95:.2f} bp",
                f"proxy error, max                  : {self.error_bp_max:.2f} bp",
                f"sigma_settle (the signal)         : {self.sigma_settle_bp:.2f} bp",
                f"error / signal ratio              : {self.ratio:.2%}",
                f"VERDICT: {verdict}",
            ]
        )


def proxy_error(windows, sigma: float = 0.03) -> ProxyCheck:
    """Compare our 60s Binance mean against Gamma's published strike."""
    errs = []
    for w in windows:
        if not w.strike or not w.strike_proxy:
            continue
        errs.append(abs(w.strike_proxy / w.strike - 1.0) * 10_000.0)
    if not errs:
        return ProxyCheck(0, math.inf, math.inf, math.inf, 16.5)
    errs.sort()
    n = len(errs)
    sig_s = sigma / math.sqrt(86400)
    sigma_bp = sig_s * math.sqrt(260.0) * 10_000.0
    return ProxyCheck(
        n=n,
        error_bp_mean=sum(errs) / n,
        error_bp_p95=errs[min(n - 1, int(0.95 * n))],
        error_bp_max=errs[-1],
        sigma_settle_bp=sigma_bp,
    )


@dataclass
class Bucket:
    lo: float
    hi: float
    n: int
    wins: int
    mean_fair: float
    mean_market: float
    mean_price: float

    @property
    def realised(self) -> float:
        return self.wins / self.n if self.n else 0.0

    @property
    def ci(self) -> tuple[float, float]:
        return _wilson(self.wins, self.n)

    @property
    def model_error(self) -> float:
        return self.realised - self.mean_fair

    @property
    def edge_vs_market(self) -> float:
        """Realised minus the market's price: the money on the table."""
        if self.mean_market is None:
            return 0.0
        return self.realised - self.mean_market

    def fee(self, fee_rate: float = 0.07) -> float:
        if not self.mean_market:
            return 0.0
        p = self.mean_market
        return fee_rate * p * (1.0 - p)

    def is_actionable(self, fee_rate: float = 0.07, min_n: int = 200) -> bool:
        """An edge only counts if the LOWER confidence bound clears the market.

        A point estimate above the market price is not an edge, it is a number.
        The first version of this used the point estimate and duly reported
        "EDGE CANDIDATE" on 63 windows of pure noise -- the exact overfitting
        trap the whole project is meant to avoid. Same discipline as
        `expectancy`: the one-sided lower bound must clear the bar.
        """
        if self.mean_market is None or self.n < min_n:
            return False
        return self.ci[0] > self.mean_market + self.fee(fee_rate)

    def needed_n(self, fee_rate: float = 0.07, power_z: float = 0.84, alpha_z: float = 1.645) -> int:
        """Windows required to prove the observed gap, at 80% power."""
        gap = self.edge_vs_market - self.fee(fee_rate)
        if gap <= 0:
            return 10**9
        p = max(min(self.realised, 0.999), 0.001)
        se_unit = math.sqrt(p * (1 - p))
        return int(math.ceil(((alpha_z + power_z) * se_unit / gap) ** 2))

    def pretty(self) -> str:
        ci = self.ci
        mkt = f"{self.mean_market*100:6.1f}c" if self.mean_market else "   —  "
        return (
            f"{self.lo:+5.2f}..{self.hi:+5.2f} {self.n:6d} "
            f"{self.realised*100:7.1f}% [{ci[0]*100:5.1f},{ci[1]*100:5.1f}] "
            f"{self.mean_fair*100:7.1f}% {self.model_error*100:+7.1f}pp "
            f"{mkt} {self.edge_vs_market*100:+8.1f}pp"
        )


HEADER = (
    f"{'x bucket':>12} {'n':>6} {'realised':>8} {'95% CI':>12} "
    f"{'model':>8} {'err':>8} {'mkt':>7} {'edge':>9}"
)


def calibration(
    windows,
    sigma: float = 0.03,
    boundaries: tuple[float, ...] = (-9, -2, -1, -0.5, 0, 0.5, 1, 2, 9),
) -> list[Bucket]:
    """Bucket windows by x = gap in sigmas and compare truth, model, and market."""
    buckets: list[list] = [[] for _ in range(len(boundaries) - 1)]
    for w in windows:
        if not w.strike or not w.spot_at_open:
            continue
        x = gap_in_sigmas(w.spot_at_open, w.strike, sigma / math.sqrt(86400), 0.0)
        for i in range(len(boundaries) - 1):
            if boundaries[i] <= x < boundaries[i + 1]:
                buckets[i].append((w, x))
                break

    out: list[Bucket] = []
    for i, group in enumerate(buckets):
        if not group:
            continue
        fairs = [fair_p_up(w.spot_at_open, w.strike, sigma / math.sqrt(86400), 0.0) for w, _ in group]
        mkts = [w.book_up_at_open for w, _ in group if w.book_up_at_open]
        out.append(
            Bucket(
                lo=boundaries[i],
                hi=boundaries[i + 1],
                n=len(group),
                wins=sum(1 for w, _ in group if w.resolved_up),
                mean_fair=sum(fairs) / len(fairs),
                mean_market=(sum(mkts) / len(mkts)) if mkts else None,
                mean_price=sum(w.strike for w, _ in group) / len(group),
            )
        )
    return out


@dataclass
class Phase2Report:
    n_windows: int
    n_scored: int
    up_rate: float
    proxy: ProxyCheck
    buckets: list[Bucket]
    fee_rate: float = 0.07
    notes: list[str] = field(default_factory=list)

    def actionable(self, min_n: int = 200) -> list[Bucket]:
        """Buckets where the edge is statistically established, not merely seen."""
        return [b for b in self.buckets if b.is_actionable(self.fee_rate, min_n)]

    def candidates(self, min_n: int = 30) -> list[Bucket]:
        """Buckets showing a positive point estimate, significant or not."""
        out = []
        for b in self.buckets:
            if b.mean_market is None or b.n < min_n:
                continue
            if b.edge_vs_market > b.fee(self.fee_rate):
                out.append(b)
        return out

    def verdict(self) -> str:
        if not self.proxy.usable:
            return (
                "PHASE 2 BLOCKED - the Binance proxy is too error-prone to test H1. "
                "Re-run against Chainlink Data Streams history."
            )
        act = self.actionable()
        if not act:
            cand = self.candidates()
            if cand:
                best = max(cand, key=lambda b: b.edge_vs_market)
                need = best.needed_n(self.fee_rate)
                need_s = f"~{need:,}" if need < 10**8 else "an unbounded"
                return (
                    f"NOT PROVEN - the best bucket shows "
                    f"{best.edge_vs_market*100:+.1f}pp over the market on {best.n} "
                    f"windows, but its 95% lower bound ({best.ci[0]*100:.1f}%) does "
                    f"not clear the price+fee ({best.mean_market*100 + best.fee(self.fee_rate)*100:.1f}%). "
                    f"You would need {need_s} windows in that bucket to prove it."
                )
            return (
                "NO EDGE - realised outcomes do not beat the market's price after "
                "fees in any bucket. Per the plan, stop here."
            )
        best = max(act, key=lambda b: b.edge_vs_market)
        return (
            f"EDGE CANDIDATE - bucket x in [{best.lo:+.2f},{best.hi:+.2f}] shows "
            f"{best.edge_vs_market*100:+.1f}pp over the market on {best.n} windows. "
            "Validate out of sample before believing it."
        )

    def pretty(self) -> str:
        lines = [
            f"windows total          : {self.n_windows}",
            f"windows scoreable      : {self.n_scored}",
            f"realised up rate       : {self.up_rate:.1%}",
            "",
            self.proxy.pretty(),
            "",
            HEADER,
            "-" * len(HEADER),
        ]
        lines += [b.pretty() for b in self.buckets]
        act = self.actionable()
        if act:
            lines.append("")
            lines.append("  statistically established (lower CI bound clears price+fee):")
            for b in act:
                lines.append(
                    f"    x in [{b.lo:+.2f},{b.hi:+.2f}]: "
                    f"{b.edge_vs_market*100:+.1f}pp on {b.n} windows"
                )
        if self.notes:
            lines.append("")
            lines += [f"  ! {n}" for n in self.notes]
        lines += ["", f"VERDICT: {self.verdict()}"]
        return "\n".join(lines)


def analyse(windows, sigma: float = 0.03, fee_rate: float = 0.07) -> Phase2Report:
    scored = [w for w in windows if w.strike and w.spot_at_open]
    proxy = proxy_error(windows, sigma)
    buckets = calibration(scored, sigma)

    notes: list[str] = []
    if len(scored) < 500:
        notes.append(
            f"only {len(scored)} scoreable windows; a 5pp gap needs ~620 per bucket"
        )
    if all(b.mean_market is None for b in buckets):
        notes.append(
            "no book prices attached - this run can only validate the model, not "
            "measure the edge. Join the order book data before concluding anything."
        )
    if any(b.n < 30 for b in buckets):
        notes.append("some buckets have <30 windows; their CIs are very wide")

    up = sum(1 for w in scored if w.resolved_up) / len(scored) if scored else 0.0
    return Phase2Report(
        n_windows=len(windows),
        n_scored=len(scored),
        up_rate=up,
        proxy=proxy,
        buckets=buckets,
        fee_rate=fee_rate,
        notes=notes,
    )
