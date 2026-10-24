"""The arithmetic of a monthly income target.

This module exists because "make a ton of money a month" is not a strategy
requirement, it is an arithmetic constraint -- and almost nobody runs the
numbers before they start. Running them first costs nothing and saves accounts.

The three facts it makes unavoidable:

1. Monthly income targets imply annual returns that are not merely optimistic,
   they are outside the range of every institution that has ever traded.
2. Return and capital are interchangeable. The same income is available from a
   modest return on large capital, or an impossible return on small capital.
   The bot does not create the money; the capital does.
3. Hard return targets force position sizes that destroy the account with
   near-certainty, because the size needed to hit the target is also the size
   that makes one bad month terminal.

Every number here is a distribution, not a promise. Nothing in this file knows
anything about your edge -- that is the point.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

# Annualised return benchmarks, for scale. These are *good* numbers.
BENCHMARKS: dict[str, float] = {
    "US 10y treasury": 0.043,
    "S&P 500 long-run average": 0.10,
    "S&P 500, best 10y stretch": 0.18,
    "top-decile hedge fund": 0.20,
    "Renaissance Medallion (best ever, closed)": 0.66,
}


# ---------------------------------------------------------------------------
# pure arithmetic
# ---------------------------------------------------------------------------
def required_monthly_return(capital: float, monthly_target: float) -> float:
    """Fraction of capital per month needed to produce `monthly_target`."""
    if capital <= 0:
        return math.inf
    return monthly_target / capital


def annualize_monthly(monthly: float) -> float:
    """Compound a monthly return into an annual one."""
    if monthly <= -1.0:
        return -1.0
    return (1.0 + monthly) ** 12 - 1.0


def monthly_from_annual(annual: float) -> float:
    if annual <= -1.0:
        return -1.0
    return (1.0 + annual) ** (1.0 / 12.0) - 1.0


def required_capital(monthly_target: float, annual_return: float) -> float:
    """Capital needed to draw `monthly_target` while compounding at `annual_return`.

    This is the number that actually matters, and it is usually the one that
    ends the conversation: income is a function of capital, not of cleverness.
    """
    if annual_return <= 0:
        return math.inf
    m = monthly_from_annual(annual_return)
    if m <= 0:
        return math.inf
    return monthly_target / m


def years_to_double(annual_return: float) -> float:
    """Rule of 72, exactly (returns YEARS -- a previous version labelled this
    in months, which made every strategy look twelve times better)."""
    if annual_return <= 0:
        return math.inf
    return math.log(2.0) / math.log(1.0 + annual_return)


def leverage_needed(capital: float, monthly_target: float, base_annual_return: float) -> float:
    """How much leverage a target demands, given a realistic base return."""
    base_monthly = monthly_from_annual(base_annual_return)
    if base_monthly <= 0:
        return math.inf
    return required_monthly_return(capital, monthly_target) / base_monthly


# ---------------------------------------------------------------------------
# distribution of outcomes
# ---------------------------------------------------------------------------
@dataclass
class EquityPath:
    months: list[float] = field(default_factory=list)

    @property
    def final(self) -> float:
        return self.months[-1] if self.months else 0.0

    @property
    def max_drawdown(self) -> float:
        peak = -math.inf
        worst = 0.0
        for v in self.months:
            peak = max(peak, v)
            if peak > 0:
                worst = min(worst, v / peak - 1.0)
        return worst


@dataclass
class Outcome:
    n_paths: int
    median_final: float
    p10_final: float
    p90_final: float
    p_profit: float
    p_hit_target: float
    p_drawdown_50: float
    p_ruin: float
    median_max_dd: float

    def pretty(self, capital: float, target: float) -> str:
        lines = [
            f"paths simulated        : {self.n_paths:,}",
            f"median outcome         : ${self.median_final:,.0f} "
            f"({self.median_final / capital - 1:+.1%} on capital)",
            f"10th percentile        : ${self.p10_final:,.0f}",
            f"90th percentile        : ${self.p90_final:,.0f}",
            f"probability of profit  : {self.p_profit:.1%}",
            f"probability of hitting ${target:,.0f}/mo target: {self.p_hit_target:.1%}",
            f"probability of -50% DD : {self.p_drawdown_50:.1%}",
            f"probability of ruin    : {self.p_ruin:.1%}",
            f"median worst drawdown  : {self.median_max_dd:.1%}",
        ]
        return "\n".join(lines)


def simulate_outcomes(
    capital: float,
    annual_return: float,
    annual_vol: float,
    months: int = 12,
    monthly_target: float = 0.0,
    n_paths: int = 20_000,
    fat_tails: float = 4.0,
    seed: int = 1,
) -> Outcome:
    """Monte Carlo of monthly returns with fat tails.

    `fat_tails` is the Student-t degrees of freedom. 4 is realistic for crypto
    and equity strategies: it produces the occasional catastrophic month that a
    normal distribution pretends cannot happen. Using a normal distribution here
    is the single most common way a risk model lies.

    Ruin is defined as losing 90% of capital, which is where a leveraged account
    is effectively finished.
    """
    rng = random.Random(seed)
    mu_m = monthly_from_annual(annual_return)
    sd_m = annual_vol / math.sqrt(12.0)

    finals: list[float] = []
    max_dds: list[float] = []
    hits = 0
    dds50 = 0
    ruins = 0

    for _ in range(n_paths):
        equity = capital
        peak = capital
        worst_dd = 0.0
        hit = False
        for _m in range(months):
            # Student-t shock with unit variance, scaled by sd_m.
            z = rng.gauss(0.0, 1.0)
            chi = sum(rng.gauss(0.0, 1.0) ** 2 for _ in range(int(fat_tails))) / fat_tails
            shock = z / math.sqrt(max(chi, 1e-9)) * math.sqrt((fat_tails - 2) / fat_tails)
            r = mu_m + sd_m * shock
            r = max(r, -0.999)
            equity *= 1.0 + r
            peak = max(peak, equity)
            if peak > 0:
                worst_dd = min(worst_dd, equity / peak - 1.0)
            if monthly_target > 0 and r * (equity / (1.0 + r)) >= monthly_target:
                hit = True
            if equity <= capital * 0.1:
                break
        finals.append(equity)
        max_dds.append(worst_dd)
        hits += int(hit)
        dds50 += int(worst_dd <= -0.5)
        ruins += int(equity <= capital * 0.1)

    finals.sort()
    max_dds.sort()

    def pct(xs: list[float], q: float) -> float:
        if not xs:
            return 0.0
        i = min(len(xs) - 1, max(0, int(q * (len(xs) - 1))))
        return xs[i]

    return Outcome(
        n_paths=n_paths,
        median_final=pct(finals, 0.5),
        p10_final=pct(finals, 0.10),
        p90_final=pct(finals, 0.90),
        p_profit=sum(1 for f in finals if f > capital) / n_paths,
        p_hit_target=hits / n_paths,
        p_drawdown_50=dds50 / n_paths,
        p_ruin=ruins / n_paths,
        median_max_dd=pct(max_dds, 0.5),
    )


# ---------------------------------------------------------------------------
# the reality check
# ---------------------------------------------------------------------------
@dataclass
class GoalReport:
    capital: float
    monthly_target: float
    required_monthly: float
    required_annual: float
    lines: list[str] = field(default_factory=list)

    def pretty(self) -> str:
        return "\n".join(self.lines)


def analyse_goal(capital: float, monthly_target: float) -> GoalReport:
    """Everything the arithmetic says about a target, in plain language."""
    m = required_monthly_return(capital, monthly_target)
    a = annualize_monthly(m)
    rep = GoalReport(
        capital=capital,
        monthly_target=monthly_target,
        required_monthly=m,
        required_annual=a,
    )

    rep.lines.append(
        f"Target: ${monthly_target:,.0f}/month from ${capital:,.0f} capital."
    )
    rep.lines.append(
        f"That requires {m:.2%} per month, or {a:,.0%} per year, compounded."
    )
    rep.lines.append("")

    if a < 0.15:
        scale = "plausible for a good systematic strategy"
    elif a < 0.40:
        scale = "top-tier hedge fund territory; extraordinarily hard"
    elif a < 1.0:
        scale = "beyond all but a handful of funds on earth"
    else:
        scale = "not achievable by any known strategy; the arithmetic is negative"

    rep.lines.append(f"Scale: {scale}.")
    rep.lines.append("")
    rep.lines.append("For comparison (annualised returns):")
    for name, val in BENCHMARKS.items():
        rep.lines.append(f"  {name:<44} {val:>7.1%}")
    rep.lines.append("")

    if a > 0:
        rep.lines.append(
            f"At {a:,.0%}/yr, capital doubles every "
            f"{years_to_double(a):.1f} years."
        )
        rep.lines.append(
            f"Compounding ${capital:,.0f} at that rate for 5 years leaves "
            f"${capital * (1 + a) ** 5:,.0f} -- if the rate were real."
        )
    rep.lines.append("")

    rep.lines.append("What the same income needs at honest returns:")
    for label, ar in (
        ("8% / yr (very good)", 0.08),
        ("15% / yr (excellent)", 0.15),
        ("25% / yr (exceptional)", 0.25),
    ):
        need = required_capital(monthly_target, ar)
        rep.lines.append(f"  {label:<28} ${need:,.0f} of capital")
    rep.lines.append("")

    lev = leverage_needed(capital, monthly_target, 0.15)
    if math.isfinite(lev):
        rep.lines.append(
            f"Or: {lev:,.1f}x leverage on an already-excellent 15%/yr strategy."
        )
        if lev > 3:
            rep.lines.append(
                "  At that leverage a single -10% move is total loss. "
                "Ruin is not a risk, it is the schedule."
            )
    return rep
