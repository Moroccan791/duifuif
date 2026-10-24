"""SQLite persistence.

One file, WAL mode, no ORM. Writes are batched through a single connection so a
busy recorder does not serialise on fsync every tick. Every table is append-only
except `markets.outcome`, because a research log you can rewrite is not evidence.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from typing import Any, Iterable

SCHEMA = """
CREATE TABLE IF NOT EXISTS markets (
    slug TEXT PRIMARY KEY,
    condition_id TEXT,
    asset TEXT,
    start_ts REAL,
    end_ts REAL,
    token_up TEXT,
    token_down TEXT,
    open_price REAL,
    outcome TEXT,
    avg_price REAL,
    resolved_ts REAL
);

CREATE TABLE IF NOT EXISTS spot (
    ts REAL, price REAL, venue TEXT
);
CREATE INDEX IF NOT EXISTS idx_spot_ts ON spot(ts);

CREATE TABLE IF NOT EXISTS books (
    ts REAL, slug TEXT, token_id TEXT, side TEXT,
    best_bid REAL, best_ask REAL, bid_size REAL, ask_size REAL
);
CREATE INDEX IF NOT EXISTS idx_books_ts ON books(ts);

CREATE TABLE IF NOT EXISTS signals (
    ts REAL, slug TEXT, side TEXT, p_true REAL, edge REAL, ask REAL,
    z REAL, remaining_s REAL, sigma REAL, allowed INTEGER, reason TEXT
);
CREATE INDEX IF NOT EXISTS idx_signals_ts ON signals(ts);

CREATE TABLE IF NOT EXISTS fills (
    ts REAL, slug TEXT, token_id TEXT, side TEXT, shares REAL,
    price REAL, fee REAL, notional REAL, reason TEXT
);

CREATE TABLE IF NOT EXISTS trades (
    ts REAL, slug TEXT, side TEXT, price REAL, shares REAL,
    fee REAL, won INTEGER, pnl REAL
);
CREATE INDEX IF NOT EXISTS idx_trades_ts ON trades(ts);

