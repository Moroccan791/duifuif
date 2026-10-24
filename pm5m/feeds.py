"""Venue clients: market discovery, order books, and a spot price stream.

Everything here is read-only. Nothing in this module can place an order; that
lives in `pm5m.live` and is deliberately the only place that touches a private
key.

Endpoint facts baked in here (verified Oct 2026, see README):

* Gamma discovery: `GET /events?series_slug=btc-up-or-down-5m&closed=false`
  works; `GET /markets?active=true` does NOT reliably return these markets.
* Slug is `{asset}-updown-{tf}-{unix_start}`, Unix seconds of the window OPEN.
* `clobTokenIds` comes back JSON-encoded as a string, not a list.
* Order books: `GET /clob.polymarket.com/book?token_id=...`.
* The market resolves on Chainlink's BTC/USD 60s TWAP stream.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, Iterable

import httpx

from .types import Book, Level, Market


class FeedError(RuntimeError):
    pass


def window_start(ts: float, window_s: float = 300.0) -> int:
    return int(ts // window_s) * int(window_s)


def slug_for(asset: str, tf: str, start_ts: int) -> str:
    return f"{asset}-updown-{tf}-{int(start_ts)}"


def _load_ids(raw: Any) -> list[str]:
    if isinstance(raw, str):
        return list(json.loads(raw))
    if isinstance(raw, list):
        return [str(x) for x in raw]
    return []


@dataclass
class GammaClient:
    base_url: str = "https://gamma-api.polymarket.com"
    timeout: float = 10.0

    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        with httpx.Client(timeout=self.timeout) as client:
            r = client.get(f"{self.base_url}{path}", params=params)
            if r.status_code != 200:
                raise FeedError(f"GET {path} -> {r.status_code}: {r.text[:200]}")
            return r.json()

    @staticmethod
    def _parse_event(ev: dict[str, Any]) -> Market | None:
        markets = ev.get("markets") or []
        if not markets:
            return None
        m = markets[0]
        ids = _load_ids(m.get("clobTokenIds"))
        if len(ids) < 2:
            return None
        start_raw = ev.get("eventStartTime") or ev.get("startDate")
        end_raw = ev.get("endDate") or m.get("endDate")
        start = _parse_iso(start_raw)
        end = _parse_iso(end_raw)
        if start is None or end is None:
            # Fall back to decoding the unix stamp out of the slug.
            slug = ev.get("slug", "")
            try:
                start = float(slug.rsplit("-", 1)[1])
                end = start + 300.0
            except (IndexError, ValueError):
                return None
        return Market(
            slug=ev.get("slug", m.get("slug", "")),
            condition_id=m.get("conditionId", ""),
            asset=(ev.get("slug", "btc").split("-")[0]),
            start_ts=start,
            end_ts=end,
            token_up=ids[0],
            token_down=ids[1],
            event_id=str(ev.get("id", "")),
            tick_size=float(m.get("orderPriceMinTickSize") or 0.01),
        )

    def by_slug(self, slug: str) -> Market | None:
        data = self._get("/events", {"slug": slug})
        events = data if isinstance(data, list) else [data]
        for ev in events:
            if ev.get("slug") == slug:
                return self._parse_event(ev)
        return None

    def series(self, series_slug: str, closed: bool = False, limit: int = 100) -> list[Market]:
        data = self._get(
            "/events",
            {
                "series_slug": series_slug,
                "closed": str(closed).lower(),
                "limit": limit,
                "order": "endDate",
                "ascending": "true",
            },
        )
        events = data if isinstance(data, list) else data.get("data", [])
        out = []
        for ev in events:
            mk = self._parse_event(ev)
            if mk is not None:
                out.append(mk)
        return out

    def discover(self, asset: str, window_s: float, series_slug: str, now: float | None = None) -> list[Market]:
        """Probe the current and next windows by constructed slug, newest first.

        Direct slug probing is one cheap request per window and avoids the
        pagination traps of the listing endpoints entirely.
        """
        now = time.time() if now is None else now
        tf = f"{int(window_s // 60)}m"
        starts = [window_start(now, window_s) + i * int(window_s) for i in range(-1, 3)]
        found: list[Market] = []
        for s in starts:
            if s + window_s <= now:
                continue
            try:
                mk = self.by_slug(slug_for(asset, tf, s))
            except FeedError:
                mk = None
            if mk is not None:
                found.append(mk)
        if found:
            return found
        return self.series(series_slug)

    def resolved_outcome(self, slug: str) -> str | None:
        """Read the settled outcome if Gamma has finished resolving.

        Gamma keeps showing *trading* prices for up to a minute or so after the
        window closes; only 1.0/0.0 in outcomePrices means resolved.
        """
        mk = self.by_slug(slug)
        if mk is None:
            return None
        data = self._get("/events", {"slug": slug})
        events = data if isinstance(data, list) else [data]
        for ev in events:
            for m in ev.get("markets", []):
                prices = _load_ids(m.get("outcomePrices"))
                if len(prices) == 2 and {round(float(p), 4) for p in prices} == {0.0, 1.0}:
                    return "up" if float(prices[0]) > 0.5 else "down"
        return None


def _parse_iso(raw: Any) -> float | None:
    if not raw:
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    s = str(raw).replace("Z", "+00:00")
    try:
        from datetime import datetime

        return datetime.fromisoformat(s).timestamp()
    except ValueError:
        return None


@dataclass
class ClobClient:
    base_url: str = "https://clob.polymarket.com"
    timeout: float = 10.0

    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        with httpx.Client(timeout=self.timeout) as client:
            r = client.get(f"{self.base_url}{path}", params=params)
            if r.status_code != 200:
                raise FeedError(f"GET {path} -> {r.status_code}")
            return r.json()

    def book(self, token_id: str, ts: float | None = None) -> Book:
        """Full order book. Polymarket returns bids/asks as price+size strings."""
        data = self._get("/book", {"token_id": token_id})
        return book_from_payload(token_id, data, ts if ts is not None else time.time())

    def tick_size(self, token_id: str) -> float:
        data = self._get("/tick-size", {"token_id": token_id})
        return float(data.get("minimum_tick_size", 0.01))

    def midpoint(self, token_id: str) -> float | None:
        try:
            data = self._get("/midpoint", {"token_id": token_id})
            mid = data.get("mid")
            return float(mid) if mid is not None else None
        except (FeedError, ValueError, TypeError):
            return None


def book_from_payload(token_id: str, data: dict[str, Any], ts: float) -> Book:
    bids = sorted(
        (Level(float(l["price"]), float(l["size"])) for l in data.get("bids", [])),
        key=lambda l: l.price,
        reverse=True,
    )
    asks = sorted(
        (Level(float(l["price"]), float(l["size"])) for l in data.get("asks", [])),
        key=lambda l: l.price,
    )
    return Book(token_id=token_id, ts=ts, bids=tuple(bids), asks=tuple(asks))


# ---------------------------------------------------------------------------
# WebSocket book stream
# ---------------------------------------------------------------------------

async def stream_books(ws_url: str, token_ids: Iterable[str], on_book, on_raw=None) -> None:
    """Subscribe to the CLOB market channel and push Book objects to `on_book`.

    The channel sends a full `book` snapshot on subscribe and `price_change`
    events afterwards. Because a price_change only names the levels that moved,
    we keep a local copy of each book and apply deltas to it -- reconstructing
    deltas without state is how people end up with a book that does not exist.
    """
    import websockets

    tokens = [str(t) for t in token_ids]
    state: dict[str, dict[float, float]] = {t: {} for t in tokens}

    async with websockets.connect(ws_url, ping_interval=20, ping_timeout=20) as ws:
        await ws.send(
            json.dumps({"assets_ids": tokens, "type": "market", "custom_feature_enabled": True})
        )
        while True:
            raw = await ws.recv()
            if on_raw:
                on_raw(raw)
            msgs = json.loads(raw) if isinstance(raw, str) else raw
            if isinstance(msgs, dict):
                msgs = [msgs]
            for msg in msgs:
                et = msg.get("event_type")
                now = time.time()
                if et == "book":
                    tid = msg.get("asset_id")
                    if tid not in state:
                        continue
                    book = book_from_payload(tid, msg, now)
                    state[tid] = {l.price: l.size for l in book.bids}
                    state[tid].update({-l.price: l.size for l in book.asks})
                    on_book(book, "up" if _is_up(tokens, tid) else "down")
                elif et == "price_change":
                    for change in msg.get("changes", []) or msg.get("price_changes", []):
                        tid = change.get("asset_id")
                        if tid not in state:
                            continue
                        price = float(change.get("price"))
                        size = float(change.get("size"))
                        side = str(change.get("side", "")).upper()
                        key = price if side in {"BUY", "BID"} else -price
                        if size <= 0:
                            state[tid].pop(key, None)
                        else:
                            state[tid][key] = size
                    book = _book_from_state(tokens, state, now)
                    if book is not None:
                        on_book(book, "up" if _is_up(tokens, book.token_id) else "down")


def _is_up(tokens: list[str], token_id: str) -> bool:
    return tokens and token_id == tokens[0]


def _book_from_state(tokens: list[str], state: dict[str, dict[float, float]], ts: float) -> Book | None:
    for tid in tokens:
        levels = state.get(tid) or {}
        if not levels:
            continue
        bids = sorted(
            (Level(p, s) for p, s in levels.items() if p > 0 and s > 0),
            key=lambda l: l.price,
            reverse=True,
        )
        asks = sorted(
            (Level(-p, s) for p, s in levels.items() if p < 0 and s > 0),
            key=lambda l: l.price,
        )
        return Book(token_id=tid, ts=ts, bids=tuple(bids), asks=tuple(asks))
    return None
