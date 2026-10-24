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


def _band_settings(mode: str, lo: float, hi: float) -> Settings:
    from dataclasses import replace

    return replace(
        Settings(),
        strategy_mode=mode,
        min_price=lo,
        max_price=hi,
        min_z_lock=0.0,
        min_edge_cents=0.5,
        maker_edge_cents=1.5,
        maker_quote_shares=40.0,
        maker_requote_s=2.0,
    )


def cmd_winrate(args: argparse.Namespace) -> int:
    """Can a 70-80% win rate be genuine and profitable?"""
    from .winrate import (
        accuracy_you_need,
        expected_longest_loss_streak,
        prob_streak_at_least,
        same_winrate_scenarios,
        table,
    )

    print("=" * 92)
    print("CAN YOU HAVE A GENUINE 70-80% WIN RATE?  YES -- AND IT MEANS NOTHING")
    print("=" * 92)
    print(
        "In a binary market the win rate you get is set by the price you pay.\n"
        "Buy at 78c and you win ~78% of the time because that is what the market\n"
        "already believes. A high win rate is a receipt, not an achievement.\n"
    )

    header = (
        f"{'price':>6} {'market says':>11} {'break-even':>11} {'edge needed':>12} "
        f"{'loss/win':>9} {'fee %stake':>10}  verdict"
    )
    print(header)
    print("-" * len(header))
    for row in table():
        print(
            f"{row.price*100:5.0f}c {row.fair_win_rate*100:10.0f}% "
            f"{row.break_even*100:10.2f}% {row.edge_needed_pp:10.2f}pp "
            f"{row.loss_win_ratio:8.1f}x {row.fee_pct_of_stake:9.2f}%  {row.verdict}"
        )

    print()
    print("=" * 92)
    print("THE SAME WIN RATE, THREE PRICES, OPPOSITE OUTCOMES")
    print("=" * 92)
    for line in same_winrate_scenarios(0.78):
        print(line)

    print()
    print("=" * 92)
    print("WHAT 78c ACCURACY ACTUALLY REQUIRES")
    print("=" * 92)
    print(accuracy_you_need(0.78))
    print(accuracy_you_need(0.60))
    print()
    print(
        "Notice the trade-off: at 60c the fee is 2.80% of stake and you need a\n"
        "1.68pp edge. At 78c the fee is 1.54% of stake, so you need just 1.20pp.\n"
        "Higher prices need LESS absolute edge -- but the events are near-certain\n"
        "and therefore priced by everyone, so finding that edge is harder, and\n"
        "each loss costs 3.5x a win."
    )

    print()
    print("=" * 92)
    print("THE STREAKS ARE SCHEDULED, NOT UNLUCKY")
    print("=" * 92)
    print(
        "At 78c, a loss is 3.5x a win. Here is what the binomial distribution\n"
        "guarantees you over a year of trading (1,000 trades):\n"
    )
    print(f"{'true win rate':>14} {'longest loss streak':>21} {'P(streak >= 4)':>16} {'P(streak >= 6)':>16}")
    print("-" * 70)
    for w in (0.70, 0.78, 0.80, 0.90, 0.95):
        exp = expected_longest_loss_streak(w, 1000)
        print(
            f"{w*100:13.0f}% {exp:20.1f} "
            f"{prob_streak_at_least(w, 4, 1000):15.1%} "
            f"{prob_streak_at_least(w, 6, 1000):15.1%}"
        )
    print()
    print(
        "At a 78% win rate you should EXPECT a run of ~5 losses in a year, and\n"
        "each of those loses 3.5x what a win earns. A 5-loss run gives back 17\n"
        "wins. If your sizing assumes the streaks do not happen, that single run\n"
        "is the one that ends the account -- and it will feel like bad luck\n"
        "rather than the arithmetic it is."
    )
    print()
    print(
        "So: 70-80% win rate? Trivially available. Buy at 75-80c. Profitable?\n"
        "Only if your TRUE accuracy exceeds the price, by more than the fee, and\n"
        "`pm5m expectancy` proves it -- which took ~1,700 trades for a genuine\n"
        "edge last time we measured. That is the real bar."
    )
    return 0