CREATE TABLE IF NOT EXISTS settlements (
    ts REAL, slug TEXT, outcome TEXT, open_price REAL, avg_price REAL,
    pnl REAL, model_p_up REAL
);
"""


class Store:
    def __init__(self, path: str) -> None:
        self.path = path
        parent = os.path.dirname(os.path.abspath(path))
        if parent:
            os.makedirs(parent, exist_ok=True)
        self.conn = sqlite3.connect(path, timeout=30.0)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    # ---- markets ---------------------------------------------------------
    def upsert_market(self, m) -> None:
        self.conn.execute(
            """INSERT INTO markets (slug, condition_id, asset, start_ts, end_ts,
                   token_up, token_down, open_price)
               VALUES (?,?,?,?,?,?,?,?)
               ON CONFLICT(slug) DO UPDATE SET
                   open_price=COALESCE(markets.open_price, excluded.open_price)""",
            (
                m.slug,
                m.condition_id,
                m.asset,
                m.start_ts,
                m.end_ts,
                m.token_up,
                m.token_down,
                m.open_price,
            ),
        )
        self.conn.commit()

    def resolve_market(self, slug: str, outcome: str, avg_price: float | None, ts: float) -> None:
        self.conn.execute(
            "UPDATE markets SET outcome=?, avg_price=?, resolved_ts=? WHERE slug=?",
            (outcome, avg_price, ts, slug),
        )
        self.conn.commit()

    def market(self, slug: str) -> dict[str, Any] | None:
        cur = self.conn.execute("SELECT * FROM markets WHERE slug=?", (slug,))
        row = cur.fetchone()
        if row is None:
            return None
        cols = [c[0] for c in cur.description]
        return dict(zip(cols, row))

    def unresolved_markets(self) -> list[dict[str, Any]]:
        cur = self.conn.execute(
            "SELECT * FROM markets WHERE outcome IS NULL ORDER BY start_ts"
        )
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]

    # ---- time series -----------------------------------------------------
    def add_spot(self, rows: Iterable[tuple[float, float, str]]) -> None:
        self.conn.executemany("INSERT INTO spot (ts, price, venue) VALUES (?,?,?)", rows)
        self.conn.commit()

    def add_book(self, ts: float, slug: str, token_id: str, side: str, book) -> None:
        self.conn.execute(
            """INSERT INTO books (ts, slug, token_id, side, best_bid, best_ask,
                   bid_size, ask_size) VALUES (?,?,?,?,?,?,?,?)""",
            (
                ts,
                slug,
                token_id,
                side,
                book.best_bid,
                book.best_ask,
                book.bid_size,
                book.ask_size,
            ),
        )
        self.conn.commit()

    def add_signal(self, ts: float, sig, allowed: bool, reason: str) -> None:
        self.conn.execute(
            """INSERT INTO signals (ts, slug, side, p_true, edge, ask, z,
                   remaining_s, sigma, allowed, reason)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (
                ts,
                sig.market_slug,
                sig.side,
                sig.p_true,
                sig.edge_per_share,
                sig.limit_price,
                sig.meta.get("z"),
                sig.meta.get("remaining_s"),
                sig.meta.get("sigma"),
                int(allowed),
                reason,
            ),
        )
        self.conn.commit()

    def add_fill(self, f) -> None:
        self.conn.execute(
            """INSERT INTO fills (ts, slug, token_id, side, shares, price, fee,
                   notional, reason) VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                f.ts,
                f.market_slug,
                f.token_id,
                f.side,
                f.shares,
                f.price,
                f.fee,
                f.notional,
                f.reason,
            ),
        )
        self.conn.commit()

    def add_settlement(
        self, ts: float, slug: str, outcome: str, open_price: float, avg: float, pnl: float, p_up: float
    ) -> None:
        self.conn.execute(
            """INSERT INTO settlements (ts, slug, outcome, open_price, avg_price,
                   pnl, model_p_up) VALUES (?,?,?,?,?,?,?)""",
            (ts, slug, outcome, open_price, avg, pnl, p_up),
        )
        self.conn.commit()

    def add_trade(self, t) -> None:
        self.conn.execute(
            """INSERT INTO trades (ts, slug, side, price, shares, fee, won, pnl)
               VALUES (?,?,?,?,?,?,?,?)""",
            (t.ts, t.slug, t.side, t.price, t.shares, t.fee, int(t.won), t.pnl),
        )
        self.conn.commit()

    # ---- queries ---------------------------------------------------------
    def spot_window(self, t0: float, t1: float, venue: str | None = None) -> list[tuple[float, float]]:
        if venue:
            cur = self.conn.execute(
                "SELECT ts, price FROM spot WHERE ts BETWEEN ? AND ? AND venue=? ORDER BY ts",
                (t0, t1, venue),
            )
        else:
            cur = self.conn.execute(
                "SELECT ts, price FROM spot WHERE ts BETWEEN ? AND ? ORDER BY ts",
                (t0, t1),
            )
        return [(float(t), float(p)) for t, p in cur.fetchall()]

    def book_series(self, slug: str, token_id: str) -> list[dict[str, Any]]:
        cur = self.conn.execute(
            "SELECT * FROM books WHERE slug=? AND token_id=? ORDER BY ts", (slug, token_id)
        )
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]

    def trade_records(self):
        """Completed trades as TradeRecord objects, for expectancy analysis."""
        from .expectancy import TradeRecord

        cur = self.conn.execute(
            "SELECT ts, slug, side, price, shares, fee, won, pnl FROM trades ORDER BY ts"
        )
        return [
            TradeRecord(
                ts=float(r[0]),
                slug=r[1],
                side=r[2],
                price=float(r[3]),
                shares=float(r[4]),
                fee=float(r[5]),
                won=bool(r[6]),
                pnl=float(r[7]),
            )
            for r in cur.fetchall()
        ]

    def settled(self) -> list[dict[str, Any]]:
        cur = self.conn.execute(
            "SELECT * FROM settlements ORDER BY ts"
        )
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]

    def fills(self) -> list[dict[str, Any]]:
        cur = self.conn.execute("SELECT * FROM fills ORDER BY ts")
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]

    def counts(self) -> dict[str, int]:
        out = {}
        for table in ("markets", "spot", "books", "signals", "fills", "trades", "settlements"):
            out[table] = self.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        return out

    def close(self) -> None:
        self.conn.close()


class JsonlLogger:
    """Append-only raw capture, for replaying a session byte for byte."""

    def __init__(self, path: str) -> None:
        self.path = path
        parent = os.path.dirname(os.path.abspath(path))
        if parent:
            os.makedirs(parent, exist_ok=True)
        self.fh = open(path, "a", encoding="utf-8")

    def write(self, kind: str, payload: Any) -> None:
        self.fh.write(
            json.dumps({"t": time.time(), "kind": kind, "payload": payload}) + "\n"
        )

    def close(self) -> None:
        self.fh.close()
