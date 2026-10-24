"""The loop: look at the chart, decide, act, manage the position.

One `step()` is one decision point. That is deliberate -- it makes the whole thing
replayable. A backtest calls `step()` in a for-loop over history; a live runner
calls it on a timer. Same code, so the backtest cannot silently disagree with the
bot.

Position management is separate from signal generation and runs first: a stop that
is only checked when a new signal fires is not a stop.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from .broker import Broker, BrokerError
from .data import Candle
from .reader import ChartRead, read_chart
from .signals import Signal, SignalConfig, evaluate


@dataclass
class Step:
    ts: float
    candle: Candle
    read: ChartRead
    signal: Signal
    action: str = ""
    note: str = ""


@dataclass
class RunnerConfig:
    symbol: str = "BTCUSDT"
    interval: str = "5m"
    lookback: int = 200
    equity_for_sizing: float = 100.0
    signal: SignalConfig = field(default_factory=SignalConfig)


class Runner:
    def __init__(self, broker: Broker, cfg: RunnerConfig | None = None) -> None:
        self.broker = broker
        self.cfg = cfg or RunnerConfig()
        self.history: list[Step] = []
        self.closed: list[dict] = []

    # -- position management ------------------------------------------------
    def _manage(self, price: float, ts: float) -> str:
        pos = self.broker.position
        if not pos.is_open:
            return ""
        if getattr(self, "_open_trade", None):
            self._open_trade["closed_ts"] = ts
        # Stops first: never let a winner turn into a loser while checking targets.
        if pos.qty > 0:
            if price <= pos.stop:
                self.broker.close_position(pos.stop, ts, "stop")
                self._record_close("stop")
                return "closed: stop"
            if price >= pos.target:
                self.broker.close_position(pos.target, ts, "target")
                self._record_close("target")
                return "closed: target"
        else:
            if price >= pos.stop:
                self.broker.close_position(pos.stop, ts, "stop")
                self._record_close("stop")
                return "closed: stop"
            if price <= pos.target:
                self.broker.close_position(pos.target, ts, "target")
                self._record_close("target")
                return "closed: target"
        return ""

    def _record_close(self, reason: str) -> None:
        """Record the whole trade, not just the exit, so it can be explained."""
        f = self.broker.fills[-1] if self.broker.fills else None
        entry = getattr(self, "_open_trade", {}) or {}
        exit_px = f.price if f else 0.0
        entry_px = entry.get("entry", 0.0)
        qty = entry.get("qty", 0.0)
        pnl = (exit_px - entry_px) * qty if qty else 0.0
        self.closed.append({
            "reason": reason,
            "price": exit_px,
            "entry": entry_px,
            "qty": qty,
            "side": entry.get("side", ""),
            "pnl": pnl,
            "opened_ts": entry.get("ts", 0.0),
            "closed_ts": entry.get("closed_ts", 0.0),
            "why": entry.get("why", ""),
            "stop": entry.get("stop", 0.0),
            "target": entry.get("target", 0.0),
            "realised": self.broker.realised,
        })
        self._open_trade = {}

    # -- one decision -------------------------------------------------------
    def step(self, candles: list[Candle], ts: float | None = None) -> Step:
        ts = ts if ts is not None else (candles[-1].ts if candles else time.time())
        price = candles[-1].close
        read = read_chart(candles)

        note = self._manage(price, ts)

        has_pos = self.broker.position.is_open
        sig = evaluate(read, self.broker.equity(price), self.cfg.signal,
                       has_position=has_pos)

        action = ""
        if sig.tradeable and not has_pos:
            try:
                self.broker.open_position(
                    sig.action, sig.size, price, sig.stop, sig.target,
                    ts=ts, reason="; ".join(sig.reasons[:2]),
                )
                self._open_trade = {
                    "side": sig.action, "entry": price, "qty": sig.size,
                    "stop": sig.stop, "target": sig.target, "ts": ts,
                    "why": "; ".join(sig.reasons),
                }
                action = f"opened {sig.action} {sig.size:.6f} @ {price:,.2f}"
            except BrokerError as e:
                action = f"blocked: {e}"
                sig.blocked = str(e)

        step = Step(ts=ts, candle=candles[-1], read=read, signal=sig,
                    action=action, note=note)
        self.history.append(step)
        return step


def backtest(candles: list[Candle], start_equity: float = 100.0,
             cfg: RunnerConfig | None = None, fee_pct: float = 0.001) -> dict:
    """Replay history through the exact same `Runner.step`."""
    from .broker import PaperBroker

    cfg = cfg or RunnerConfig()
    broker = PaperBroker(start_equity, fee_pct=fee_pct)
    runner = Runner(broker, cfg)

    warmup = 30
    equity_curve: list[tuple[float, float]] = []
    for i in range(warmup, len(candles)):
        window = candles[:i + 1]
        runner.step(window)
        equity_curve.append((candles[i].ts, broker.equity(candles[i].close)))

    # Close anything still open at the last price.
    if broker.position.is_open:
        broker.close_position(candles[-1].close, candles[-1].ts, "end of test")

    final = broker.equity(candles[-1].close)
    wins = sum(1 for c in runner.closed if c["reason"] == "target")
    losses = sum(1 for c in runner.closed if c["reason"] == "stop")
    peak = start_equity
    max_dd = 0.0
    for _, eq in equity_curve:
        peak = max(peak, eq)
        max_dd = max(max_dd, (peak - eq) / peak)

    return {
        "start": start_equity,
        "final": final,
        "return_pct": (final / start_equity - 1.0) * 100.0,
        "trades": len(runner.closed),
        "wins": wins,
        "losses": losses,
        "win_rate": wins / len(runner.closed) if runner.closed else 0.0,
        "max_drawdown_pct": max_dd * 100.0,
        "fills": len(broker.fills),
        "steps": len(runner.history),
        "equity_curve": equity_curve,
        "runner": runner,
    }
