"""Turning a chart read into a decision.

The rules are written out in full and every one is inspectable, because a black
box that trades your money is useless even when it works -- you cannot tell a
lucky run from a broken one.

Two modes, because charts do two things:

    trend     price is going somewhere; buy pullbacks in the direction of travel
    range     price is going nowhere; fade the edges, but smaller

The default is trend. A signal only fires when enough independent readings agree,
and the count of agreeing readings IS the confidence -- it is not a fitted
number.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .reader import ChartRead


@dataclass
class SignalConfig:
    mode: str = "trend"          # "trend" | "range" | "both"
    min_confidence: int = 3      # how many agreeing reads before we act
    risk_pct: float = 0.02       # of equity, per trade
    stop_atr: float = 1.2
    target_atr: float = 2.2
    max_vol_pct: float = 0.02    # skip if ATR > 2% of price (too wild)
    min_vol_pct: float = 0.0004  # skip if ATR < 0.04% (dead, fees dominate)
    allow_short: bool = True
    max_notional_pct: float = 0.5   # spot, no leverage: never commit > 50% of equity


@dataclass
class Signal:
    action: str                  # "buy" | "sell" | "flat"
    confidence: int
    reasons: list[str] = field(default_factory=list)
    stop: float = 0.0
    target: float = 0.0
    size: float = 0.0            # base-asset units
    mode: str = ""
    blocked: str = ""

    @property
    def tradeable(self) -> bool:
        return self.action in ("buy", "sell") and not self.blocked and self.size > 0

    def describe(self) -> str:
        if self.blocked:
            return f"FLAT ({self.blocked})"
        if self.action == "flat":
            return f"FLAT - {', '.join(self.reasons) or 'no setup'}"
        rr = abs(self.target - self.stop) / max(1e-12, abs(self.stop - 0)) if self.stop else 0
        return (f"{self.action.upper()} conf={self.confidence} size={self.size:.6f} "
                f"stop={self.stop:,.2f} target={self.target:,.2f} "
                f"[{'; '.join(self.reasons)}]")


def _position_size(equity: float, price: float, stop_distance: float,
                   risk_pct: float, max_notional_pct: float = 0.5) -> tuple[float, float]:
    """(units, effective risk as a fraction of equity).

    Risk-based sizing says: units = (equity * risk_pct) / stop_distance.

    On a small spot account that formula regularly asks for more notional than
    the account holds -- a 0.4% ATR stop with a 2% risk budget implies a position
    of 4x equity. There is no leverage on spot, so the position is capped by
    capital instead, and the REAL risk per trade comes out far below the target.

    This is the arithmetic that makes small accounts hard, and it should be
    visible rather than silent: an account that cannot fund its own stop
    distance is not risking 2%, it is risking whatever the cap allows.
    """
    if stop_distance <= 0 or price <= 0 or equity <= 0:
        return 0.0, 0.0
    risk_units = (equity * risk_pct) / stop_distance
    cap_units = (equity * max_notional_pct) / price
    units = max(0.0, min(risk_units, cap_units))
    return units, (units * stop_distance) / equity


def evaluate(read: ChartRead, equity: float, cfg: SignalConfig | None = None,
             has_position: bool = False) -> Signal:
    """Decide what to do, given what the chart says."""
    cfg = cfg or SignalConfig()

    if has_position:
        return Signal("flat", 0, ["already in a position"], blocked="in position")

    if read.vol_pct > cfg.max_vol_pct:
        return Signal("flat", 0, [], blocked=f"volatility too high ({read.vol_pct*100:.2f}%)")
    if read.vol_pct < cfg.min_vol_pct:
        return Signal("flat", 0, [], blocked=f"volatility too low ({read.vol_pct*100:.2f}%)")

    stop_dist = read.vol_pct * read.price * cfg.stop_atr
    target_dist = read.vol_pct * read.price * cfg.target_atr
    if stop_dist <= 0:
        return Signal("flat", 0, [], blocked="zero stop distance")

    if cfg.mode in ("trend", "both"):
        s = _trend(read, equity, cfg, stop_dist, target_dist)
        if s.tradeable or s.blocked:
            return s

    if cfg.mode in ("range", "both"):
        return _range(read, equity, cfg, stop_dist, target_dist)

    return Signal("flat", 0, ["no setup"])


def _trend(read: ChartRead, equity: float, cfg: SignalConfig,
           stop_dist: float, target_dist: float) -> Signal:
    """Buy pullbacks inside an uptrend; mirror for downtrends."""
    if read.trend == "flat":
        return Signal("flat", 0, [f"no trend ({read.trend_strength:+.2f})"])

    up = read.trend == "up"
    reasons: list[str] = []
    conf = 0

    # 1. the trend itself
    conf += 1
    reasons.append(f"{read.trend}-trend {read.trend_strength:+.2f}")

    # 2. structure must agree, or we do not take it
    want = "higher" if up else "lower"
    if read.structure == want:
        conf += 1
        reasons.append(f"{read.structure} highs/lows")

    # 3. momentum confirms but must NOT be exhausted
    if up:
        if 45 <= read.momentum <= 68:
            conf += 1
            reasons.append(f"rsi {read.momentum:.0f} has room")
        elif read.momentum > 75:
            reasons.append(f"rsi {read.momentum:.0f} overbought - skipped")
            return Signal("flat", conf, reasons)
    else:
        if 32 <= read.momentum <= 55:
            conf += 1
            reasons.append(f"rsi {read.momentum:.0f} has room")
        elif read.momentum < 25:
            reasons.append(f"rsi {read.momentum:.0f} oversold - skipped")
            return Signal("flat", conf, reasons)

    # 4. pullback: entering at the extreme of the range is buying the top
    if up and read.range_pos < 0.7:
        conf += 1
        reasons.append(f"pullback (pos {read.range_pos:.2f})")
    elif not up and read.range_pos > 0.3:
        conf += 1
        reasons.append(f"pullback (pos {read.range_pos:.2f})")
    else:
        reasons.append(f"at range extreme (pos {read.range_pos:.2f}) - skipped")
        return Signal("flat", conf, reasons)

    # 5. volume should not be collapsing under the move
    if read.rel_volume >= 0.8:
        conf += 1
        reasons.append(f"volume {read.rel_volume:.2f}x")

    # 6. breaking out in the wrong direction voids the setup
    if (up and read.breakout == "down") or (not up and read.breakout == "up"):
        return Signal("flat", conf, reasons + ["breaking out against the trend"])

    if conf < cfg.min_confidence:
        return Signal("flat", conf, reasons + [f"only {conf} reads agree"])

    if not up and not cfg.allow_short:
        return Signal("flat", conf, reasons + ["shorts disabled"])

    price = read.price
    size, eff_risk = _position_size(equity, price, stop_dist, cfg.risk_pct,
                                    cfg.max_notional_pct)
    if eff_risk < cfg.risk_pct * 0.5:
        reasons.append(f"capped by capital: {eff_risk*100:.2f}% risk not {cfg.risk_pct*100:.1f}%")
    if up:
        return Signal("buy", conf, reasons, stop=price - stop_dist,
                      target=price + target_dist, size=size, mode="trend")
    return Signal("sell", conf, reasons, stop=price + stop_dist,
                  target=price - target_dist, size=size, mode="trend")


def _range(read: ChartRead, equity: float, cfg: SignalConfig,
           stop_dist: float, target_dist: float) -> Signal:
    """Fade the edges of a quiet range, at half size."""
    if read.trend != "flat":
        return Signal("flat", 0, ["range mode but a trend is present"])

    price = read.price
    size, _ = _position_size(equity, price, stop_dist, cfg.risk_pct * 0.5,
                             cfg.max_notional_pct)

    if read.range_pos < 0.15 and read.momentum < 40:
        return Signal("buy", 3,
                       [f"bottom of range ({read.range_pos:.2f})",
                        f"rsi {read.momentum:.0f}"],
                       stop=price - stop_dist, target=price + target_dist,
                       size=size, mode="range")
    if read.range_pos > 0.85 and read.momentum > 60 and cfg.allow_short:
        return Signal("sell", 3,
                      [f"top of range ({read.range_pos:.2f})",
                       f"rsi {read.momentum:.0f}"],
                      stop=price + stop_dist, target=price - target_dist,
                      size=size, mode="range")
    return Signal("flat", 0, [f"middle of range ({read.range_pos:.2f})"])
