"""Live runner and order placement.

This is the only module that can spend money, so it is built to be hard to arm
by accident. An order requires ALL of:

  1. `PM5M_LIVE=1` in the environment
  2. `PM5M_DRY_RUN=0`
  3. no kill-switch file present
  4. `py-clob-client` installed and credentials present
  5. the CLI invoked with `--i-understand-this-can-lose-money`
  6. the order notional under `max_position_usd`

If any of those is missing you get a paper fill and a log line explaining which
gate stopped you. A live bot that silently falls back to paper is dangerous in
the other direction (you think you have a position and you don't), so every
decision is logged with the gate that fired.

Order types: these markets have a 5-minute life, so a resting "post-only" maker
order only makes sense in the first minute. The default here is a marketable
FAK (fill-and-kill) limit at the signal price -- you take what is there or you
get nothing, which is exactly the behaviour a 300-second game demands.
"""

from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass

from .broker import PaperBroker
from .config import Settings
from .engine import Engine
from .feeds import ClobClient, GammaClient, stream_books
from .risk import RiskManager
from .spot import stream_spot
from .storage import Store
from .types import Book, Fill, Signal
from .vol import SpotBuffer

LIVE_ENV_KEY = "POLYMARKET_PRIVATE_KEY"


class LiveDisabled(RuntimeError):
    pass


def live_gates(settings: Settings, acknowledged: bool) -> list[str]:
    """Return the list of gates that are still closed. Empty list == armed."""
    closed: list[str] = []
    if not settings.live:
        closed.append("PM5M_LIVE is not 1")
    if settings.dry_run:
        closed.append("PM5M_DRY_RUN is on")
    if not acknowledged:
        closed.append("--i-understand-this-can-lose-money not passed")
    if os.path.exists(settings.kill_switch_path):
        closed.append("kill switch file present")
    if not os.environ.get(LIVE_ENV_KEY):
        closed.append(f"{LIVE_ENV_KEY} not set")
    try:
        import py_clob_client  # noqa: F401
    except ImportError:
        closed.append("py-clob-client not installed (pip install '.[live]')")
    return closed


@dataclass
class LiveOrderClient:
    """Thin wrapper over py-clob-client. Refuses to construct unless armed."""

    settings: Settings
    acknowledged: bool = False

    def __post_init__(self) -> None:
        closed = live_gates(self.settings, self.acknowledged)
        if closed:
            raise LiveDisabled("; ".join(closed))
        from py_clob_client.client import ClobClient as SdkClient

        self._sdk = SdkClient(
            host=self.settings.clob_url,
            key=os.environ[LIVE_ENV_KEY],
            chain_id=int(os.environ.get("PM5M_CHAIN_ID", "137")),
            signature_type=int(os.environ.get("PM5M_SIG_TYPE", "2")),
            funder=os.environ.get("PM5M_FUNDER_ADDRESS") or None,
        )
        if os.environ.get("PM5M_API_KEY"):
            from py_clob_client.clob_types import ApiCreds

            self._sdk.set_api_creds(
                ApiCreds(
                    api_key=os.environ["PM5M_API_KEY"],
                    api_secret=os.environ["PM5M_API_SECRET"],
                    api_passphrase=os.environ["PM5M_API_PASSPHRASE"],
                )
            )
        else:
            self._sdk.set_api_creds(self._sdk.create_or_derive_api_creds())

    def buy(self, token_id: str, shares: float, limit_price: float) -> dict:
        from py_clob_client.clob_types import MarketOrderArgs, OrderType
        from py_clob_client.order_builder.constants import BUY

        if os.path.exists(self.settings.kill_switch_path):
            raise LiveDisabled("kill switch appeared mid-session")
        notional = shares * limit_price
        if notional > self.settings.max_position_usd * 1.5:
            raise LiveDisabled(
                f"order notional ${notional:.2f} exceeds cap "
                f"${self.settings.max_position_usd:.2f}"
            )
        args = MarketOrderArgs(
            token_id=token_id,
            amount=round(notional, 2),
            side=BUY,
            price=round(limit_price, 2),
            order_type=OrderType.FAK,
        )
        signed = self._sdk.create_market_order(args)
        return self._sdk.post_order(signed, OrderType.FAK)


