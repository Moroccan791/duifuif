"""Reading a chart.

This is the part that replaces "look at the graph". A human glancing at a chart
does roughly six things at once, and each of them is computable:

    trend        which way, and how convincingly
    momentum     is it accelerating or fading
    structure    higher highs and higher lows, or lower ones
    levels       where the nearest support and resistance sit
    volatility   how far price is likely to travel from here
    volume       is anybody actually behind this move

`read_chart()` returns all six as numbers. `ChartRead.narrate()` renders them as
the sentence a trader would say out loud. The strategy then reasons over the
structured version, and the dashboard draws the same thing so you can see what it
saw.

Deliberately NOT included: any attempt to recognise visual patterns like "head
and shoulders" from pixels. That is doable and it is worse than the features
above, for the same reason a doctor does not diagnose from a photograph when a
blood test is available. The chart image is a lossy compression of the numbers.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .data import Candle


# ---------------------------------------------------------------------------
# primitives
# ---------------------------------------------------------------------------
def ema(values: list[float], period: int) -> list[float]:
    if not values:
        return []
    k = 2.0 / (period + 1.0)
    out = [values[0]]
    for v in values[1:]:
        out.append(v * k + out[-1] * (1.0 - k))
    return out


def sma(values: list[float], period: int) -> list[float]:
    out: list[float] = []
    run = 0.0
    for i, v in enumerate(values):
        run += v
        if i >= period:
            run -= values[i - period]
        out.append(run / min(i + 1, period))
    return out


def rsi(closes: list[float], period: int = 14) -> float:
    """Wilder's RSI. Returns 50.0 when there is not enough data."""
    if len(closes) < period + 1:
        return 50.0
    gains = losses = 0.0
    for i in range(1, period + 1):
        d = closes[i] - closes[i - 1]
        gains += max(d, 0.0)
        losses += max(-d, 0.0)
    ag, al = gains / period, losses / period
    for i in range(period + 1, len(closes)):
        d = closes[i] - closes[i - 1]
        ag = (ag * (period - 1) + max(d, 0.0)) / period
        al = (al * (period - 1) + max(-d, 0.0)) / period
    if al == 0:
        return 100.0
    return 100.0 - 100.0 / (1.0 + ag / al)


def atr(candles: list[Candle], period: int = 14) -> float:
    if len(candles) < 2:
        return 0.0
    trs: list[float] = []
    for i in range(1, len(candles)):
        c, p = candles[i], candles[i - 1]
        trs.append(max(c.high - c.low, abs(c.high - p.close), abs(c.low - p.close)))
    return sum(trs[-period:]) / min(period, len(trs))


def swings(candles: list[Candle], k: int = 3) -> tuple[list[float], list[float]]:
    """Fractal swing highs and lows: a bar that is the extreme of its window."""
    highs: list[float] = []
    lows: list[float] = []
    for i in range(k, len(candles) - k):
        window = candles[i - k:i + k + 1]
        if candles[i].high >= max(c.high for c in window):
            highs.append(candles[i].high)
        if candles[i].low <= min(c.low for c in window):
            lows.append(candles[i].low)
    return highs, lows


# ---------------------------------------------------------------------------
# the read
# ---------------------------------------------------------------------------
@dataclass
class ChartRead:
    n: int
    price: float

    trend: str                  # "up" | "down" | "flat"
    trend_strength: float       # -1..1, signed
    momentum: float             # RSI 0..100
    momentum_dir: str           # "rising" | "falling" | "flat"
    structure: str              # "higher" | "lower" | "mixed"
    vol_pct: float              # ATR as a fraction of price
    rel_volume: float           # recent volume / baseline
    range_pos: float            # 0 at range low, 1 at range high
    support: float
    resistance: float
    distance_to_resistance: float   # as a fraction of price
    distance_to_support: float
    breakout: str               # "up" | "down" | "none"
    patterns: list[str] = field(default_factory=list)
    ema_fast: float = 0.0
    ema_slow: float = 0.0

    # -- human readable -----------------------------------------------------
    def narrate(self) -> str:
        bits = [
            f"{self.trend.upper()} trend "
            f"(strength {self.trend_strength:+.2f}), "
            f"structure {self.structure} highs/lows",
            f"momentum {self.momentum:.0f} and {self.momentum_dir}",
            f"price at {self.range_pos*100:.0f}% of the recent range",
        ]
        if self.breakout != "none":
            bits.append(f"BREAKOUT {self.breakout}")
        bits.append(
            f"resistance {self.distance_to_resistance*100:+.2f}% away, "
            f"support {self.distance_to_support*100:+.2f}% away"
        )
        bits.append(f"volume {self.rel_volume:.2f}x normal")
        if self.patterns:
            bits.append("patterns: " + ", ".join(self.patterns))
        return "; ".join(bits) + "."

    def compact(self) -> str:
        return (f"{self.trend:>4} {self.structure:>6} rsi={self.momentum:5.1f} "
                f"pos={self.range_pos:4.2f} v={self.rel_volume:4.2f} "
                f"atr={self.vol_pct*100:4.2f}%")


