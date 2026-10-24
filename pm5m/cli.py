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


def cmd_plan(args: argparse.Namespace) -> int:
    """Simulate the stated plan: start at $X, make a fixed amount per day."""
    from .plan_sim import (PlanConfig, bankroll_for_daily, daily_distribution,
                           daily_ev, pretty, simulate)

    start, target = args.start, args.target
    print("=" * 78)
    print(f"THE PLAN: start with ${start:.0f}, make ${target:.0f}/day")
    print("=" * 78)
    print(f"That is {target/start*100:.1f}% per day, or "
          f"{((1+target/start)**30-1)*100:,.0f}% per month compounded.")
    print()

    print("WHAT A DAY ACTUALLY LOOKS LIKE (40 trades, taker at 55c)")
    print(f"{'edge':>6} {'stake':>7} {'mean':>8} {'sd':>7} {'p5':>8} {'median':>8} "
          f"{'p95':>8} {'P(lose)':>8}")
    print("-" * 76)
    for e in (0.01, 0.02, 0.03, 0.05):
        d = daily_distribution(start, e)
        print(f"{e*100:5.0f}pp {d['stake']:7.2f} {d['mean']:8.2f} {d['sd']:7.2f} "
              f"{d['p05']:8.2f} {d['p50']:8.2f} {d['p95']:8.2f} {d['p_loss_day']:8.1%}")
    print()
    print("  A 'fixed' daily amount is not something a probabilistic edge can")
    print("  produce. Even at a large edge, roughly a third of days are losses.")
    print()

    print(f"WHAT BANKROLL ${target:.0f}/DAY ACTUALLY REQUIRES")
    print(f"{'edge':>6} {'taker':>14} {'maker':>14}")
    print("-" * 40)
    for e in (0.01, 0.02, 0.03, 0.05):
        t = bankroll_for_daily(target, e, maker=False)
        m = bankroll_for_daily(target, e, maker=True)
        ts = f"${t:,.0f}" if t < 1e12 else "impossible"
        ms = f"${m:,.0f}" if m < 1e12 else "impossible"
        print(f"{e*100:5.0f}pp {ts:>14} {ms:>14}")
    print()
    print("  At 55c a taker pays 1.73c/share, so any edge under ~1.8pp is")
    print("  negative after fees. 'maker' means quoting inside the spread.")
    print()

    cfg = PlanConfig(start=start, daily_target=target, price=0.55,
                     edge=args.edge, trades_per_day=args.trades,
                     days=args.days, maker=args.maker, seed=7)
    print("MONTE CARLO OF THE FULL PLAN")
    print(pretty(cfg, paths=args.paths))
    return 0


def cmd_phase2(args: argparse.Namespace) -> int:
    """Phase 2: validate the settlement model, then look for edge vs the book."""
    from .history import Cache, Window, attach_spot, fetch_binance_1s, fetch_gamma_windows, synthetic_windows
    from .phase2 import analyse

    cache = Cache()
    print("=" * 96)
    print("PHASE 2 — the decisive test")
    print("=" * 96)

    if args.fixture:
        windows = synthetic_windows(
            n=args.windows, edge_bps=args.edge_bps, market_edge_bps=args.market_edge
        )
        print(f"using SYNTHETIC windows (n={len(windows)}, injected edge {args.edge_bps} bp)\n")
    else:
        if cache.has("windows.csv"):
            windows = cache.load_windows("windows.csv")
            print(f"loaded {len(windows)} windows from cache\n")
        else:
            print("fetching closed 5m windows from Gamma ...")
            windows = fetch_gamma_windows(max_pages=args.pages)
            if not windows:
                print(
                    "no windows fetched. Either the endpoint is unreachable from "
                    "this machine, or the response shape changed.\n"
                    "Try --fixture to exercise the analysis offline."
                )
                return 1
            cache.save_windows("windows.csv", windows)
            print(f"fetched and cached {len(windows)} windows\n")

        if cache.has("klines.json") and cache.has("klines_span.json"):
            span = cache.load_json("klines_span.json")
            klines = [(int(t), float(p)) for t, p in cache.load_json("klines.json")]
            print(f"loaded {len(klines)} klines ({span} layers) from cache")
        else:
            print("fetching Binance 1s klines (this is the slow part) ...")
            lo = int(min(w.start_ts for w in windows) * 1000) - 120_000
            hi = int(max(w.end_ts for w in windows) * 1000)
            klines = fetch_binance_1s(lo, hi)
            if klines:
                cache.save_json("klines.json", klines)
                cache.save_json("klines_span.json", f"{len(klines)} candles")
                print(f"fetched {len(klines)} klines")
            else:
                print("no klines fetched; cannot score the proxy")
                return 1
        attach_spot(windows, klines)

    report = analyse(windows)
    print(report.pretty())
    return 0


