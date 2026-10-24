"""Where orders actually go.

Three brokers, in increasing order of how much they can hurt you:

    PaperBroker    nothing leaves the process
    BinanceBroker  real HTTP, real money, requires keys and an explicit flag

The live path is gated four ways: an API key must be in the environment, the
caller must pass `allow_live=True`, the daily loss limit must not be breached, and
every order is checked against a notional cap before it is signed. Testnet is the
default when a key exists, so a misconfiguration sends orders to a fake exchange
rather than to your account.

A broker that can only lose you 2% of your equity per trade and stops after 5% in
a day is not a limitation. It is the difference between a bad week and a story
you tell later.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import time
import urllib.parse
from dataclasses import dataclass, field

import httpx

from .data import SPOT, TESTNET


class BrokerError(RuntimeError):
    pass


@dataclass
class Fill:
    ts: float
    symbol: str
    side: str          # "buy" | "sell"
    qty: float
    price: float
    fee: float
    reason: str = ""

    @property
    def notional(self) -> float:
        return self.qty * self.price


@dataclass
class Position:
    symbol: str = ""
    qty: float = 0.0
    entry: float = 0.0
    stop: float = 0.0
    target: float = 0.0
    opened_ts: float = 0.0

    @property
    def is_open(self) -> bool:
        return abs(self.qty) > 1e-12

    @property
    def side(self) -> str:
        return "long" if self.qty > 0 else "short"


@dataclass
class RiskLimits:
    max_notional_pct: float = 0.5    # never hold more than 50% of equity in one go
    max_daily_loss_pct: float = 0.05
    max_trades_per_day: int = 20


class Broker:
    """Common bookkeeping. Subclasses implement `_execute`."""

    def __init__(self, equity: float, fee_pct: float = 0.001,
                 limits: RiskLimits | None = None) -> None:
        self.start_equity = equity
        self.cash = equity
        self.position = Position()
        self.fills: list[Fill] = []
        self.fee_pct = fee_pct
        self.limits = limits or RiskLimits()
        self.realised = 0.0
        self._day = None
        self.last_mark: float | None = None
        self._day_start_equity = equity
        self._trades_today = 0

    # -- risk ---------------------------------------------------------------
    def begin_bar(self, ts: float, mark: float | None = None) -> None:
        """Advance the calendar to `ts`. Safe to call as often as you like.

        This exists because *not trading* must still roll the day. The daily-loss
        check compares equity against the day's opening equity, and that opening
        value used to be refreshed only inside the order methods. So a bar that
        tripped the limit could never roll the day that tripped it, and the bot
        stayed halted forever -- one bad day, dead for good. Call this at the top
        of every bar, before asking `halted()` anything.
        """
        if mark is not None:
            self.last_mark = mark
        day = int(ts // 86400)
        if self._day != day:
            self._day = day
            self._day_start_equity = self.equity(self.last_mark)
            self._trades_today = 0

    def mark_to(self, price: float) -> None:
        """Value the book at `price`. `daily_pnl` is meaningless without it.

        An open position used to be marked at its *entry* price whenever no mark
        was supplied, so the daily loss check compared the entry value against
        the day's open -- a number driven by where you bought, not by what the
        market did. On a crash day it reported an 8.6% loss on a bar that had
        not moved.
        """
        self.last_mark = price

    # kept for callers that predate the public name
    def _roll_day(self, ts: float) -> None:
        self.begin_bar(ts)

    def daily_pnl(self) -> float:
        return self.equity() - self._day_start_equity

    def halted(self) -> str:
        """Non-empty string means stop trading, with the reason."""
        if self.limits.max_daily_loss_pct > 0:
            dd = self.daily_pnl() / max(self._day_start_equity, 1e-9)
            if dd <= -self.limits.max_daily_loss_pct:
                return f"daily loss limit hit ({dd*100:.1f}%)"
        if self._trades_today >= self.limits.max_trades_per_day:
            return f"trade cap reached ({self.limits.max_trades_per_day}/day)"
        return ""

    def equity(self, mark: float | None = None) -> float:
        if not self.position.is_open:
            return self.cash
        p = mark if mark is not None else (self.last_mark or self.position.entry)
        return self.cash + self.position.qty * p

    # -- orders -------------------------------------------------------------
    def _check_notional(self, qty: float, price: float) -> None:
        notional = abs(qty) * price
        cap = self.equity() * self.limits.max_notional_pct
        if notional > cap * (1.0 + 1e-9):  # exact-cap orders are fine
            raise BrokerError(
                f"order notional ${notional:,.2f} exceeds cap ${cap:,.2f} "
                f"({self.limits.max_notional_pct:.0%} of equity)"
            )

    def open_position(self, side: str, qty: float, price: float, stop: float,
                      target: float, ts: float | None = None, reason: str = "") -> Fill:
        ts = ts if ts is not None else time.time()
        self._roll_day(ts)
        h = self.halted()
        if h:
            raise BrokerError(f"trading halted: {h}")
        if qty <= 0:
            raise BrokerError("quantity must be positive")
        self._check_notional(qty, price)
        signed = qty if side == "buy" else -qty
        fill = self._execute("buy" if signed > 0 else "sell", qty, price, reason)
        self.position = Position(
            symbol=fill.symbol, qty=signed, entry=fill.price,
            stop=stop, target=target, opened_ts=ts,
        )
        self._trades_today += 1
        return fill

    def add_to_position(self, qty: float, price: float, ts: float | None = None,
                        reason: str = "") -> Fill:
        """Buy (+qty) or sell (-qty), adjusting an existing position.

        `open_position` REPLACES the position, which is right for a one-shot
        entry and catastrophic for a rebalancing strategy: adding 0.1 BTC to an
        existing 0.5 BTC left you holding 0.1 while still having paid for 0.6.
        That bug turned a +40%/yr strategy into a -47%/yr one in the live
        runner, which is exactly the kind of thing a backtest cannot catch
        because the backtest never calls the broker.
        """
        ts = ts if ts is not None else time.time()
        self._roll_day(ts)
        h = self.halted()
        if h:
            raise BrokerError(f"trading halted: {h}")
        if abs(qty) < 1e-12:
            raise BrokerError("quantity must be non-zero")
        self._check_notional(abs(qty), price)

        pos = self.position
        same_dir = (pos.qty == 0) or ((pos.qty > 0) == (qty > 0))
        fill = self._execute("buy" if qty > 0 else "sell", abs(qty), price, reason)

        new_qty = pos.qty + qty
        if same_dir:
            total = abs(pos.qty) * pos.entry + abs(qty) * fill.price
            entry = total / (abs(pos.qty) + abs(qty))
            self.position = Position(symbol=fill.symbol, qty=new_qty, entry=entry,
                                     stop=pos.stop, target=pos.target,
                                     opened_ts=pos.opened_ts or ts)
        else:
            # Reducing. Realise PnL on the part that was closed -- including the
            # case where the position goes exactly flat, which an earlier
            # version handled in its own branch and forgot to account for.
            closed = min(abs(pos.qty), abs(qty))
            self.realised += ((fill.price - pos.entry) * closed
                              * (1 if pos.qty > 0 else -1)) - fill.fee
            if abs(new_qty) < 1e-12:
                self.position = Position()
            else:
                self.position = Position(symbol=fill.symbol, qty=new_qty,
                                         entry=pos.entry, stop=pos.stop,
                                         target=pos.target,
                                         opened_ts=pos.opened_ts or ts)
        self._trades_today += 1
        return fill

    def close_position(self, price: float, ts: float | None = None,
                       reason: str = "") -> Fill | None:
        if not self.position.is_open:
            return None
        pos = self.position
        side = "sell" if pos.qty > 0 else "buy"
        fill = self._execute(side, abs(pos.qty), price, reason)
        pnl = (fill.price - pos.entry) * pos.qty - fill.fee
        self.realised += pnl
        self.position = Position()
        return fill

    def _execute(self, side: str, qty: float, price: float, reason: str) -> Fill:
        raise NotImplementedError


class PaperBroker(Broker):
    """Fills at the given price plus a fee. Nothing leaves the process."""

    def __init__(self, equity: float, fee_pct: float = 0.001,
                 limits: RiskLimits | None = None, slippage_pct: float = 0.0002) -> None:
        super().__init__(equity, fee_pct, limits)
        self.slippage_pct = slippage_pct

    def _execute(self, side: str, qty: float, price: float, reason: str) -> Fill:
        slip = price * self.slippage_pct * (1 if side == "buy" else -1)
        px = price + slip
        fee = px * qty * self.fee_pct
        if side == "buy":
            self.cash -= qty * px + fee
        else:
            self.cash += qty * px - fee
        f = Fill(ts=time.time(), symbol="PAPER", side=side, qty=qty,
                 price=px, fee=fee, reason=reason)
        self.fills.append(f)
        return f


class BinanceBroker(Broker):
    """Real orders. Requires BINANCE_API_KEY / BINANCE_API_SECRET.

    Use `testnet=True` (the default) until the strategy has earned otherwise.
    """

    def __init__(self, equity: float, symbol: str = "BTCUSDT",
                 fee_pct: float = 0.001, limits: RiskLimits | None = None,
                 testnet: bool = True, allow_live: bool = False,
                 timeout: float = 15.0) -> None:
        super().__init__(equity, fee_pct, limits)
        self.symbol = symbol
        self.base = TESTNET if testnet else SPOT
        self.testnet = testnet
        self.allow_live = allow_live
        self.timeout = timeout
        self.key = os.environ.get("BINANCE_API_KEY", "")
        self.secret = os.environ.get("BINANCE_API_SECRET", "")
        self._validate()

    def _validate(self) -> None:
        if not self.key or not self.secret:
            raise BrokerError(
                "BINANCE_API_KEY / BINANCE_API_SECRET are not set. "
                "Export them, or run in paper mode."
            )
        if not self.testnet and not self.allow_live:
            raise BrokerError(
                "refusing to send real orders without allow_live=True. "
                "This is deliberate: set testnet=True to rehearse, or pass "
                "--i-understand-this-can-lose-money to go live."
            )

    def _sign(self, params: dict) -> str:
        params = dict(params)
        params["timestamp"] = int(time.time() * 1000)
        params["recvWindow"] = 5000
        query = urllib.parse.urlencode(params)
        sig = hmac.new(self.secret.encode(), query.encode(), hashlib.sha256).hexdigest()
        return f"{query}&signature={sig}"

    def _post(self, path: str, params: dict) -> dict:
        qs = self._sign(params)
        with httpx.Client(timeout=self.timeout) as c:
            r = c.post(f"{self.base}{path}", content=qs,
                       headers={"X-MBX-APIKEY": self.key})
        if r.status_code >= 400:
            raise BrokerError(f"binance {r.status_code}: {r.text[:300]}")
        return r.json()

    def _execute(self, side: str, qty: float, price: float, reason: str) -> Fill:
        self._check_notional(qty, price)
        data = self._post("/order", {
            "symbol": self.symbol,
            "side": side.upper(),
            "type": "MARKET",
            "quantity": f"{qty:.6f}",
        })
        fills = data.get("fills") or []
        avg = (sum(float(f["price"]) * float(f["qty"]) for f in fills)
               / sum(float(f["qty"]) for f in fills)) if fills else price
        fee = sum(float(f.get("commission", 0)) for f in fills)
        f = Fill(ts=time.time(), symbol=self.symbol, side=side, qty=qty,
                 price=avg, fee=fee, reason=reason)
        self.fills.append(f)
        return f