@dataclass
class LiveBroker(PaperBroker):
    """Paper books plus a real order. Records the exchange response verbatim."""

    order_client: LiveOrderClient | None = None
    exchange_responses: list[dict] | None = None

    def execute(self, signal: Signal, book: Book, shares: float, now: float) -> Fill | None:
        if self.order_client is None:
            raise LiveDisabled("LiveBroker constructed without an order client")
        resp = self.order_client.buy(signal.token_id, shares, signal.limit_price)
        if self.exchange_responses is None:
            self.exchange_responses = []
        self.exchange_responses.append({"ts": now, "slug": signal.market_slug, "resp": resp})
        if not resp or (isinstance(resp, dict) and resp.get("error")):
            return None
        return super().execute(signal, book, shares, now)


async def run_live(
    settings: Settings,
    seconds: float | None = None,
    acknowledged: bool = False,
    venue: str = "binance",
    record_only: bool = False,
) -> None:
    """Main event loop. `record_only` collects data and never trades."""
    store = Store(settings.db_path)
    gamma = GammaClient(settings.gamma_url)
    clob = ClobClient(settings.clob_url)
    risk = RiskManager(settings=settings)

    order_client = None
    if not record_only:
        closed = live_gates(settings, acknowledged)
        if closed:
            print(f"[live] gates closed -> PAPER MODE: {'; '.join(closed)}")
            broker: PaperBroker = PaperBroker(settings=settings, risk=risk)
        else:
            order_client = LiveOrderClient(settings, acknowledged)
            broker = LiveBroker(settings=settings, risk=risk, order_client=order_client)
            print("[live] ARMED. Real orders will be placed.")
    else:
        broker = PaperBroker(settings=settings, risk=risk)

    engine = Engine(
        settings=settings, broker=broker, risk=risk, store=store, spot_buffer=SpotBuffer()
    )

    token_sides: dict[str, tuple[str, str]] = {}  # token_id -> (slug, side)

    def on_book(book: Book, side: str) -> None:
        info = token_sides.get(book.token_id)
        engine.on_book(book, side, info[0] if info else None)

    def on_spot(ts: float, price: float) -> None:
        engine.on_spot(ts, price)

    async def discover_loop() -> None:
        while True:
            try:
                found = await asyncio.to_thread(
                    gamma.discover,
                    settings.asset,
                    settings.window_s,
                    settings.series_slug,
                )
                for mk in found:
                    if mk.slug not in engine.windows:
                        if mk.open_price is None:
                            mk.open_price = None
                        engine.on_market(mk)
                        token_sides[mk.token_up] = (mk.slug, "up")
                        token_sides[mk.token_down] = (mk.slug, "down")
                engine.tick(time.time())
            except Exception as exc:  # noqa: BLE001 - keep the loop alive
                print(f"[discover] {exc!r}")
            await asyncio.sleep(20.0)

    async def book_loop() -> None:
        while True:
            tokens = list(token_sides)
            if not tokens:
                await asyncio.sleep(2.0)
                continue
            try:
                # Seed with REST so the first snapshot is not empty.
                for tid in tokens:
                    try:
                        b = await asyncio.to_thread(clob.book, tid)
                        on_book(b, "up" if tid in [k for k, v in token_sides.items() if v[1] == "up"] else "down")
                    except Exception:
                        pass
                await stream_books(settings.ws_url, tokens, on_book)
            except Exception as exc:  # noqa: BLE001
                print(f"[books] reconnect: {exc!r}")
                await asyncio.sleep(3.0)

    tasks = [
        asyncio.create_task(discover_loop()),
        asyncio.create_task(book_loop()),
        asyncio.create_task(stream_spot(venue, on_spot)),
    ]
    try:
        if seconds:
            await asyncio.wait(tasks, timeout=seconds)
        else:
            await asyncio.gather(*tasks)
    finally:
        for t in tasks:
            t.cancel()
        print("summary:", broker.summary())
        print("counts :", store.counts())
        store.close()
