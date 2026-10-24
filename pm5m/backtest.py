"""Replay recorded sessions through the engine.

This is the only backtest worth running: it uses the spot prints you actually
recorded, at the timestamps they arrived, and the book levels you actually saw.
There is no lookahead because the engine's settlement index only ever consumes
ticks at or before the query time, and the book used for a decision is the most
recent one received before it.

Known limitation, stated up front: recordings store best bid/ask and top-of-book
size, not full depth. Fills therefore walk a single level, which is optimistic
for large sizes. Keep `max_book_fraction` low and size small, or record full
depth if you intend to trade size.
"""

from __future__ import annotations

from dataclasses import dataclass

from .broker import PaperBroker
from .config import Settings
from .engine import Engine
from .risk import RiskManager
from .storage import Store
from .types import Book, Level, Market
from .vol import SpotBuffer


@dataclass
class BacktestResult:
    windows: int
    trades: int
    wins: int
    losses: int
    pnl: float
    fees: float
    staked: float

    @property
    def roi(self) -> float:
        return self.pnl / self.staked if self.staked else 0.0

    def pretty(self) -> str:
        return "\n".join(
            [
                f"windows replayed  : {self.windows}",
                f"trades            : {self.trades}",
                f"outcome           : {self.wins}W / {self.losses}L"
                + (f"  ({self.wins / self.trades:.1%})" if self.trades else ""),
                f"total staked      : ${self.staked:,.2f}",
                f"fees paid         : ${self.fees:,.2f}",
                f"net pnl           : ${self.pnl:,.2f}",
                f"roi on stake      : {self.roi:+.2%}",
            ]
        )


def replay(store: Store, settings: Settings, max_windows: int = 500) -> BacktestResult:
    risk = RiskManager(settings=settings)
    broker = PaperBroker(settings=settings, risk=risk)
    engine = Engine(
        settings=settings, broker=broker, risk=risk, spot_buffer=SpotBuffer()
    )

    rows = store.conn.execute(
        "SELECT * FROM markets WHERE start_ts IS NOT NULL ORDER BY start_ts LIMIT ?",
        (max_windows,),
    ).fetchall()
    cols = [c[0] for c in store.conn.execute("SELECT * FROM markets LIMIT 0").description]
    markets = [dict(zip(cols, r)) for r in rows]

    windows = 0
    for m in markets:
        start, end = m["start_ts"], m["end_ts"]
        if not start or not end:
            continue
        ticks = store.spot_window(start - 1, end)
        if len(ticks) < 10:
            continue
        mk = Market(
            slug=m["slug"],
            condition_id=m["condition_id"] or "",
            asset=m["asset"] or settings.asset,
            start_ts=start,
            end_ts=end,
            token_up=m["token_up"],
            token_down=m["token_down"],
            open_price=m["open_price"],
        )
        # Prime the vol buffer with the preceding lookback window.
        for ts, price in store.spot_window(start - settings.vol_lookback_s, start):
            engine.spot_buffer.add(ts, price)
        engine.on_market(mk)

        books = {
            mk.token_up: store.book_series(mk.slug, mk.token_up),
            mk.token_down: store.book_series(mk.slug, mk.token_down),
        }
        events: list[tuple[float, str, object]] = []
        for ts, price in ticks:
            events.append((ts, "spot", price))
        for token, series in books.items():
            for b in series:
                if b["best_bid"] is None and b["best_ask"] is None:
                    continue
                events.append((b["ts"], "book", (token, b)))
        events.sort(key=lambda e: e[0])

        for ts, kind, payload in events:
            if kind == "spot":
                engine.on_spot(ts, float(payload))
            else:
                token, b = payload
                book = Book(
                    token_id=token,
                    ts=float(b["ts"]),
                    bids=(Level(float(b["best_bid"]), float(b["bid_size"] or 0)),)
                    if b["best_bid"] is not None
                    else (),
                    asks=(Level(float(b["best_ask"]), float(b["ask_size"] or 0)),)
                    if b["best_ask"] is not None
                    else (),
                )
                engine.on_book(book, "up" if token == mk.token_up else "down", mk.slug)
        engine.tick(end + 1e-6)
        windows += 1

    return BacktestResult(
        windows=windows,
        trades=broker.n_trades,
        wins=broker.n_wins,
        losses=broker.n_losses,
        pnl=broker.realized_pnl,
        fees=broker.fees_paid,
        staked=sum(f.notional + f.fee for f in broker.fills),
    )
