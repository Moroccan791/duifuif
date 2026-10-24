"""Does the stated plan actually work? Simulate it instead of arguing about it.

The plan, in the user's words: start with $80-100, make a fixed $10-20 a day.

The useful thing about a plan stated that precisely is that it can be checked.
`$10/day on $90` is **11% per day**, or ~3,000% a month compounded. Rather than
assert that this is or isn't possible, this module simulates the actual process
at several honest edge assumptions and reports the distribution of outcomes.

Both the arithmetic and the variance matter, and they point in opposite
directions:

  * the ARITHMETIC says a 5pp edge on 40 trades a day compounds to roughly 40x
    in three months, which would make the daily target trivially easy;
  * the FACT that the arithmetic is that generous is itself evidence the edge is
    not real. A 5pp edge on a liquid 5-minute market at 40 trades/day is not a
    small inefficiency, it is a money printer, and money printers do not sit
    open on a public exchange.

So the simulation is run across edge sizes, including zero. The zero-edge column
is not a strawman -- it is the base rate for a strategy that has not yet been
shown to have an edge at all.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

from .fees import fee_as_pct_of_stake


@dataclass
class PlanConfig:
    start: float = 90.0
    daily_target: float = 15.0
    price: float = 0.55
    edge: float = 0.0            # our true prob minus the market's price
    trades_per_day: int = 40
    days: int = 90
    kelly_fraction: float = 0.25
    max_stake_pct: float = 0.05  # never more than 5% of bankroll on one trade
    maker: bool = False
    seed: int = 0


@dataclass
class PlanResult:
    cfg: PlanConfig
    finals: list[float] = field(default_factory=list)
    ruined: int = 0
    hit_target_days: list[int] = field(default_factory=list)

    @property
    def n(self) -> int:
        return len(self.finals)

    def quantiles(self) -> list[tuple[float, float]]:
        s = sorted(self.finals)
        out = []
        for q in (0.05, 0.25, 0.50, 0.75, 0.95):
            out.append((q, s[min(len(s) - 1, int(q * len(s)))]))
        return out

    @property
    def p_ruin(self) -> float:
        return self.ruined / self.n if self.n else 0.0

    @property
    def p_profit(self) -> float:
        return sum(1 for f in self.finals if f > self.cfg.start) / self.n if self.n else 0.0

    @property
    def p_target(self) -> float:
        return len(self.hit_target_days) / self.n if self.n else 0.0

    @property
    def median_days_to_target(self) -> float | None:
        if not self.hit_target_days:
            return None
        s = sorted(self.hit_target_days)
        return s[len(s) // 2]


def kelly_stake(bankroll: float, price: float, p_true: float, cfg: PlanConfig) -> float:
    """Fractional Kelly, capped. Returns a dollar stake, or 0 if there is no edge."""
    fee = 0.0 if cfg.maker else fee_as_pct_of_stake(price)
    cost = price * (1.0 + fee)
    if cost >= 1.0 or cost <= 0:
        return 0.0
    b = (1.0 - cost) / cost
    if b <= 0:
        return 0.0
    f = (p_true * b - (1.0 - p_true)) / b
    if f <= 0:
        return 0.0
    f = min(f * cfg.kelly_fraction, cfg.max_stake_pct)
    return bankroll * f


def simulate(cfg: PlanConfig, paths: int = 4000) -> PlanResult:
    rng = random.Random(cfg.seed)
    res = PlanResult(cfg=cfg)
    min_stake = 0.05

    for _ in range(paths):
        bank = cfg.start
        hit_day: int | None = None
        day_pnl: list[float] = []

        for day in range(cfg.days):
            day_start = bank
            for _ in range(cfg.trades_per_day):
                stake = kelly_stake(bank, cfg.price, cfg.price + cfg.edge, cfg)
                if stake < min_stake or stake > bank:
                    stake = min(bank, max(0.0, stake))
                if stake < min_stake:
                    break
                fee = 0.0 if cfg.maker else stake * fee_as_pct_of_stake(cfg.price)
                shares = stake / cfg.price
                if rng.random() < cfg.price + cfg.edge:
                    bank += shares - stake - fee   # share pays 1.00
                else:
                    bank -= stake + fee
                if bank <= 1.0:
                    break
            day_pnl.append(bank - day_start)
            if bank <= 1.0:
                break
            if hit_day is None and bank - cfg.start >= cfg.daily_target * 10:
                hit_day = day + 1

        if bank <= 1.0:
            res.ruined += 1
        else:
            res.finals.append(bank)
        if hit_day is not None and bank > 1.0:
            res.hit_target_days.append(hit_day)

    return res


def pretty(cfg: PlanConfig, paths: int = 4000) -> str:
    res = simulate(cfg, paths)
    lines = [
        f"start ${cfg.start:.0f}   target ${cfg.daily_target:.0f}/day   "
        f"{cfg.trades_per_day} trades/day   {cfg.days} days   "
        f"{'maker' if cfg.maker else 'taker'}",
        f"edge assumed: {cfg.edge*100:+.1f}pp   "
        f"(= true win rate {(cfg.price+cfg.edge)*100:.1f}% at a {cfg.price*100:.0f}c price)",
        "",
        f"  P(ruined, under $1)        {res.p_ruin:6.1%}",
        f"  P(ends above start)        {res.p_profit:6.1%}",
        f"  P(clears $150 profit)      {res.p_target:6.1%}",
    ]
    q = res.quantiles()
    lines.append("  ")
    lines.append("  ending bankroll:")
    for pct, val in q:
        lines.append(f"    p{int(pct*100):02d}  ${val:>10,.0f}")
    lines.append("")
    lines.append(f"  median ${q[2][1]:,.0f} from ${cfg.start:.0f}")
    lines.append(f"  that is {q[2][1]/cfg.start:,.1f}x over {cfg.days} days "
                 f"= {((q[2][1]/cfg.start)**(30/cfg.days)-1)*100:+,.1f}%/month")
    return "\n".join(lines)


def daily_ev(bankroll: float, edge: float, price: float = 0.55,
             trades_per_day: int = 40, maker: bool = False,
             kelly_fraction: float = 0.25, max_stake_pct: float = 0.05) -> dict:
    """Expected $/day, plus the stake and per-trade EV, for a given edge.

    This is the reverse of the question the simulation answers. Rather than
    asking "what does $90 turn into", ask "what does it take to make $15/day" --
    and the answer is mostly *capital*, not edge.
    """
    fee = 0.0 if maker else fee_as_pct_of_stake(price)
    cost = price * (1.0 + fee)
    b = (1.0 - cost) / cost
    p = min(0.999, price + max(0.0, edge))
    raw = (p * b - (1.0 - p)) / b
    f = 0.0 if raw <= 0 else min(raw * kelly_fraction, max_stake_pct)
    stake = bankroll * f
    ev_per_trade = stake * (p * b - (1.0 - p)) / 1.0 if f > 0 else 0.0
    return {
        "stake": stake,
        "f_pct": f * 100.0,
        "ev_per_trade": ev_per_trade,
        "per_day": ev_per_trade * trades_per_day,
        "per_month": ev_per_trade * trades_per_day * 30,
        "breakeven_edge": (cost - price) / price * 0.0,  # see band command
    }


def bankroll_for_daily(daily_target: float, edge: float, price: float = 0.55,
                       trades_per_day: int = 40, maker: bool = False) -> float:
    """Bankroll needed to *average* `daily_target` at a given edge."""
    unit = daily_ev(1.0, edge, price, trades_per_day, maker)["per_day"]
    if unit <= 0:
        return float("inf")
    return daily_target / unit


def required_market_edge(start: float, daily_target: float, price: float = 0.55,
                         trades_per_day: int = 40, maker: bool = False) -> float:
    """Rough edge needed to average `daily_target` on `start`, via fractional Kelly.

    Uses the closed form rather than a search, because the answer should be
    auditable: EV per trade = f * bankroll * (p*b - q), and the daily expected
    return is trades_per_day times that.
    """
    fee = 0.0 if maker else fee_as_pct_of_stake(price)
    cost = price * (1.0 + fee)
    if cost >= 1.0:
        return math.inf
    b = (1.0 - cost) / cost
    need = (daily_target / start) / trades_per_day   # required return per trade
    # Solve f*(p*b-q) = need with p = price + e, f = 0.25 * (p*b-q)/b, capped.
    # Approximate: for small e, (p*b-q) ~= e*(b+1), so need ~= f*e*(b+1)/b with
    # f ~= 0.25*e*(b+1)/b  =>  need ~= 0.25*e^2*(b+1)^2/b^2.
    return math.sqrt(need * 4.0) * b / (b + 1.0)


def daily_distribution(bankroll: float, edge: float, price: float = 0.55,
                       trades_per_day: int = 40, maker: bool = False,
                       kelly_fraction: float = 0.25, max_stake_pct: float = 0.05,
                       days: int = 20000, seed: int = 3) -> dict:
    """Distribution of ONE day's P&L. This is what kills the 'fixed' target.

    A 'fixed $10-20 a day' is not a quantity a probabilistic edge can produce.
    Each trade is a coin flip; a day is 40 of them. Even with a large edge the
    daily total is a random variable with a wide spread, and a meaningful share
    of days are losses.
    """
    rng = random.Random(seed)
    fee_rate = 0.0 if maker else fee_as_pct_of_stake(price)
    stake = kelly_stake(bankroll, price, price + max(0.0, edge),
                        PlanConfig(start=bankroll, price=price, maker=maker,
                                   kelly_fraction=kelly_fraction,
                                   max_stake_pct=max_stake_pct))
    if stake <= 0:
        return {"stake": 0.0, "mean": 0.0, "sd": 0.0, "p05": 0.0, "p50": 0.0,
                "p95": 0.0, "p_loss_day": 1.0, "p_under_5": 1.0, "n": 0}
    fee = stake * fee_rate
    shares = stake / price
    p = price + edge
    win = shares - stake - fee
    lose = -(stake + fee)
    day_pnls = []
    for _ in range(days):
        tot = 0.0
        for _ in range(trades_per_day):
            tot += win if rng.random() < p else lose
        day_pnls.append(tot)
    day_pnls.sort()
    n = len(day_pnls)
    mean = sum(day_pnls) / n
    sd = (sum((x - mean) ** 2 for x in day_pnls) / n) ** 0.5
    return {
        "stake": stake,
        "mean": mean,
        "sd": sd,
        "p05": day_pnls[int(0.05 * n)],
        "p50": day_pnls[n // 2],
        "p95": day_pnls[int(0.95 * n)],
        "p_loss_day": sum(1 for x in day_pnls if x < 0) / n,
        "p_under_5": sum(1 for x in day_pnls if x < 5.0) / n,
        "n": n,
    }