def _detect_patterns(c: list[Candle]) -> list[str]:
    out: list[str] = []
    if len(c) < 3:
        return out
    a, b = c[-2], c[-1]
    body = max(b.body, 1e-12)
    rng = max(b.range, 1e-12)

    if b.body / rng < 0.1:
        out.append("doji")
    upper = b.high - max(b.open, b.close)
    lower = min(b.open, b.close) - b.low
    if lower > 2 * body and upper < body:
        out.append("hammer")
    if upper > 2 * body and lower < body:
        out.append("shooting-star")
    if (b.close > b.open and a.close < a.open
            and b.close > a.open and b.open < a.close):
        out.append("bullish-engulfing")
    if (b.close < b.open and a.close > a.open
            and b.close < a.open and b.open > a.close):
        out.append("bearish-engulfing")
    return out


def read_chart(candles: list[Candle], fast: int = 12, slow: int = 26,
               swing_k: int = 3, range_lookback: int = 40) -> ChartRead:
    """Reduce a candle series to the six things a trader actually looks at."""
    if len(candles) < 5:
        raise ValueError("need at least 5 candles to read a chart")

    closes = [c.close for c in candles]
    price = closes[-1]

    ef = ema(closes, fast)
    es = ema(closes, slow)
    # Trend: EMA separation, normalised by price so it is comparable across assets.
    sep = (ef[-1] - es[-1]) / price if price else 0.0
    # Scale: 0.5% separation is a strong trend on a 5m chart.
    strength = max(-1.0, min(1.0, sep / 0.005))
    if abs(strength) < 0.15:
        trend = "flat"
    elif strength > 0:
        trend = "up"
    else:
        trend = "down"

    m = rsi(closes)
    prev_m = rsi(closes[:-3]) if len(closes) > 5 else m
    momentum_dir = ("rising" if m > prev_m + 1.0
                    else "falling" if m < prev_m - 1.0 else "flat")

    highs, lows = swings(candles, swing_k)
    if len(highs) >= 2 and len(lows) >= 2:
        if highs[-1] > highs[-2] and lows[-1] > lows[-2]:
            structure = "higher"
        elif highs[-1] < highs[-2] and lows[-1] < lows[-2]:
            structure = "lower"
        else:
            structure = "mixed"
    else:
        structure = "mixed"

    a = atr(candles)
    vol_pct = a / price if price else 0.0

    recent_vol = sum(c.volume for c in candles[-5:]) / 5.0
    base_slice = candles[-40:-5] or candles[:-5] or candles
    base_vol = sum(c.volume for c in base_slice) / max(1, len(base_slice))
    rel_volume = recent_vol / base_vol if base_vol > 0 else 1.0

    window = candles[-range_lookback:]
    hi = max(c.high for c in window)
    lo = min(c.low for c in window)
    span = hi - lo
    range_pos = (price - lo) / span if span > 0 else 0.5

    # Nearest levels: the closest swing above (resistance) and below (support).
    above = [h for h in highs if h > price] or [hi]
    below = [l for l in lows if l < price] or [lo]
    resistance = min(above)
    support = max(below)

    breakout = "none"
    prior = candles[-range_lookback:-1] or candles[:-1]
    if prior:
        if price > max(c.high for c in prior):
            breakout = "up"
        elif price < min(c.low for c in prior):
            breakout = "down"

    return ChartRead(
        n=len(candles),
        price=price,
        trend=trend,
        trend_strength=strength,
        momentum=m,
        momentum_dir=momentum_dir,
        structure=structure,
        vol_pct=vol_pct,
        rel_volume=rel_volume,
        range_pos=range_pos,
        support=support,
        resistance=resistance,
        distance_to_resistance=(resistance - price) / price if price else 0.0,
        distance_to_support=(support - price) / price if price else 0.0,
        breakout=breakout,
        patterns=_detect_patterns(candles),
        ema_fast=ef[-1],
        ema_slow=es[-1],
    )
