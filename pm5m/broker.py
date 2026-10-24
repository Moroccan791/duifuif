"""Paper broker: fills against a real order book, settles against the model.

Design rules that keep the paper numbers meaningful:

* Fills walk the actual displayed book, capped at `max_book_fraction`. There is
  no "fill at mid" anywhere in this file.
* Fees are charged per fill using the published formula, at the category rate.
* Settlement is binary at window end. Nothing is ever marked at a mid price and
  called profit.
* Exposure is released and PnL booked through the risk manager, so a paper
  session exercises the same limits a live one would.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .config import Settings
from .fees import taker_fee
from .risk import RiskManager
from .types import Book, Fill, Market, Position, Signal


@dataclass
class PaperBroker:
    settings: Settings
    risk: RiskManager
    positions: dict[str, Position] = field(default_factory=dict)
    fills: list[Fill] = field(default_factory=list)
    realized_pnl: float = 0.0
    fees_paid: float = 0.0
    n_wins: int = 0
    n_losses: int = 0

    def execute(self, signal: Signal, book: Book, shares: float, now: float) -> Fill | None:
        if shares <= 0:
            return None
        filled, notional = book.walk_buy(
            signal.limit_price, shares, self.settings.max_book_fraction
        )
        if filled <= 0:
            return None
        vwap = notional / filled
        fee = taker_fee(filled, vwap, self.settings.taker_fee_rate)

        existing = self.positions.get(signal.token_id)
        if existing is None:
            existing = Position(
                token_id=signal.token_id,
                market_slug=signal.market_slug,
                side=signal.side,
            )
            self.positions[signal.token_id] = existing
        existing.shares += filled
        existing.cost += notional + fee

        fill = Fill(
            ts=now,
            market_slug=signal.market_slug,
            token_id=signal.token_id,
            side=signal.side,
            shares=filled,
            price=vwap,
            fee=fee,
            notional=notional,
            reason=signal.reason,
        )
        self.fills.append(fill)
        self.fees_paid += fee
        self.risk.record_fill(now, notional + fee)
        return fill

    def settle_market(self, market: Market, outcome: str, now: float) -> float:
        """Resolve every position in `market`. Returns PnL booked."""
        total = 0.0
        for token in (market.token_up, market.token_down):
            pos = self.positions.pop(token, None)
            if pos is None or pos.shares <= 0:
                continue
            won = pos.side == outcome
            payout = pos.shares if won else 0.0
            pnl = payout - pos.cost
            total += pnl
            self.realized_pnl += pnl
            if won:
                self.n_wins += 1
            else:
                self.n_losses += 1
            self.risk.record_close(now, pnl, pos.cost)
        return total

    # ---- reporting -------------------------------------------------------
    @property
    def exposure_usd(self) -> float:
        return sum(p.cost for p in self.positions.values())

    @property
    def n_trades(self) -> int:
        return len(self.fills)

    @property
    def win_rate(self) -> float:
        n = self.n_wins + self.n_losses
        return self.n_wins / n if n else 0.0

    def summary(self) -> dict[str, float | int]:
        return {
            "trades": self.n_trades,
            "settled_wins": self.n_wins,
            "settled_losses": self.n_losses,
            "win_rate": round(self.win_rate, 4),
            "realized_pnl": round(self.realized_pnl, 4),
            "fees_paid": round(self.fees_paid, 4),
            "open_exposure": round(self.exposure_usd, 2),
            "open_positions": len(self.positions),
        }
