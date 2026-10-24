"""The engine: a pure, synchronous state machine.

No network, no asyncio, no globals. Feeds push events in; the engine decides
what, if anything, to do; a broker records the result. That separation is what
makes the whole thing testable, and tests are the only reason to believe any
number it prints.

Event contract:
    on_market(market)      a new 5-minute window is known
    on_spot(ts, price)     a spot print arrived
    on_book(book, side)    a book update for a token arrived
    tick(now)              housekeeping: settle finished windows, discover next

The engine will not trade a window it did not observe from (or nearly from) its
open, because the settlement index needs the whole path. A late join means no
trade for that window -- correct behaviour, and the most common reason a bot
appears to "do nothing".
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from .broker import PaperBroker
from .config import Settings
from .maker import Quote, plan_quotes, quote_is_filled
from .model import fair_up
from .risk import RiskManager
from .settlement import SettlementIndex
from .storage import Store
from .strategy import evaluate, size_signal
from .types import Book, Market, Signal


@dataclass
class WindowState:
    market: Market
    index: SettlementIndex
    books: dict[str, Book] = field(default_factory=dict)
    last_eval: float = 0.0
    last_eval_key: tuple = ()
    last_p_up: float | None = None
    mid_p_up: float | None = None
    traded: bool = False
    quotes: dict[str, Quote] = field(default_factory=dict)
    last_quote_ts: float = 0.0


@dataclass
class Engine:
    settings: Settings
    broker: PaperBroker
    risk: RiskManager
    store: Store | None = None
    spot_buffer: object | None = None
    sigma_override: float | None = None
    windows: dict[str, WindowState] = field(default_factory=dict)
    last_spot: tuple[float, float] | None = None
    signals_seen: int = 0
    signals_taken: int = 0
    log: list[str] = field(default_factory=list)

    # ---- helpers ---------------------------------------------------------
    def _note(self, msg: str) -> None:
        self.log.append(msg)
        if len(self.log) > 500:
            del self.log[:100]

    def _sigma(self) -> float:
        if self.sigma_override is not None:
            return self.sigma_override
        if self.spot_buffer is not None:
            from .vol import realized_sigma

            return realized_sigma(
                self.spot_buffer.samples,
                self.settings.vol_lookback_s,
                self.settings.vol_fallback_per_sqrt_s,
            )
        return self.settings.vol_fallback_per_sqrt_s

    def active_window(self, now: float) -> WindowState | None:
        for ws in self.windows.values():
            if ws.market.start_ts <= now < ws.market.end_ts:
                return ws
        return None

    # ---- events ----------------------------------------------------------
    def on_market(self, market: Market) -> WindowState:
        ws = self.windows.get(market.slug)
        if ws is not None:
            return ws
        open_price = market.open_price
        if open_price is None and self.last_spot is not None:
            open_price = self.last_spot[1]
        index = SettlementIndex(
            start_ts=market.start_ts,
            end_ts=market.end_ts,
            open_price=open_price or 0.0,
        )
        ws = WindowState(market=market, index=index)
        self.windows[market.slug] = ws
        if self.store:
            self.store.upsert_market(market)
        self._note(f"window open {market.slug} open={open_price}")
        return ws

    def on_spot(self, ts: float, price: float) -> None:
        self.last_spot = (ts, price)
        if self.spot_buffer is not None:
            self.spot_buffer.add(ts, price)
        for ws in self.windows.values():
            if ts < ws.market.start_ts:
                continue
            if ws.index.open_price <= 0:
                ws.index.open_price = price
            ws.index.add(ts, price)

    def on_book(self, book: Book, side: str, slug: str | None = None) -> None:
        target = None
        if slug is not None:
            target = self.windows.get(slug)
        if target is None:
            for ws in self.windows.values():
                if book.token_id in (ws.market.token_up, ws.market.token_down):
                    target = ws
                    break
        if target is None:
            return
        target.books[book.token_id] = book
        if self.store:
            self.store.add_book(book.ts, target.market.slug, book.token_id, side, book)
        self._maybe_trade(target, book.ts)

    # ---- decision --------------------------------------------------------
    def _maybe_trade(self, ws: WindowState, now: float) -> None:
        if ws.traded:
            return
        if self.last_spot is None or ws.index.open_price <= 0:
            return
        books = {
            "up": ws.books.get(ws.market.token_up),
            "down": ws.books.get(ws.market.token_down),
        }
        books = {k: v for k, v in books.items() if v is not None}
        if not books:
            return
        if any(now - b.ts > 5.0 for b in books.values()):
            return  # stale quote: do not decide on yesterday's book
        # Skip only when the inputs are literally unchanged. A wall-clock
        # throttle looks equivalent but is not: the second side's book usually
        # arrives at the same instant as the first, and a timer would swallow
        # exactly the update that completes the picture.
        key = tuple(sorted((side, b.ts) for side, b in books.items()))
        if key == ws.last_eval_key:
            return  # no new information since the last decision
        ws.last_eval_key = key
        ws.last_eval = now

        sigma = self._sigma()
        spot = self.last_spot[1]
        view = fair_up(ws.index, spot, sigma, now, self.settings.drift_per_s, self.settings.z_cap)
        ws.last_p_up = view.p_up
        if ws.mid_p_up is None and ws.index.remaining_tau(now) <= ws.market.duration / 2:
            ws.mid_p_up = view.p_up

        if self.settings.strategy_mode == "maker":
            self._maker_step(ws, books, spot, sigma, now)
            return

        signals = evaluate(
            ws.market, ws.index, books, spot, sigma, now, self.settings
        )
        self.signals_seen += len(signals)
        if not signals:
            return
        sig = signals[0]

        decision = self.risk.check(sig, now)
        if not decision.allowed:
            if self.store:
                self.store.add_signal(now, sig, False, decision.reason)
            self._note(f"veto {ws.market.slug} {sig.side}: {decision.reason}")
            return

        side_book = books.get(sig.side)
        if side_book is None:
            return
        shares, why = size_signal(
            sig, side_book, self.settings, self.settings.bankroll_usd, self.risk.exposure_usd
        )
        if shares <= 0:
            if self.store:
                self.store.add_signal(now, sig, False, why)
            self._note(f"no size {ws.market.slug} {sig.side}: {why}")
            return

        fill = self.broker.execute(sig, side_book, shares, now)
        if fill is None:
            return
        ws.traded = True
        self.signals_taken += 1
        if self.store:
            self.store.add_signal(now, sig, True, why)
            self.store.add_fill(fill)
        self._note(
            f"BUY {sig.side.upper()} {fill.shares:.2f}@{fill.price:.3f} "
            f"fee={fill.fee:.3f} {ws.market.slug}"
        )

    def _maker_step(self, ws: WindowState, books: dict[str, Book], spot: float, sigma: float, now: float) -> None:
        """Rest quotes, and check whether any resting quote was hit.

        Fill check runs before re-quoting: a quote that was live when the last
        book update arrived may already be filled by this one, and cancel/replace
        logic that ignores that will silently under-report fills.
        """
        for token in list(ws.quotes):
            q = ws.quotes[token]
            book = books.get(q.side)
            if book is None or not quote_is_filled(q, book):
                continue
            ws.quotes.pop(token, None)
            sig = Signal(
                market_slug=ws.market.slug,
                token_id=q.token_id,
                side=q.side,
                limit_price=q.price,
                p_true=q.fair,
                edge_per_share=q.edge,
                reason=(
                    f"maker fill {q.side} @{q.price:.3f} fair={q.fair:.4f} "
                    f"edge={q.edge:.4f} ask={book.best_ask:.3f}"
                ),
                meta={"cost_per_share": q.price, "remaining_s": ws.index.remaining_tau(now)},
            )
            decision = self.risk.check(sig, now)
            if not decision.allowed:
                if self.store:
                    self.store.add_signal(now, sig, False, decision.reason)
                self._note(f"maker veto {ws.market.slug} {sig.side}: {decision.reason}")
                continue
            fill = self.broker.execute(sig, book, q.shares, now, as_maker=True)
            if fill is None:
                continue
            ws.traded = True
            self.signals_taken += 1
            if self.store:
                self.store.add_signal(now, sig, True, "maker fill")
                self.store.add_fill(fill)
            self._note(
                f"MAKER BUY {sig.side.upper()} {fill.shares:.2f}@{fill.price:.3f} "
                f"fee=0 {ws.market.slug}"
            )

        if ws.traded or now - ws.last_quote_ts < self.settings.maker_requote_s:
            return
        ws.last_quote_ts = now
        held = {p.token_id for p in self.broker.positions.values()}
        ws.quotes = {
            q.token_id: q
            for q in plan_quotes(
                ws.market,
                ws.index,
                books,
                spot,
                sigma,
                now,
                self.settings,
                self.risk,
                held,
            )
        }

    # ---- housekeeping ----------------------------------------------------
    def tick(self, now: float, outcome_fn=None) -> None:
        """Settle any window whose end has passed.

        `outcome_fn(WindowState) -> "up"|"down"` lets a caller inject the
        ground-truth resolution (used by the simulator, which knows it) instead
        of inferring it from the observed path. Live trading has no such luxury:
        the index plus a Gamma confirmation is all you get.
        """
        for slug in list(self.windows):
            ws = self.windows[slug]
            if now < ws.market.end_ts:
                continue
            if ws.index.open_price <= 0 or not ws.index._pts:
                # Never observed the path: cannot settle honestly. Drop it.
                self.windows.pop(slug)
                continue
            if outcome_fn is not None:
                outcome = outcome_fn(ws)
            else:
                outcome = ws.index.resolved_outcome()
            avg = ws.index.realized_average(ws.market.end_ts) or 0.0
            before = len(self.broker.closed)
            pnl = self.broker.settle_market(ws.market, outcome, ws.market.end_ts)
            if self.store:
                for trade in self.broker.closed[before:]:
                    self.store.add_trade(trade)
            if self.store:
                self.store.resolve_market(slug, outcome, avg, now)
                self.store.add_settlement(
                    ws.market.end_ts,
                    slug,
                    outcome,
                    ws.index.open_price,
                    avg,
                    pnl,
                    ws.last_p_up if ws.last_p_up is not None else -1.0,
                )
            self._note(
                f"settle {slug} -> {outcome} avg={avg:.2f} open={ws.index.open_price:.2f} pnl={pnl:+.2f}"
            )
            self.windows.pop(slug)

    def report(self) -> dict[str, object]:
        out: dict[str, object] = dict(self.broker.summary())
        out.update(
            {
                "windows_open": len(self.windows),
                "signals_seen": self.signals_seen,
                "signals_taken": self.signals_taken,
                "sigma": round(self._sigma(), 8),
                "now": time.time(),
            }
        )
        return out