def cmd_dashboard(args: argparse.Namespace) -> int:
    """Serve the cockpit. Graphs, not tables."""
    from .dashboard import DashboardServer
    from .storage import Store

    store = None
    if not args.demo:
        try:
            store = Store(Settings().db_path)
        except Exception as exc:  # noqa: BLE001
            print(f"could not open store ({exc}); falling back to demo data")
    srv = DashboardServer(port=args.port, store=store, demo=args.demo)
    print("serving dashboard - open the URL below (Ctrl-C to stop)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
        srv.stop()
    return 0


def cmd_selectivity(args: argparse.Namespace) -> int:
    """Can selectivity lift the win rate to 70% at 50-60c, and what does it cost?"""
    from .selectivity import HEADER, best_by_profit, highest_win_rate, sweep

    print("=" * 92)
    print("CAN WE WIN 70% AT 50-60c?  THE ONLY LEVER IS TAKING FEWER, BETTER TRADES")
    print("=" * 92)
    print(
        "At a fixed price band, a higher win rate IS a higher edge -- at 55c every\n"
        "extra point of accuracy is a cent per share. So the question is real.\n"
        "Raising the required edge filters out the marginal trades first, which\n"
        "lifts the hit rate on what survives. The cost is trade count.\n"
    )

    results = sweep(windows=args.windows, seed=args.seed)

    for label, rows in results.items():
        print(f"--- counterparty: {label} ---")
        print(HEADER)
        print("-" * 92)
        for r in rows:
            print(r.pretty())
        top = highest_win_rate(rows, min_trades=args.min_trades)
        best = best_by_profit(rows)
        print()
        if top:
            print(
                f"  highest win rate with >= {args.min_trades} trades: "
                f"{top.win_rate:.1%} on {top.trades} trades "
                f"(threshold {top.min_edge_cents:.1f}c, pnl ${top.total_pnl:+,.0f})"
            )
        if best:
            print(
                f"  most profit: ${best.total_pnl:+,.0f} at "
                f"{best.win_rate:.1%} win rate on {best.trades} trades "
                f"(threshold {best.min_edge_cents:.1f}c)"
            )
        print()

    print("=" * 92)
    print("WHAT THIS MEANS")
    print("=" * 92)

    allrows = [r for rows in results.values() for r in rows]
    eligible = [r for r in allrows if r.trades >= args.min_trades]
    best_wr = max(eligible, key=lambda r: r.win_rate) if eligible else None
    best_pnl = max(allrows, key=lambda r: r.total_pnl) if allrows else None
    noise = [
        r for r in allrows
        if 0 < r.trades < args.min_trades and r.win_rate >= 0.65
    ]

    print(
        f"  Ceiling with a meaningful sample (>= {args.min_trades} trades): "
        + (f"{best_wr.win_rate:.1%} on {best_wr.trades} trades" if best_wr else "none")
    )
    if best_wr:
        print(
            f"    -> 70% was NOT reached at any threshold. p-value on that row: "
            f"{best_wr.p_value:.3f}"
            + ("  (not significant)" if best_wr.p_value >= 0.05 else "  (significant)")
        )
    if best_pnl:
        print(
            f"  Most profitable row overall: ${best_pnl.total_pnl:+,.0f} at a "
            f"{best_pnl.win_rate:.1%} win rate on {best_pnl.trades} trades."
        )
    print()

    if noise:
        print("  High win rates that are pure noise (fewer trades than the minimum):")
        for r in sorted(noise, key=lambda r: -r.win_rate)[:5]:
            print(
                f"    {r.win_rate:.1%} on {r.trades} trades, "
                f"pnl ${r.total_pnl:+,.0f}, p={r.p_value:.3f}  <- NOT evidence"
            )
        print(
            "    These are the numbers you would screenshot. They are the reason\n"
            "    people believe a 70% win rate exists. It does -- until the sample\n"
            "    grows and it reverts to the price you paid."
        )
        print()

    print(
        "  THE MAKER PARADOX -- the most important thing on this page:\n\n"
        "  Raising the required edge LOWERS the maker win rate instead of raising it.\n"
        "  A maker who demands more edge must quote further below fair value, so the\n"
        "  order only fills when the market crashes down to meet it -- which is\n"
        "  precisely when the counterparty is most likely to be right. Demanding a\n"
        "  bigger edge selects for maximum adverse selection.\n\n"
        "  Selectivity is only a real lever for TAKERS, who choose when to act. For\n"
        "  makers the counterparty chooses, and it chooses the worst moments.\n\n"
        "  And for takers, the fee at 55c (3.15% of stake) eats the improvement.\n"
        "  That is why no row in this table reaches 70% with a sample worth trusting.\n\n"
        "  FINAL: at 50-60c the achievable, statistically supportable win rate looks\n"
        "  like the high 50s to low 60s -- and only if the spot-watching counterparty\n"
        "  hypothesis is true. 70% is not on the table at this sample size, from any\n"
        "  threshold, in any mode. If you see 70% somewhere, count the trades."
    )
    return 0


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
            "the book prices spot-vs-strike and ignores the trailing 60s average",
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
        "  C, D and E let the counterparty be wrong in a specific way.\n"
        "\n"
        "  IMPORTANT: under the correct rule (TWAP60 at BOTH ends) a spot-watcher\n"
        "  is much closer to right than the old window-average rule implied -- a\n"
        "  60s trailing mean tracks spot closely over 5 minutes. Against the old,\n"
        "  wrong rule C and D looked profitable. That profit was an artifact of a\n"
        "  settlement rule that does not exist. Do not expect it to return.\n"
        "\n"
        "  Whether any of these resembles reality is not a modelling question.\n"
        "  It is what `phase2`, `record` and `backtest` exist to answer."
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


def cmd_reversal(args: argparse.Namespace) -> int:
    """Measure the five-minute fade on 1-minute klines, then price the trade.

    This is the only part of pm5m with a measured edge. It answers two separate
    questions and refuses to merge them: how often the event happens (data),
    and whether it is worth buying at a given price (arithmetic + fees).
    """
    import glob as _glob

    from .reversal import (MIN_MOVE, breakeven_ask, ev_per_dollar,
                           fade_probability, kelly_fraction)

    th = args.threshold
    print(f"fade threshold: |5-minute move| >= {th:.2%}   "
          f"(fee {args.fee:.0%} taker, settlement Up iff TWAP60(close) >= TWAP60(open))")
    print()

    from .reversal import windows_from_files

    for pattern in args.csv:
        files = sorted(_glob.glob(pattern))
        if not files:
            print(f"{pattern}: no files")
            continue
        rows = windows_from_files(files)
        if not rows:
            print(f"{pattern}: no complete windows")
            continue
        sel = [r for r in rows if abs(r[2]) >= th]
        base = sum(r[1] for r in rows) / len(rows)
        print(f"{pattern}")
        print(f"  {len(rows):,} five-minute windows, base rate up {base:.2%}")
        if not sel:
            print("  no windows above the threshold")
            print()
            continue
        wins = sum(1 for r in sel if (1 if r[2] < 0 else 0) == r[1])
        hit = wins / len(sel)
        se = (hit * (1 - hit) / len(sel)) ** 0.5
        per_day = len(sel) / (len(rows) * 300 / 86400)
        print(f"  fade fires on {len(sel):,} of them ({100*len(sel)/len(rows):.2f}%), "
              f"about {per_day:.1f} per day")
        print(f"  hit rate {hit:.2%}  +/- {1.96*se:.2%} (95%)")
        print(f"  break-even price {breakeven_ask(hit)*100:.2f}c -- buy below that or lose")
        print()
        print("  price you pay   EV per $1    Kelly stake on $100   verdict")
        for ask in (0.45, 0.48, 0.50, 0.52, 0.54, 0.56):
            ev = ev_per_dollar(hit, ask, args.fee)
            k = kelly_fraction(hit, ask, args.fee)
            print(f"      {ask*100:4.0f}c        {ev*100:+7.2f}%      "
                  f"{k*100:5.2f}% = ${100*k:5.2f}      "
                  f"{'TAKE' if ev > 0 else 'pass'}")
        print()

    print(f"Reference table (BTC 2022-2026): a 0.40% move fades {fade_probability(MIN_MOVE):.1%}")
    print("The hit rate is the event. The price is the trade. Only one of them")
    print("is in this data; the other has to be recorded off the order book.")
    return 0


def _utc(ts: float) -> str:
    import time as _t
    return _t.strftime("%Y-%m-%d", _t.gmtime(ts))


def cmd_fade(args: argparse.Namespace) -> int:
    """What a daily income target actually costs, at each possible fill price.

    The honest output is a table, not a number: capital scales linearly with the
    edge, and the edge is only known to within a confidence interval, so the
    requirement ranges from "cheap" to "impossible" across it.
    """
    import glob as _glob

    from .fade_sim import FadeConfig, capital_for_daily_target, simulate
    from .reversal import windows_from_files

    grouped: list[list[tuple[float, int, float]]] = []
    for pattern in args.csv:
        files = sorted(_glob.glob(pattern))
        got = windows_from_files(files)
        if got:
            grouped.append(got)
    if not grouped:
        print("no windows; check --csv")
        return 1

    # Compare like with like: if one asset only has two years of data, counting
    # the other asset's earlier years dilutes bets-per-day and inflates the
    # capital requirement. Default to the period they all cover.
    since = args.since if args.since is not None else max(g[0][0] for g in grouped)
    windows = [w for g in grouped for w in g if w[0] >= since]
    windows.sort(key=lambda w: w[0])
    if not windows:
        print("no windows in the common period; pass --since")
        return 1

    bets = sum(1 for w in windows if abs(w[2]) >= args.threshold)
    days = len({int(w[0] // 86_400) for w in windows})
    print(f"period: {_utc(since)} onward ({len(grouped)} asset(s))")
    print(f"{len(windows):,} windows over {days:,} days; "
          f"{bets:,} bets fire ({bets/days:.1f} per day) at >= {args.threshold:.2%}")
    print(f"target: ${args.target:,.2f} per day\n")

    lo, mid, hi = 0.5305, 0.5391, 0.5476      # BTC 53.91% and its 95% interval
    print("capital required, by the price you actually pay for the fade")
    print(f"{'fill':>7}{'if edge is 53.05%':>20}{'if 53.91%':>14}{'if 54.76%':>14}"
          f"{'worst day':>13}{'maxDD':>9}")
    for fill in (0.46, 0.48, 0.50, 0.515, 0.52, 0.54):
        cfg = FadeConfig(fill=fill, threshold=args.threshold)
        cells = []
        for p in (lo, mid, hi):
            c = capital_for_daily_target(windows, args.target, cfg, p)
            cells.append("never" if c == float("inf") else f"${c:,.0f}")
        cap = capital_for_daily_target(windows, args.target, cfg, mid)
        if cap == float("inf"):
            print(f"{fill*100:6.1f}c{cells[0]:>20}{cells[1]:>14}{cells[2]:>14}"
                  f"{'-':>13}{'-':>9}")
            continue
        res = simulate(windows, bankroll=cap, cfg=cfg, compound=False)
        print(f"{fill*100:6.1f}c{cells[0]:>20}{cells[1]:>14}{cells[2]:>14}"
              f"{res.worst_day:>13,.0f}{res.max_drawdown*100:>8.0f}%")

    print("\nThe 'never' cells are not pessimism. At 51.5c the fee-adjusted edge")
    print("is small enough that the low end of the confidence interval is below")
    print("break-even, so the trade stops being a trade. Everything above is")
    print("conditional on a fill price this code cannot observe.")
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

    pl = sub.add_parser("plan", help="simulate start-with-$X make-$Y-per-day")
    pl.add_argument("--start", type=float, default=90.0)
    pl.add_argument("--target", type=float, default=15.0, help="dollars per day")
    pl.add_argument("--edge", type=float, default=0.03, help="assumed edge, e.g. 0.03")
    pl.add_argument("--trades", type=int, default=40, help="trades per day")
    pl.add_argument("--days", type=int, default=90)
    pl.add_argument("--paths", type=int, default=2000)
    pl.add_argument("--maker", action="store_true", help="zero fees (quote inside the spread)")
    pl.set_defaults(func=cmd_plan)

    p2 = sub.add_parser("phase2", help="validate the settlement model and test for edge")
    p2.add_argument("--fixture", action="store_true", help="use synthetic windows, no network")
    p2.add_argument("--windows", type=int, default=2000)
    p2.add_argument("--pages", type=int, default=40)
    p2.add_argument("--edge-bps", type=float, default=0.0, dest="edge_bps",
                    help="make the MODEL wrong by this many bp")
    p2.add_argument("--market-edge", type=float, default=0.0, dest="market_edge",
                    help="make the BOOK cheap by this many bp")
    p2.set_defaults(func=cmd_phase2)

    dash = sub.add_parser("dashboard", help="serve the visual cockpit")
    dash.add_argument("--port", type=int, default=8000)
    dash.add_argument("--demo", action="store_true", help="use synthetic data even if a store exists")
    dash.set_defaults(func=cmd_dashboard)

    sel = sub.add_parser("selectivity", help="can selectivity reach a 70 percent win rate at 50-60c?")
    sel.add_argument("--windows", type=int, default=250)
    sel.add_argument("--min-trades", type=int, default=30)
    sel.add_argument("--seed", type=int, default=11)
    sel.set_defaults(func=cmd_selectivity)

    wr = sub.add_parser("winrate", help="why a 70-80 percent win rate means nothing on its own")
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

    rv = sub.add_parser("reversal", help="measure the 5-minute fade on 1m klines and price it")
    rv.add_argument("--csv", action="append", required=True,
                    help="glob of 1-minute CSVs (repeatable to compare assets)")
    rv.add_argument("--threshold", type=float, default=0.004,
                    help="minimum absolute 5-minute move, as a fraction (default 0.004)")
    rv.add_argument("--fee", type=float, default=0.07, help="taker fee rate (default 0.07)")
    rv.set_defaults(func=cmd_reversal)

    fd = sub.add_parser("fade", help="what a daily income target costs at each fill price")
    fd.add_argument("--csv", action="append", required=True,
                    help="glob of 1-minute CSVs (repeatable)")
    fd.add_argument("--target", type=float, default=15.0, help="dollars per day (default 15)")
    fd.add_argument("--threshold", type=float, default=0.004, help="minimum move (default 0.004)")
    fd.add_argument("--since", type=float, default=None,
                    help="unix ts to start from (default: the period every input covers)")
    fd.set_defaults(func=cmd_fade)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
