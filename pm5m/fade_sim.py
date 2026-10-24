"""What $15 a day would actually require, on the five-minute fade.

The hit rate is measured (`REVERSAL.md`). The fee algebra is exact
(`reversal.py`). This module turns those into an income plan: how much capital,
how many bets, how bad the bad days are, and what happens to the plan when the
fill price is not the one you hoped for.

TWO WAYS TO RUN IT, AND THEY ANSWER DIFFERENT QUESTIONS

* `compound=True` — the bankroll grows. Mean daily return is a growth rate.
* `compound=False` — the bankroll is reset to its starting size at each day
  boundary, so you skim the profit (or refill the loss) and keep the same
  amount of money working. This is the right model for *income*, and it is what
  "$15 a day" means: not "grow into $15 a day eventually", but "take $15 out
  today, and tomorrow, from a fixed float."

Averaging dollar P&L against the *starting* bankroll while the bankroll
compounds produces a meaningless number — the first version of this module
reported a mean daily return above 100,000,000,000,000% — so the two modes are
kept explicitly separate rather than blended.

Everything here assumes you get filled at `fill`. That number comes from an
order book this code cannot see. Treat every output as conditional on it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .reversal import DEFAULT_FEE_RATE, fade_probability, kelly_fraction


@dataclass(frozen=True)
class FadeConfig:
    fill: float = 0.50          # assumed ask on the reversion side
    threshold: float = 0.004    # minimum trailing move to bet on
    kelly_mult: float = 0.5     # fraction of full Kelly (0.5 = half Kelly)
    # 2%, not 5%: Kelly sizes the LARGEST stakes on the >1.0% move band, which
    # is measured from only 927 windows. Sizing off the least-sampled bucket is
    # how a backtest becomes a bank account with nothing in it, so the stake is
    # capped well below what the raw Kelly number asks for.
    max_stake_pct: float = 0.02
    # BTC, ETH and SOL fire at the same time and lose together: they are one
    # trade, not three. Without a portfolio cap the combined run drew down 95%,
    # because three 2% legs on the same five-minute shock is 6% at risk on a
    # single event. Cap the TOTAL at risk at any moment, not just each bet.
    max_concurrent_pct: float = 0.10
    min_stake: float = 1.0      # below this a bet is not worth the click
    fee: float = DEFAULT_FEE_RATE


@dataclass
class DayResult:
    day: int
    trades: int = 0
    wins: int = 0
    pnl: float = 0.0
    start_equity: float = 0.0


@dataclass
class FadeResult:
    start_equity: float
    final_equity: float
    trades: int
    wins: int
    days: int
    withdrawn: float
    short_days: int            # days trading did not earn the withdrawal
    min_equity: float          # lowest the float ever got
    ruined: bool
    max_drawdown: float        # fraction, negative
    worst_day: float           # dollars
    best_day: float            # dollars
    daily_pnl: list[float] = field(default_factory=list)

    @property
    def hit_rate(self) -> float:
        return self.wins / self.trades if self.trades else 0.0

    @property
    def mean_daily_pnl(self) -> float:
        return sum(self.daily_pnl) / len(self.daily_pnl) if self.daily_pnl else 0.0

    @property
    def median_daily_pnl(self) -> float:
        if not self.daily_pnl:
            return 0.0
        s = sorted(self.daily_pnl)
        return s[len(s) // 2]

    @property
    def mean_daily_return(self) -> float:
        """Mean daily P&L as a fraction of the *starting* bankroll."""
        return self.mean_daily_pnl / self.start_equity if self.start_equity else 0.0

    @property
    def losing_day_pct(self) -> float:
        if not self.daily_pnl:
            return 0.0
        return sum(1 for r in self.daily_pnl if r < 0) / len(self.daily_pnl)

    @property
    def short_day_pct(self) -> float:
        return self.short_days / self.days if self.days else 0.0


def kelly_function_safe(p_true: float, cfg: FadeConfig) -> float:
    return kelly_fraction(p_true, cfg.fill, cfg.fee)


def _won(up: int, move: float) -> bool:
    """A fade bet wins when the window settles against the move."""
    return (up == 1) if move < 0 else (up == 0)


def win_profit_per_dollar(cfg: FadeConfig) -> float:
    """Profit on a winning bet, per dollar staked.

    You pay `fill` per share plus the fee, and a winner pays 1.00.
    """
    cost = cfg.fill + cfg.fee * cfg.fill * (1.0 - cfg.fill)
    return 1.0 / cost - 1.0


def simulate(
    windows: list[tuple[float, int, float]],
    bankroll: float = 1_000.0,
    cfg: FadeConfig | None = None,
    withdraw_per_day: float = 0.0,
    compound: bool = True,
    max_days: int | None = None,
) -> FadeResult:
    """Run the fade forward, one window at a time.

    Withdrawals happen at the UTC day boundary and are capped at the bankroll —
    you cannot take out money you do not have. A day where the bankroll could
    not cover it is counted in `short_days`, which is the honest answer to "can
    I actually live off this yet".

    With `compound=False` the bankroll is restored to its starting size every
    day: profit is skimmed, losses are topped up. That keeps the stake sizing
    stable and makes the dollar figures directly comparable.
    """
    cfg = cfg or FadeConfig()
    equity = float(bankroll)
    peak = equity
    max_dd = 0.0
    trades = wins = 0
    withdrawn = 0.0
    short_days = 0
    ruined = False
    low = equity
    win_mult = win_profit_per_dollar(cfg)
    open_bets: list[tuple[float, float]] = []   # (settles_at, stake)

    days: list[DayResult] = []
    cur = DayResult(day=-1, start_equity=equity)

    def close_day(day: DayResult) -> None:
        nonlocal equity, withdrawn, short_days
        # A day is "short" when trading did not earn the withdrawal, so the
        # float had to cover the difference. That is the number that says
        # whether an income target is being met or merely borrowed from capital.
        if withdraw_per_day > 0 and day.pnl < withdraw_per_day:
            short_days += 1
        if withdraw_per_day > 0:
            take = min(withdraw_per_day, max(0.0, equity))
            equity -= take
            withdrawn += take
        if not compound:
            # reset the float: skim the profit, replace the loss
            equity = float(bankroll)
        days.append(day)

    for ts, up, move in windows:
        if abs(move) < cfg.threshold:
            continue
        day_no = int(ts // 86_400)

        if cur.day == -1:
            cur.day = day_no
            cur.start_equity = equity
        elif day_no != cur.day:
            close_day(cur)
            if max_days is not None and len(days) >= max_days:
                cur = None
                break
            cur = DayResult(day=day_no, start_equity=equity)

        p_true = fade_probability(abs(move))
        if p_true <= 0.5:
            continue
        # a bet opened at `ts` is at risk until it settles 300s later
        open_bets = [(t, s_) for (t, s_) in open_bets if t > ts]
        outstanding = sum(s_ for _, s_ in open_bets)

        frac = min(cfg.kelly_mult * kelly_function_safe(p_true, cfg),
                   cfg.max_stake_pct)
        stake = min(frac * equity, equity)
        room = max(0.0, cfg.max_concurrent_pct * equity - outstanding)
        stake = min(stake, room)
        if stake <= 0.0 or stake < cfg.min_stake:
            continue
        open_bets.append((ts + 300.0, stake))

        won = _won(up, move)
        trades += 1
        cur.trades += 1
        if won:
            wins += 1
            cur.wins += 1
            equity += stake * win_mult
            cur.pnl += stake * win_mult
        else:
            equity -= stake
            cur.pnl -= stake

        low = min(low, equity)
        peak = max(peak, equity)
        if peak > 0:
            max_dd = min(max_dd, equity / peak - 1.0)
        if equity <= 1e-9:
            ruined = True
            break

    if cur is not None and cur.day != -1 and not ruined:
        close_day(cur)

    return FadeResult(
        start_equity=bankroll,
        final_equity=equity,
        trades=trades,
        wins=wins,
        days=len(days),
        withdrawn=withdrawn,
        short_days=short_days,
        min_equity=low,
        ruined=ruined,
        max_drawdown=max_dd,
        worst_day=min((d.pnl for d in days), default=0.0),
        best_day=max((d.pnl for d in days), default=0.0),
        daily_pnl=[d.pnl for d in days],
    )


def expected_daily_pnl(
    windows: list[tuple[float, int, float]],
    bankroll: float = 1_000.0,
    cfg: FadeConfig | None = None,
    p_true: float | None = None,
) -> float:
    """Expected daily profit, computed rather than sampled.

    A single realised path is worthless as an estimate here: 3,519 bets with a
    half-cent edge each have a daily standard error larger than the edge, so
    `simulate` can show +$0.03/day or -$0.30/day from the same edge. Capital
    requirements therefore come from the arithmetic — bets per day, stake
    fraction, expected value per stake — and the simulation is used only for
    what it is good at: the shape of the bad days.

    `p_true` overrides the measured probability, which is how the confidence
    interval gets turned into a range on the capital requirement.
    """
    cfg = cfg or FadeConfig()
    if not windows:
        return 0.0
    win_mult = win_profit_per_dollar(cfg)
    days = len({int(w[0] // 86_400) for w in windows})
    if days == 0:
        return 0.0

    total = 0.0
    for _ts, _up, move in windows:
        if abs(move) < cfg.threshold:
            continue
        p = p_true if p_true is not None else fade_probability(abs(move))
        if p <= 0.5:
            continue
        frac = min(cfg.kelly_mult * kelly_fraction(p, cfg.fill, cfg.fee),
                   cfg.max_stake_pct)
        if frac * bankroll < cfg.min_stake:
            continue
        stake = frac * bankroll
        total += stake * (p * win_mult - (1.0 - p))
    return total / days


def capital_for_daily_target(
    windows: list[tuple[float, int, float]],
    target_per_day: float,
    cfg: FadeConfig | None = None,
    p_true: float | None = None,
    probe: float = 10_000.0,
) -> float:
    """Bankroll whose *expected* daily profit equals `target_per_day`.

    Stakes are a fixed fraction of bankroll, so expected profit is linear and
    the answer is a division. Returns infinity when the edge is not positive.
    """
    cfg = cfg or FadeConfig()
    per_dollar = expected_daily_pnl(windows, probe, cfg, p_true) / probe
    if per_dollar <= 0:
        return float("inf")
    return target_per_day / per_dollar


def scale_check(windows: list[tuple[float, int, float]],
                target_per_day: float,
                cfg: FadeConfig | None = None) -> dict[str, float]:
    """Capital required, then what the days look like at that capital.

    The second half matters more than the first: an average can be met and still
    be unlivable, so this reports the worst day, the worst drawdown, and how
    often the target would have been missed.
    """
    cfg = cfg or FadeConfig()
    cap = capital_for_daily_target(windows, target_per_day, cfg)
    out: dict[str, float] = {"capital": cap}
    if cap == float("inf"):
        return out
    res = simulate(windows, bankroll=cap, cfg=cfg, compound=False,
                   withdraw_per_day=target_per_day)
    out.update({
        "days": float(res.days),
        "min_equity": res.min_equity,
        "final_equity_after_withdrawals": res.final_equity,
        "trades": float(res.trades),
        "hit_rate": res.hit_rate,
        "mean_daily": res.mean_daily_pnl,
        "median_daily": res.median_daily_pnl,
        "worst_day": res.worst_day,
        "best_day": res.best_day,
        "max_drawdown": res.max_drawdown,
        "losing_day_pct": res.losing_day_pct,
        "short_day_pct": res.short_day_pct,
        "withdrawn": res.withdrawn,
        "final_equity": res.final_equity,
    })
    return out