def cmd_expectancy(args: argparse.Namespace) -> int:
    """Is the edge real? The only question that matters before sizing up."""
    from .expectancy import analyze, conservative_kelly
    from .storage import Store

    settings = Settings()
    if args.demo:
        import random

        rng = random.Random(args.seed)
        from .expectancy import TradeRecord

        trades = []
        for i in range(args.demo):
            price = 0.55
            shares = 40.0
            fee = 0.0
            won = rng.random() < args.true_win_rate
            pnl = (shares * 1.0 if won else 0.0) - price * shares - fee
            trades.append(
                TradeRecord(
                    ts=float(i), slug=f"demo{i}", side="up", price=price,
                    shares=shares, fee=fee, won=won, pnl=pnl,
                )
            )
        print(
            f"SIMULATED {args.demo} trades at a TRUE win rate of "
            f"{args.true_win_rate:.1%} and a 55c entry.\n"
            "The point is to watch how much evidence a real edge needs before\n"
            "the statistics can see it.\n"
        )
    else:
        store = Store(settings.db_path)
        trades = store.trade_records()
        store.close()
        if not trades:
            print("no recorded trades. Run `paper` first, or try --demo 200.")
            return 1
        print(f"analysing {len(trades)} recorded trades from {settings.db_path}\n")

    exp = analyze(trades, strategies_tried=args.variants)
    print(exp.pretty())
    f = conservative_kelly(exp)
    print()
    print(f"conservative Kelly (quarter-Kelly on the LOWER CI bound): {f:.2%} of bankroll")
    print(
        f"  on a ${settings.bankroll_usd:,.0f} bankroll that is "
        f"${f * settings.bankroll_usd:,.2f} per trade"
    )
    if exp.n and exp.mean_pnl > 0 and not exp.significant:
        print(
            "\nDo not size up on this. Positive-but-insignificant is exactly the\n"
            "state in which people lever a coin flip."
        )
    return 0


def cmd_goal(args: argparse.Namespace) -> int:
    """The arithmetic of a monthly income target."""
    from .goals import analyse_goal, simulate_outcomes  # noqa: F401

    rep = analyse_goal(args.capital, args.target)
    print("=" * 78)
    print("WHAT A MONTHLY INCOME TARGET ACTUALLY REQUIRES")
    print("=" * 78)
    print(rep.pretty())

    print()
    print("=" * 78)
    print("WHAT A GENUINELY GOOD STRATEGY LOOKS LIKE OVER 12 MONTHS")
    print("=" * 78)
    print(
        "Assumption: 20%/yr return, 15%/yr volatility, fat-tailed monthly shocks.\n"
        "That is a *good* fund. Here is its actual distribution of outcomes.\n"
    )
    for label, ar, vol in (
        ("excellent strategy (20%/yr, 15% vol)", 0.20, 0.15),
        ("exceptional strategy (35%/yr, 25% vol)", 0.35, 0.25),
        ("leveraged to chase the target", 0.20, 0.15),
    ):
        lev = 1.0
        if "leveraged" in label:
            lev = max(1.0, min(20.0, rep.required_annual / 0.20)) if rep.required_annual > 0 else 1.0
        out = simulate_outcomes(
            args.capital,
            ar,
            vol * lev,
            months=12,
            monthly_target=args.target,
            fat_tails=4.0,
        )
        print(f"--- {label}" + (f"  ({lev:.1f}x)" if lev > 1 else "") + " ---")
        print(out.pretty(args.capital, args.target))
        print()

    print(
        "The column that matters is 'probability of hitting target'. A good\n"
        "strategy on small capital hits a large monthly target roughly never.\n"
        "Leverage raises the odds of a good month and raises the odds of ruin\n"
        "faster. That trade is not a strategy, it is a wager on the order of\n"
        "your bad months."
    )
    return 0


def cmd_band(args: argparse.Namespace) -> int:
    """The economics of the price band you want to trade in."""
    settings = Settings()
    print("=" * 84)
    print("TAKER vs MAKER ECONOMICS BY PRICE BAND")
    print("=" * 84)
    print(
        "A contract bought at p pays 1.00 if it wins. So the break-even win rate is\n"
        "simply your all-in cost per share. Takers pay the fee; makers do not.\n"
    )
    header = (
        f"{'price':>6} {'fee/sh':>7} {'fee %stake':>11} "
        f"{'TAKER need':>11} {'edge vs mkt':>12} {'MAKER need':>11} {'payout':>8}"
    )
    print(header)
    print("-" * len(header))
    for p in (0.50, 0.52, 0.55, 0.58, 0.60, 0.65, 0.75, 0.85, 0.95):
        fee = taker_fee_per_share(p, settings.taker_fee_rate)
        taker_need = p + fee
        payout = (1 - p) / p
        print(
            f"{p:6.2f} {fee * 100:6.2f}c {fee / p * 100:10.2f}% "
            f"{taker_need * 100:10.2f}% {(taker_need - p) * 100:10.2f}pp "
            f"{p * 100:10.2f}% {payout * 100:7.1f}%"
        )
    print(
        "\nReading it: at 55c a taker must be right 56.73% of the time against a\n"
        "market implying 55% -- a 1.73pp forecasting edge on every trade. A maker\n"
        "needs only 55%, because the fee is zero. That asymmetry is the entire\n"
        "argument for quoting in this band instead of taking."
    )
    return 0


