"""Risk gate. Every order passes through here or it does not happen.

The check order matters: kill switch and daily loss first (capital protection),
then exposure (position limits), then rate (behavioural limits), then price
(per-trade sanity). A rejection is never silent -- the reason is returned and
logged so you can see *why* the bot sat on its hands.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field

from .config import Settings
from .types import Signal


@dataclass
class RiskDecision:
    allowed: bool
    reason: str = ""


@dataclass
class RiskManager:
    settings: Settings
    realized_pnl_today: float = 0.0
    exposure_usd: float = 0.0
    trades: list[float] = field(default_factory=list)
    last_loss_ts: float = 0.0
    day_stamp: str = ""
    halt_reason: str = ""

    def _kill_switch_on(self) -> bool:
        path = self.settings.kill_switch_path
        return bool(path) and os.path.exists(path)

    def roll_day(self, now: float) -> None:
        stamp = time.strftime("%Y-%m-%d", time.gmtime(now))
        if stamp != self.day_stamp:
            self.day_stamp = stamp
            self.realized_pnl_today = 0.0

    def trades_in_last_hour(self, now: float) -> int:
        cutoff = now - 3600.0
        self.trades = [t for t in self.trades if t >= cutoff]
        return len(self.trades)

    def check(self, signal: Signal, now: float) -> RiskDecision:
        self.roll_day(now)

        if self._kill_switch_on():
            self.halt_reason = "kill switch file present"
            return RiskDecision(False, "kill switch file present")
        if self.halt_reason:
            return RiskDecision(False, f"halted: {self.halt_reason}")
        if self.realized_pnl_today <= -abs(self.settings.max_daily_loss_usd):
            self.halt_reason = (
                f"daily loss limit hit (${self.realized_pnl_today:.2f})"
            )
            return RiskDecision(False, self.halt_reason)
        if (
            self.last_loss_ts
            and now - self.last_loss_ts < self.settings.cooldown_after_loss_s
        ):
            left = self.settings.cooldown_after_loss_s - (now - self.last_loss_ts)
            return RiskDecision(False, f"cooldown {left:.0f}s after loss")
        if self.exposure_usd >= self.settings.max_total_exposure_usd:
            return RiskDecision(False, "max total exposure reached")
        if self.trades_in_last_hour(now) >= self.settings.max_trades_per_hour:
            return RiskDecision(False, "hourly trade cap reached")
        if not (0.0 < signal.limit_price <= self.settings.max_price):
            return RiskDecision(False, f"price {signal.limit_price:.3f} outside band")
        if signal.edge_per_share <= 0:
            return RiskDecision(False, "non-positive edge")
        return RiskDecision(True)

    def record_fill(self, now: float, notional_plus_fee: float) -> None:
        self.trades.append(now)
        self.exposure_usd += notional_plus_fee

    def record_close(self, now: float, pnl: float, cost_released: float) -> None:
        self.exposure_usd = max(0.0, self.exposure_usd - cost_released)
        self.realized_pnl_today += pnl
        if pnl < 0:
            self.last_loss_ts = now
