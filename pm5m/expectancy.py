"""Expectancy statistics: is the edge real, or did you get lucky?

This is the module that separates the 1% from the 97%, and it has nothing to do
with predicting prices. It answers one question rigorously: given the trades you
have actually taken, is your expected profit per trade distinguishable from
zero, or is it noise wearing a convincing costume?

Why this matters more than any strategy:

* A 60% win rate over 25 trades is indistinguishable from a coin flip. The
  standard error of a win rate at p=0.5, n=25 is ~10pp, so 60% is one sigma.
* Testing 20 strategy variants guarantees one will look profitable at p<0.05 by
  construction. That is not a discovery, it is arithmetic.
* Sizing on an unproven edge is how an account dies during the very trades that
  would have told you the edge was not there.

So: measure first, size with the *lower* confidence bound, and stop early only
on rules you wrote down before you started.

The statistics used here:

* expectancy = mean PnL per trade, with a t-based confidence interval
* one-sided t-test of H0: expectancy <= 0
* required sample size to establish the observed edge at a given power
* bootstrap CI, which does not assume normality (trades are fat-tailed and
  bounded below at -stake, so symmetry is a bad assumption)
* Bonferroni / Sidak correction for the number of strategies you tried
* Kelly sized on the conservative bound, not the point estimate
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# Student-t distribution via the incomplete beta function
# ---------------------------------------------------------------------------
def _betacf(a: float, b: float, x: float, itmax: int = 200, eps: float = 3e-12) -> float:
    """Continued fraction for the incomplete beta (Lentz's method)."""
    tiny = 1e-300
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < tiny:
        d = tiny
    d = 1.0 / d
    h = d
    for m in range(1, itmax):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < tiny:
            d = tiny
        c = 1.0 + aa / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < tiny:
            d = tiny
        c = 1.0 + aa / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < eps:
            break
    return h


def _betai(a: float, b: float, x: float) -> float:
    """Regularized incomplete beta I_x(a, b)."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    lbeta = math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
    front = math.exp(lbeta + a * math.log(x) + b * math.log(1.0 - x))
    if x < (a + 1.0) / (a + b + 2.0):
        return front * _betacf(a, b, x) / a
    return 1.0 - front * _betacf(b, a, 1.0 - x) / b


def t_cdf(t: float, df: float) -> float:
    """CDF of Student's t. Exact enough for any sample size we will see."""
    if df <= 0:
        return 0.5
    x = df / (df + t * t)
    ib = _betai(df / 2.0, 0.5, x)
    return 1.0 - 0.5 * ib if t > 0 else 0.5 * ib


def t_ppf(p: float, df: float) -> float:
    """Inverse t CDF by bisection. Only used for CI construction."""
    if not 0.0 < p < 1.0:
        raise ValueError("p must be in (0,1)")
    lo, hi = -1e3, 1e3
    for _ in range(200):
        mid = (lo + hi) / 2.0
        if t_cdf(mid, df) < p:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0


# ---------------------------------------------------------------------------
# trade records
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class TradeRecord:
    """One completed round trip. `pnl` is net of fees, in dollars."""

    ts: float
    slug: str
    side: str
    price: float
    shares: float
    fee: float
    won: bool
    pnl: float

    @property
    def stake(self) -> float:
        """Cash actually committed, fees included."""
        return self.price * self.shares + self.fee

    @property
    def return_on_stake(self) -> float:
        return self.pnl / self.stake if self.stake > 0 else 0.0


@dataclass
class Expectancy:
    n: int
    mean_pnl: float
    sd_pnl: float
    se_pnl: float
    t_stat: float
    p_value: float
    ci_low: float
    ci_high: float
    win_rate: float
    win_rate_ci: tuple[float, float]
    avg_win: float
    avg_loss: float
    profit_factor: float
    breakeven_win_rate: float
    avg_price: float
    mean_return: float
    total_pnl: float
    boot_ci: tuple[float, float] = (0.0, 0.0)
    required_n: int = 0
    half_split_delta: float = 0.0
    notes: list[str] = field(default_factory=list)

    @property
    def significant(self) -> bool:
        return self.p_value < 0.05 and self.mean_pnl > 0

    def verdict(self) -> str:
        if self.n < 30:
            need = (
                f"roughly {self.required_n}"
                if self.required_n < 10**8
                else "more than a hundred"
            )
            return (
                f"INCONCLUSIVE - only {self.n} trades. You need {need} "
                "before this means anything."
            )
        if self.significant:
            return (
                f"EDGE SUPPORTED - {self.mean_pnl:+.3f}/trade, "
                f"p={self.p_value:.4f}, 95% CI [{self.ci_low:+.3f}, {self.ci_high:+.3f}]"
            )
        if self.mean_pnl > 0:
            return (
                f"NOT DISTINGUISHABLE FROM LUCK - {self.mean_pnl:+.3f}/trade "
                f"but p={self.p_value:.3f}. Keep paper trading; do not size up."
            )
        return (
            f"NEGATIVE EXPECTANCY - {self.mean_pnl:+.3f}/trade, p={self.p_value:.3f}. "
            "Stop trading this and fix the strategy."
        )

    def pretty(self) -> str:
        lines = [
            f"trades                 : {self.n}",
            f"total pnl              : ${self.total_pnl:+,.2f}",
            f"expectancy / trade     : ${self.mean_pnl:+.3f}  (mean return {self.mean_return:+.2%})",
            f"std error of mean      : ${self.se_pnl:.3f}",
            f"t-statistic            : {self.t_stat:+.2f}",
            f"p-value (one-sided)    : {self.p_value:.4f}",
            f"95% CI for expectancy  : [${self.ci_low:+.3f}, ${self.ci_high:+.3f}]",
            f"bootstrap 95% CI       : [${self.boot_ci[0]:+.3f}, ${self.boot_ci[1]:+.3f}]",
            f"win rate               : {self.win_rate:.1%} "
            f"(95% CI {self.win_rate_ci[0]:.1%}-{self.win_rate_ci[1]:.1%})",
            f"break-even win rate    : {self.breakeven_win_rate:.1%}",
            f"avg win / avg loss     : ${self.avg_win:+.3f} / ${self.avg_loss:+.3f}",
            f"profit factor          : {self.profit_factor:.2f}",
            f"avg entry price        : {self.avg_price * 100:.1f}c",
            "trades needed for 80% power: "
            + (
                f"{self.required_n}"
                if self.required_n < 10**8
                else "unbounded (expectancy is not positive)"
            ),
            "",
            f"VERDICT: {self.verdict()}",
        ]
        if self.notes:
            lines.append("")
            for note in self.notes:
                lines.append(f"  ! {note}")
        return "\n".join(lines)


def _wilson(k: int, n: int, z: float = 1.959964) -> tuple[float, float]:
    """Wilson score interval for a proportion -- correct at small n, unlike
    the normal approximation, which is where '60% win rate on 20 trades'
    fantasies come from."""
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def required_sample_size(mean: float, sd: float, power: float = 0.8, alpha: float = 0.05) -> int:
    """Trades needed to detect `mean` at the given power. Infinity if mean<=0."""
    if mean <= 0 or sd <= 0:
        return 10**9
    z_alpha = 1.6449  # one-sided 5%
    z_beta = {0.8: 0.8416, 0.9: 1.2816, 0.95: 1.6449}.get(power, 0.8416)
    return int(math.ceil(((z_alpha + z_beta) * sd / mean) ** 2))


def analyze(
    trades: list[TradeRecord],
    n_boot: int = 2000,
    seed: int = 1,
    strategies_tried: int = 1,
) -> Expectancy:
    """Full expectancy analysis. Refuses to flatter a small sample."""
    n = len(trades)
    if n == 0:
        return Expectancy(
            0, 0, 0, 0, 0, 1.0, 0, 0, 0, (0, 0), 0, 0, 0, 0, 0, 0, 0, 0,
            notes=["no completed trades"],
        )

    pnls = [t.pnl for t in trades]
    mean = sum(pnls) / n
    var = sum((x - mean) ** 2 for x in pnls) / (n - 1) if n > 1 else 0.0
    sd = math.sqrt(var)
    se = sd / math.sqrt(n) if n else 0.0

    if se > 0:
        t_stat = mean / se
        p_value = 1.0 - t_cdf(t_stat, n - 1)
    else:
        t_stat = math.inf if mean > 0 else 0.0
        p_value = 0.0 if mean > 0 else 1.0

    if n > 1:
        crit = t_ppf(0.975, n - 1)
        ci = (mean - crit * se, mean + crit * se)
    else:
        ci = (mean, mean)

    wins = [t.pnl for t in trades if t.pnl > 0]
    losses = [t.pnl for t in trades if t.pnl <= 0]
    avg_win = sum(wins) / len(wins) if wins else 0.0
    avg_loss = sum(losses) / len(losses) if losses else 0.0
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    profit_factor = gross_win / gross_loss if gross_loss > 0 else math.inf

    avg_price = sum(t.price * t.shares for t in trades) / sum(t.shares for t in trades)
    breakeven = avg_price / (1.0 - avg_price) if 0 < avg_price < 1 else 0.0
    # Break-even win rate for a binary paying 1.0, given all-in cost c:
    #   p*c - (1-p)*c ... resolved more simply: you stake c per share and get 1.
    #   p*1 - c = 0  =>  p = c. So the break-even rate is the all-in cost.
    fees = sum(t.fee for t in trades)
    total_stake = sum(t.stake for t in trades)
    breakeven = (avg_price * total_stake + fees) / total_stake if total_stake else avg_price

    # Bootstrap: trades are fat-tailed and bounded, so do not trust symmetry.
    rng = random.Random(seed)
    boots = []
    for _ in range(n_boot):
        sample = [pnls[rng.randrange(n)] for _ in range(n)]
        boots.append(sum(sample) / n)
    boots.sort()
    boot_ci = (boots[int(0.025 * len(boots))], boots[int(0.975 * len(boots)) - 1])

    # Regime check: does the edge survive into the second half?
    half = n // 2
    first = sum(pnls[:half]) / half if half else 0.0
    second = sum(pnls[half:]) / (n - half) if n - half else 0.0

    notes: list[str] = []
    if strategies_tried > 1:
        corrected = min(1.0, p_value * strategies_tried)
        notes.append(
            f"{strategies_tried} variants tried -> Bonferroni-corrected p={corrected:.4f}. "
            "Half of a backtest's 'discoveries' are this artifact."
        )
        p_value = corrected
    if n < 30:
        notes.append(
            f"n={n} is too small to conclude anything. At this sample size the "
            "95% CI on your win rate spans roughly 30 percentage points."
        )
    if avg_price < 0.7:
        notes.append(
            f"avg entry {avg_price*100:.0f}c means a 1-loss-per-19-wins shape "
            "(or worse). A single losing streak can erase months."
        )
    if half and abs(second - first) > 2 * sd / math.sqrt(max(half, 1)):
        notes.append(
            f"edge changed between halves (${first:+.3f} -> ${second:+.3f}); "
            "regime shift or overfit. Investigate before sizing up."
        )

    return Expectancy(
        n=n,
        mean_pnl=mean,
        sd_pnl=sd,
        se_pnl=se,
        t_stat=t_stat,
        p_value=p_value,
        ci_low=ci[0],
        ci_high=ci[1],
        win_rate=len(wins) / n,
        win_rate_ci=_wilson(len(wins), n),
        avg_win=avg_win,
        avg_loss=avg_loss,
        profit_factor=profit_factor,
        breakeven_win_rate=breakeven,
        avg_price=avg_price,
        mean_return=sum(t.return_on_stake for t in trades) / n,
        total_pnl=sum(pnls),
        boot_ci=boot_ci,
        required_n=required_sample_size(mean, sd),
        half_split_delta=second - first,
        notes=notes,
    )


def conservative_kelly(exp: Expectancy, fraction: float = 0.25) -> float:
    """Kelly sized on the LOWER confidence bound, not the point estimate.

    This is the single most important sizing decision. If your true expectancy
    is at the bottom of the interval, betting as if it were the middle is how
    an account dies during the exact drawdown that proves the edge was smaller
    than you thought.

    Uses the binary-Kelly form with p from the lower bound of the win-rate CI
    and cost from the observed average all-in price.
    """
    c = exp.breakeven_win_rate
    if c <= 0 or c >= 1:
        return 0.0
    p_low = exp.win_rate_ci[0]
    f_star = (p_low - c) / (1.0 - c)
    if f_star <= 0:
        return 0.0
    return max(0.0, min(1.0, f_star * fraction))