def cmd_demo(args: argparse.Namespace) -> int:
    from .sim import run_simulation

    base = Settings()
    print("=" * 84)
    print("ADVERSARIAL SIMULATION - this harness is built to falsify the strategy")
    print("=" * 84)
    fee50 = taker_fee_per_share(0.50, base.taker_fee_rate)
    print(
        f"taker fee at 50c: {fee50 * 100:.2f}c/share "
        f"({fee_as_pct_of_stake(0.50):.2%} of stake)   maker fee: 0"
    )
    print("run `python -m pm5m band` for the full price table\n")

    scenarios = [
        (
            "A. TAKER in 50-60c, fair counterparty",
            _band_settings("taker", 0.50, 0.60),
            dict(counterparty="fair", basis_sd=2e-5, sigma_noise_sd=0.5),
            "should refuse to trade: no edge, and the fee is 3% of stake",
        ),
        (
            "B. MAKER in 50-60c, fair counterparty",
            _band_settings("maker", 0.50, 0.60),
            dict(counterparty="fair", basis_sd=2e-5, sigma_noise_sd=0.5,
                 counterparty_noise_cents=1.5),
            "adverse selection vs an informed book: the honest default",
        ),
        (
            "C. MAKER in 50-60c, spot-watching counterparty",
            _band_settings("maker", 0.50, 0.60),
            dict(counterparty="naive_spot", basis_sd=2e-5, sigma_noise_sd=0.5,
                 counterparty_noise_cents=1.5),
            "THE HYPOTHESIS: the book ignores that settlement is an average",
        ),
        (
            "D. MAKER in 50-60c, slightly stale counterparty",
            _band_settings("maker", 0.50, 0.60),
            dict(counterparty="lagged", lag_ticks=3, basis_sd=2e-5,
                 sigma_noise_sd=0.5, counterparty_noise_cents=1.5),
            "the book is 6s behind the true fair value",
        ),
        (
            "E. TAKER in 50-60c, spot-watching counterparty",
            _band_settings("taker", 0.50, 0.60),
            dict(counterparty="naive_spot", basis_sd=2e-5, sigma_noise_sd=0.5),
            "same mispricing, but we pay 3% to reach it",
        ),
    ]

    for title, cfg, kwargs, note in scenarios:
        res = run_simulation(cfg, n_windows=args.windows, seed=args.seed, **kwargs)
        print(f"--- {title} ---")
        print(f"    ({note})")
        print(res.pretty())
        print()

    print(
        "How to read this:\n"
        "  A and B are the verdict on mid-range trading with an informed book.\n"
        "  C and D are the only worlds where 50-60c works, and they require the\n"
        "  counterparty to be systematically wrong. E shows that even when they\n"
        "  are wrong, paying the taker fee at 55c eats a large share of it.\n"
        "  Whether C or D resembles reality is not a modelling question -- it is\n"
        "  what `record` and `backtest` exist to answer, using your own fills."
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

    wr = sub.add_parser("winrate", help="why a 70-80% win rate means nothing on its own")
    wr.set_defaults(func=cmd_winrate)

    e = sub.add_parser("expectancy", help="is the edge statistically real?")
    e.add_argument("--demo", type=int, default=0, help="simulate N trades instead of using the store")
    e.add_argument("--true-win-rate", type=float, default=0.58, dest="true_win_rate")
    e.add_argument("--variants", type=int, default=1, help="strategy variants you tried (multiple-testing correction)")
    e.add_argument("--seed", type=int, default=3)
    e.set_defaults(func=cmd_expectancy)

    g = sub.add_parser("goal", help="what a monthly income target requires")
    g.add_argument("--capital", type=float, default=1000.0)
    g.add_argument("--target", type=float, default=5000.0, help="monthly target in USD")
    g.set_defaults(func=cmd_goal)

    b = sub.add_parser("band", help="taker vs maker economics by price")
    b.set_defaults(func=cmd_band)

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
