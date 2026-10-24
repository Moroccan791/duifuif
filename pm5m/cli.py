"""Command line entry point.

    python -m pm5m demo                 # offline simulation, no network
    python -m pm5m doctor               # check endpoints + credentials + deps
    python -m pm5m record --minutes 30  # collect data, never trade
    python -m pm5m paper  --minutes 30  # trade against live books, fake money
    python -m pm5m backtest             # replay what you recorded
    python -m pm5m report               # summarise the store
    python -m pm5m live --minutes 30 --i-understand-this-can-lose-money
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from .config import Settings
from .fees import fee_as_pct_of_stake, taker_fee_per_share


def cmd_demo(args: argparse.Namespace) -> int:
    from .sim import breakeven_edge_cents, run_simulation

    settings = Settings()
    print("=" * 74)
    print("ADVERSARIAL SIMULATION - this harness is built to falsify the strategy")
    print("=" * 74)
    print(
        f"taker fee at 50c: {fee_as_pct_of_stake(0.50):.2%} of stake per entry "
        f"({taker_fee_per_share(0.50) * 100:.2f}c/share)"
    )
    print(
        f"taker fee at 95c: {fee_as_pct_of_stake(0.95):.2%} of stake "
        f"({taker_fee_per_share(0.95) * 100:.2f}c/share)"
    )
    print(
        f"break-even fee at 95c: {breakeven_edge_cents(settings, 0.95):.2f}c/share\n"
    )

    scenarios = [
        (
            "A. fair maker, no basis risk, no vol error",
            dict(maker_efficiency=1.0, basis_sd=0.0, sigma_noise_sd=0.0),
            "the bot has no advantage, so it should barely trade",
        ),
        (
            "B. fair maker, realistic basis risk (2bp) + vol error",
            dict(maker_efficiency=1.0, basis_sd=2e-5, sigma_noise_sd=0.5),
            "the honest default: does a noisy model make money?",
        ),
        (
            "C. slow maker (quotes halfway to 50/50)",
            dict(maker_efficiency=0.5, basis_sd=2e-5, sigma_noise_sd=0.5),
            "the world you would need to exist",
        ),
        (
            "D. clueless maker (always quotes 50/50)",
            dict(maker_efficiency=0.0, basis_sd=2e-5, sigma_noise_sd=0.5),
            "upper bound on the prize, not a forecast",
        ),
    ]

    for title, kwargs, note in scenarios:
        res = run_simulation(
            settings, n_windows=args.windows, seed=args.seed, **kwargs
        )
        print(f"--- {title} ---")
        print(f"    ({note})")
        print(res.pretty())
        print()

    print(
        "Read scenarios A and B as the verdict, and C and D as the requirement:\n"
        "  you only make money if your counterparty is materially slower or worse\n"
        "  informed than you are, and you must be right by more than the spread\n"
        "  plus the fee, every window, for thousands of windows."
    )
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    from .feeds import ClobClient, GammaClient

    settings = Settings()
    print(f"python      : {sys.version.split()[0]}")
    ok = True

    for mod in ("httpx", "websockets"):
        try:
            __import__(mod)
            print(f"dep {mod:<10}: ok")
        except ImportError:
            print(f"dep {mod:<10}: MISSING (pip install {mod})")
            ok = False

    try:
        import py_clob_client  # noqa: F401

        print("dep py-clob-client: ok (live trading possible)")
    except ImportError:
        print("dep py-clob-client: missing (paper/record only)")

    gamma = GammaClient(settings.gamma_url)
    clob = ClobClient(settings.clob_url)
    try:
        markets = gamma.discover(
            settings.asset, settings.window_s, settings.series_slug
        )
        print(f"gamma       : ok, {len(markets)} window(s) discovered")
        for mk in markets[:3]:
            print(
                f"  {mk.slug} start={mk.start_ts:.0f} end={mk.end_ts:.0f} "
                f"tick={mk.tick_size}"
            )
        if markets:
            mk = markets[0]
            book = clob.book(mk.token_up)
            print(
                f"clob book   : up best_bid={book.best_bid} "
                f"best_ask={book.best_ask} sizes={book.bid_size:.0f}/{book.ask_size:.0f}"
            )
            print(f"fee/share at ask: {taker_fee_per_share(book.best_ask or 0.5) * 100:.3f}c")
    except Exception as exc:  # noqa: BLE001
        print(f"network     : FAILED ({exc!r})")
        ok = False

    from .live import live_gates

    gates = live_gates(settings, acknowledged=False)
    print("live gates  : " + ("ARMED" if not gates else f"{len(gates)} closed -> " + "; ".join(gates)))
    return 0 if ok or args.lenient else 1


def cmd_record(args: argparse.Namespace) -> int:
    from .live import run_live

    settings = Settings()
    asyncio.run(
        run_live(
            settings,
            seconds=args.minutes * 60 if args.minutes else None,
            record_only=True,
            venue=args.venue,
        )
    )
    return 0


def cmd_paper(args: argparse.Namespace) -> int:
    from .live import run_live

    settings = Settings()
    asyncio.run(
        run_live(
            settings,
            seconds=args.minutes * 60 if args.minutes else None,
            record_only=False,
            acknowledged=False,
            venue=args.venue,
        )
    )
    return 0


def cmd_live(args: argparse.Namespace) -> int:
    from .live import run_live, live_gates

    settings = Settings()
    gates = live_gates(settings, args.i_understand_this_can_lose_money)
    if gates:
        print("refusing to go live:")
        for g in gates:
            print(f"  - {g}")
        return 2
    asyncio.run(
        run_live(
            settings,
            seconds=args.minutes * 60 if args.minutes else None,
            acknowledged=True,
            venue=args.venue,
        )
    )
    return 0


def cmd_backtest(args: argparse.Namespace) -> int:
    from .backtest import replay
    from .storage import Store

    settings = Settings()
    store = Store(settings.db_path)
    counts = store.counts()
    print("store:", json.dumps(counts))
    if counts["spot"] < 100:
        print("not enough recorded spot data to replay. Run `record` first.")
        store.close()
        return 1
    res = replay(store, settings, args.windows)
    print(res.pretty())
    store.close()
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    from .storage import Store

    settings = Settings()
    store = Store(settings.db_path)
    print("counts:", json.dumps(store.counts(), indent=2))

    rows = store.conn.execute(
        """SELECT COUNT(*) n, SUM(pnl) pnl, AVG(outcome='up') up_rate,
                  AVG(ABS(open_price - avg_price)) mean_abs_move
           FROM settlements"""
    ).fetchone()
    if rows and rows[0]:
        print(
            f"\nsettlements: {rows[0]}  pnl=${rows[1] or 0:,.2f}  "
            f"up_rate={rows[2] or 0:.1%}  mean|avg-open|=${rows[3] or 0:,.2f}"
        )

    fills = store.fills()
    if fills:
        staked = sum(f["notional"] + f["fee"] for f in fills)
        fees = sum(f["fee"] for f in fills)
        print(
            f"fills      : {len(fills)}  staked=${staked:,.2f}  fees=${fees:,.2f} "
            f"({fees / staked:.2%} of stake)"
        )
    store.close()
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="pm5m", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("demo", help="offline simulation of the mechanics")
    d.add_argument("--windows", type=int, default=200)
    d.add_argument("--seed", type=int, default=11)
    d.set_defaults(func=cmd_demo)

    doc = sub.add_parser("doctor", help="check connectivity, deps and live gates")
    doc.add_argument("--lenient", action="store_true", help="exit 0 even if network fails")
    doc.set_defaults(func=cmd_doctor)

    for name, func, helptext in (
        ("record", cmd_record, "record market data, never trade"),
        ("paper", cmd_paper, "trade against live books with fake money"),
    ):
        sp = sub.add_parser(name, help=helptext)
        sp.add_argument("--minutes", type=float, default=30.0)
        sp.add_argument("--venue", default="binance")
        sp.set_defaults(func=func)

    lv = sub.add_parser("live", help="place real orders (all gates required)")
    lv.add_argument("--minutes", type=float, default=None)
    lv.add_argument("--venue", default="binance")
    lv.add_argument(
        "--i-understand-this-can-lose-money", action="store_true", dest="i_understand_this_can_lose_money"
    )
    lv.set_defaults(func=cmd_live)

    bt = sub.add_parser("backtest", help="replay recorded data")
    bt.add_argument("--windows", type=int, default=500)
    bt.set_defaults(func=cmd_backtest)

    rp = sub.add_parser("report", help="summarise the store")
    rp.set_defaults(func=cmd_report)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
