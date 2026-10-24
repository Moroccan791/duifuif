"""The strategies the literature actually tests, implemented plainly.

Everything here comes from published work, not from invention. The point is to
compare them on the SAME data with the SAME cost model, so the comparison means
something.

Why these, specifically:

* **Buy and hold** is the benchmark, and it is the one to beat. On Bitcoin it is
  not a weak baseline -- published tests put it at a Sharpe around 1.1 with a
  CAGR over 70% across 2014-2023. Most "strategies" lose to it in absolute
  terms and only win on drawdown.

* **Long/flat above a 200-day average.** The simplest documented trend rule.
  A careful 8.5-year test reports Sharpe 0.89 and max drawdown -10.4% versus
  -84% for holding.

* **Donchian breakout (Turtle).** Zarattini & Barbon's crypto ensemble runs nine
  Donchian lookbacks and reports net-of-fee Sharpe above 1.5.

* **Time-series momentum (TSMOM).** 1/3/12-month returns, position by sign, the
  Moskowitz/Ooi/Pedersen recipe as applied to crypto.

* **Volatility targeting** on top of each. This is the part the literature is
  most insistent about: Kim, Tse & Wald show that a large share of published
  trend performance comes from the volatility-scaling step rather than the
  directional signal. In one decomposition, trend alone scored Sharpe 0.71 with
  an -80% drawdown; adding vol scaling gave 0.79 with -15%.

The honest caveats, recorded here rather than buried:

1. The strongest crypto-specific test (Grobys et al. 2025, 30 coins, 2016-2023)
   finds raw TSMOM indistinguishable from zero full-sample, marginally positive
   before July 2020, and *negative and insignificant after July 2020*.
2. Crypto momentum returns have power-law tails: one outlier accounted for 37%
   of the compounded return in that study. A backtest's mean is a few tail
   events wearing a trenchcoat.
3. Any 2020-2025 crypto result rides one enormous uptrend. Sharpe ratios near
   1.5-2.4 from that window are young-market artifacts until they survive a full
   cycle.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from .data import Candle

DAYS_PER_YEAR = 365.0  # crypto trades every day; using 252 overstates Sharpe


# ---------------------------------------------------------------------------
# building blocks
# ---------------------------------------------------------------------------
def sma(vals: list[float], period: int) -> list[float]:
    out: list[float] = []
    run = 0.0
    for i, v in enumerate(vals):
        run += v
        if i >= period:
            run -= vals[i - period]
        out.append(run / min(i + 1, period))
    return out


def realized_vol(returns: list[float], i: int, lookback: int = 60) -> float:
    """Annualised stdev of daily log returns ending at i."""
    lo = max(0, i - lookback + 1)
    window = returns[lo:i + 1]
    if len(window) < 5:
        return 0.0
    mean = sum(window) / len(window)
    var = sum((r - mean) ** 2 for r in window) / (len(window) - 1)
    return math.sqrt(var * DAYS_PER_YEAR)


# ---------------------------------------------------------------------------
# strategies: each returns a target position per bar, in [-1, 1]
# ---------------------------------------------------------------------------
def buy_and_hold(candles: list[Candle]) -> list[float]:
    return [1.0] * len(candles)


def sma_long_flat(candles: list[Candle], window: int = 200) -> list[float]:
    closes = [c.close for c in candles]
    line = sma(closes, window)
    out: list[float] = []
    for i, c in enumerate(closes):
        out.append(1.0 if i >= window and c > line[i] else 0.0)
    return out


def donchian(candles: list[Candle], entry: int = 55, exit_n: int = 20,
             allow_short: bool = False) -> list[float]:
    """Breakout of the N-day high, exit on the M-day low. Turtle-flavoured."""
    out: list[float] = []
    pos = 0.0
    for i in range(len(candles)):
        if i < entry + 1:
            out.append(0.0)
            continue
        hi = max(c.high for c in candles[i - entry:i])
        lo = min(c.low for c in candles[i - entry:i])
        px = candles[i].close
        if px > hi:
            pos = 1.0
        elif allow_short and px < lo:
            pos = -1.0
        else:
            ex_hi = max(c.high for c in candles[i - exit_n:i])
            ex_lo = min(c.low for c in candles[i - exit_n:i])
            if pos > 0 and px < ex_lo:
                pos = 0.0
            elif pos < 0 and px > ex_hi:
                pos = 0.0
        out.append(pos)
    return out


def tsmom(candles: list[Candle], lookbacks: tuple[int, ...] = (21, 63, 252),
          allow_short: bool = True) -> list[float]:
    """Sign of past return, averaged across lookbacks. The MOP recipe."""
    closes = [c.close for c in candles]
    out: list[float] = []
    for i in range(len(closes)):
        votes: list[float] = []
        for lb in lookbacks:
            if i >= lb:
                votes.append(1.0 if closes[i] > closes[i - lb] else -1.0)
        if not votes:
            out.append(0.0)
            continue
        v = sum(votes) / len(votes)
        out.append(v if allow_short else max(0.0, v))
    return out


def ema_cross(candles: list[Candle], fast: int = 20, slow: int = 100) -> list[float]:
    closes = [c.close for c in candles]

    def e(period: int) -> list[float]:
        k = 2.0 / (period + 1.0)
        out = [closes[0]]
        for v in closes[1:]:
            out.append(v * k + out[-1] * (1.0 - k))
        return out

    ef, es = e(fast), e(slow)
    return [1.0 if ef[i] > es[i] else -1.0 for i in range(len(closes))]


# ---------------------------------------------------------------------------
# vol targeting overlay
# ---------------------------------------------------------------------------
def vol_target(positions: list[float], returns: list[float],
               target: float = 0.25, lookback: int = 60,
               max_lev: float = 2.0) -> list[float]:
    """Scale exposure inversely to recent volatility.

    This is the step the literature credits with most of the benefit. It is not
    a prediction; it is risk management. When volatility triples, the position
    shrinks by two thirds.
    """
    out: list[float] = []
    for i, p in enumerate(positions):
        if p == 0.0:
            out.append(0.0)
            continue
        rv = realized_vol(returns, i, lookback)
        if rv <= 0:
            out.append(0.0)
            continue
        scale = min(max_lev, target / rv)
        out.append(p * scale)
    return out


# ---------------------------------------------------------------------------
# evaluation
# ---------------------------------------------------------------------------
@dataclass
class Metrics:
    name: str
    cagr: float
    vol: float
    sharpe: float
    max_dd: float
    calmar: float
    exposure: float
    turns: float          # total position turnover, for cost sanity
    final_multiple: float
    equity: list[float] = field(default_factory=list)

    def pretty(self) -> str:
        return (f"{self.name:<28} {self.cagr*100:>8.1f}% {self.vol*100:>7.1f}% "
                f"{self.sharpe:>7.2f} {self.max_dd*100:>8.1f}% "
                f"{self.exposure*100:>8.0f}%")


def evaluate(name: str, candles: list[Candle], positions: list[float],
             cost_bps: float = 5.0, start: float = 1.0) -> Metrics:
    """Walk the position series forward, one bar at a time.

    The position decided at bar i-1 is applied to the return of bar i. That one-
    bar lag is the difference between a backtest and a fantasy.
    """
    closes = [c.close for c in candles]
    rets = [0.0] + [closes[i] / closes[i - 1] - 1.0 for i in range(1, len(closes))]

    eq = start
    curve = [eq]
    peak = eq
    max_dd = 0.0
    strat_rets: list[float] = []
    prev_pos = 0.0
    turnover = 0.0
    exposed = 0

    for i in range(1, len(candles)):
        pos = positions[i - 1] if i - 1 < len(positions) else 0.0
        r = pos * rets[i]
        cost = abs(pos - prev_pos) * (cost_bps / 10_000.0)
        r -= cost
        turnover += abs(pos - prev_pos)
        if abs(pos) > 1e-9:
            exposed += 1
        prev_pos = pos
        eq *= (1.0 + r)
        curve.append(eq)
        strat_rets.append(r)
        peak = max(peak, eq)
        max_dd = min(max_dd, eq / peak - 1.0)

    years = len(candles) / DAYS_PER_YEAR
    cagr = (eq / start) ** (1.0 / years) - 1.0 if years > 0 and eq > 0 else 0.0
    n = len(strat_rets)
    mean = sum(strat_rets) / n if n else 0.0
    var = sum((x - mean) ** 2 for x in strat_rets) / (n - 1) if n > 1 else 0.0
    sd = math.sqrt(var)
    sharpe = (mean / sd) * math.sqrt(DAYS_PER_YEAR) if sd > 0 else 0.0

    return Metrics(
        name=name, cagr=cagr, vol=sd * math.sqrt(DAYS_PER_YEAR), sharpe=sharpe,
        max_dd=max_dd, calmar=(cagr / abs(max_dd)) if max_dd < 0 else 0.0,
        exposure=exposed / n if n else 0.0, turns=turnover,
        final_multiple=eq / start, equity=curve,
    )


def compare(candles: list[Candle], cost_bps: float = 5.0,
            target_vol: float = 0.25) -> list[Metrics]:
    """Run every documented variant on the same data with the same costs."""
    closes = [c.close for c in candles]
    rets = [0.0] + [closes[i] / closes[i - 1] - 1.0 for i in range(1, len(closes))]

    variants: list[tuple[str, list[float]]] = [
        ("buy and hold", buy_and_hold(candles)),
        ("SMA 200 long/flat", sma_long_flat(candles, 200)),
        ("SMA 50 long/flat", sma_long_flat(candles, 50)),
        ("Donchian 55/20 long", donchian(candles, 55, 20)),
        ("Donchian 55/20 long/short", donchian(candles, 55, 20, True)),
        ("TSMOM 1/3/12m long/short", tsmom(candles)),
        ("TSMOM 1/3/12m long only", tsmom(candles, allow_short=False)),
        ("EMA 20/100 cross", ema_cross(candles, 20, 100)),
    ]

    out: list[Metrics] = []
    for name, pos in variants:
        out.append(evaluate(name, candles, pos, cost_bps))
        out.append(evaluate(f"{name} + vol target",
                            candles, vol_target(pos, rets, target_vol),
                            cost_bps))
    return out


# ---------------------------------------------------------------------------
# multi-asset
# ---------------------------------------------------------------------------
def strategy_returns(candles: list[Candle], positions: list[float],
                     cost_bps: float = 5.0) -> list[float]:
    """Net daily returns of one strategy on one asset (one-bar lag, with costs)."""
    closes = [c.close for c in candles]
    out: list[float] = []
    prev = 0.0
    for i in range(1, len(closes)):
        pos = positions[i - 1] if i - 1 < len(positions) else 0.0
        r = pos * (closes[i] / closes[i - 1] - 1.0)
        r -= abs(pos - prev) * (cost_bps / 10_000.0)
        prev = pos
        out.append(r)
    return out


def portfolio(series: dict[str, list[float]], weights: dict[str, float] | None = None,
              start: float = 1.0) -> tuple[list[float], Metrics]:
    """Equal-weight a set of per-asset return series, aligned by index.

    Diversifying across coins is the standard answer to "you tested the one that
    survived". Two correlated assets is weak diversification, but it is strictly
    better than one, and the same-window comparison removes the regime confound.
    """
    names = [n for n in series if series[n]]
    if not names:
        raise ValueError("no series")
    n = min(len(series[k]) for k in names)
    w = weights or {k: 1.0 / len(names) for k in names}

    combined = [sum(w.get(k, 0.0) * series[k][i] for k in names) for i in range(n)]
    eq = start
    curve = [eq]
    peak = eq
    max_dd = 0.0
    for r in combined:
        eq *= (1.0 + r)
        curve.append(eq)
        peak = max(peak, eq)
        max_dd = min(max_dd, eq / peak - 1.0)

    years = n / DAYS_PER_YEAR
    cagr = (eq / start) ** (1.0 / years) - 1.0 if years > 0 and eq > 0 else 0.0
    mean = sum(combined) / n
    var = sum((x - mean) ** 2 for x in combined) / (n - 1) if n > 1 else 0.0
    sd = math.sqrt(var)
    sharpe = (mean / sd) * math.sqrt(DAYS_PER_YEAR) if sd > 0 else 0.0
    return combined, Metrics(
        name="portfolio", cagr=cagr, vol=sd * math.sqrt(DAYS_PER_YEAR),
        sharpe=sharpe, max_dd=max_dd,
        calmar=(cagr / abs(max_dd)) if max_dd < 0 else 0.0,
        exposure=1.0, turns=0.0, final_multiple=eq / start, equity=curve,
    )


def returns_with_dates(candles: list[Candle], positions: list[float],
                       cost_bps: float = 5.0) -> dict[float, float]:
    """Same as `strategy_returns` but keyed by bar timestamp, for date alignment."""
    closes = [c.close for c in candles]
    out: dict[float, float] = {}
    prev = 0.0
    for i in range(1, len(closes)):
        pos = positions[i - 1] if i - 1 < len(positions) else 0.0
        r = pos * (closes[i] / closes[i - 1] - 1.0)
        r -= abs(pos - prev) * (cost_bps / 10_000.0)
        prev = pos
        out[candles[i].ts] = r
    return out


def align_and_portfolio(dated: dict[str, dict[float, float]],
                        start: float = 1.0) -> tuple[list[float], Metrics, int]:
    """Portfolio across assets on the dates they all share."""
    names = list(dated)
    common = set(dated[names[0]])
    for k in names[1:]:
        common &= set(dated[k])
    dates = sorted(common)
    series = {k: [dated[k][d] for d in dates] for k in names}
    combined, m = portfolio(series, start=start)
    return combined, m, len(dates)
