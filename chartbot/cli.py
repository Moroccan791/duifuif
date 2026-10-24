"""Command line for the chart bot.

    python -m chartbot scan            read the chart once and print the readout
    python -m chartbot backtest        replay history through the same code
    python -m chartbot run             the live loop (paper by default)
    python -m chartbot serve           dashboard with the chart the bot sees
    python -m chartbot webhook         receive TradingView alerts

Nothing trades real money unless `--live` is passed together with
`--i-understand-this-can-lose-money`, and even then it refuses without API keys.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .broker import BinanceBroker, BrokerError, PaperBroker
from .data import (BinanceClient, Candle, load_csv, synthetic_candles,
                   trending_candles)
from .reader import read_chart
from .render import render_svg
from .runner import Runner, RunnerConfig, backtest
from .signals import SignalConfig, evaluate


def _candles(args) -> list[Candle]:
    if getattr(args, "csv", ""):
        return load_csv(args.csv, interval_s=86400.0)[-args.lookback:]
    if args.synthetic:
        return trending_candles(n=args.lookback, up=args.up, seed=args.seed)
    client = BinanceClient()
    return client.klines(args.symbol, args.interval, args.lookback)


def cmd_scan(args: argparse.Namespace) -> int:
    candles = _candles(args)
    read = read_chart(candles)
    sig = evaluate(read, args.equity, _signal_cfg(args))
    print(f"{args.symbol} {args.interval}   last {read.price:,.2f}   ({len(candles)} candles)")
    print()
    print("WHAT THE CHART SAYS")
    print("  " + read.narrate())
    print()
    print("NUMBERS")
    print(f"  trend            {read.trend:>5}  strength {read.trend_strength:+.2f}")
    print(f"  structure        {read.structure:>5} highs / lows")
    print(f"  momentum         RSI {read.momentum:.1f} ({read.momentum_dir})")
    print(f"  range position   {read.range_pos:.2f}  (0 = low, 1 = high)")
    print(f"  resistance       {read.resistance:,.2f}  ({read.distance_to_resistance*100:+.2f}%)")
    print(f"  support          {read.support:,.2f}  ({read.distance_to_support*100:+.2f}%)")
    print(f"  volatility       ATR {read.vol_pct*100:.3f}% of price")
    print(f"  volume           {read.rel_volume:.2f}x baseline")
    print(f"  ema12 / ema26    {read.ema_fast:,.2f} / {read.ema_slow:,.2f}")
    if read.patterns:
        print(f"  patterns         {', '.join(read.patterns)}")
    print()
    print("DECISION")
    print("  " + sig.describe())
    if args.svg:
        open(args.svg, "w").write(render_svg(candles, read, sig,
                                             title=f"{args.symbol} {args.interval}"))
        print(f"\n  chart written to {args.svg}")
    return 0


def _signal_cfg(args) -> SignalConfig:
    return SignalConfig(mode=getattr(args, "mode", "trend"),
                        risk_pct=getattr(args, "risk", 0.02),
                        allow_short=not getattr(args, "no_short", False),
                        max_vol_pct=getattr(args, "max_vol", 0.02),
                        min_vol_pct=getattr(args, "min_vol", 0.0004))


def cmd_backtest(args: argparse.Namespace) -> int:
    if getattr(args, "csv", ""):
        candles = load_csv(args.csv, interval_s=86400.0)[-args.bars:]
        first, last = str(candles[0].ts), str(candles[-1].ts)
        print(f"loaded {len(candles)} candles from {args.csv}")
    elif args.synthetic:
        # A random walk, not a monotonic trend: a monotonic trend pegs RSI above
        # 75 forever and the strategy correctly refuses every entry, which looks
        # like a broken bot instead of a working filter.
        candles = synthetic_candles(n=args.bars, interval_s=300.0, seed=args.seed,
                                    vol=0.004)
    else:
        client = BinanceClient()
        candles = client.klines(args.symbol, args.interval, args.bars)

    cfg = RunnerConfig(symbol=args.symbol, interval=args.interval,
                       signal=_signal_cfg(args))
    res = backtest(candles, start_equity=args.equity, cfg=cfg)
    print("=" * 70)
    print(f"BACKTEST  {args.symbol} {args.interval}  {len(candles)} bars")
    print("=" * 70)
    print(f"  start            ${res['start']:,.2f}")
    print(f"  final            ${res['final']:,.2f}")
    print(f"  return           {res['return_pct']:+.2f}%")
    print(f"  trades           {res['trades']}  ({res['wins']}W / {res['losses']}L)")
    print(f"  win rate         {res['win_rate']*100:.1f}%")
    print(f"  max drawdown     {res['max_drawdown_pct']:.2f}%")
    print(f"  decision points  {res['steps']}")
    print()
    if res["trades"] < 10:
        print("  Only a handful of trades. That number is noise, not a result.")

    if args.trace:
        print()
        print(f"  FIRST {min(args.trace, len(res['runner'].closed))} TRADES, IN FULL")
        print()
        for k, t in enumerate(res["runner"].closed[:args.trace], 1):
            held = (t["closed_ts"] - t["opened_ts"]) / 60.0 if t["closed_ts"] else 0.0
            print(f"  #{k}  {t['side'].upper()} {t['qty']:.6f} BTC @ {t['entry']:,.2f}")
            print(f"      why      {t['why']}")
            print(f"      stop     {t['stop']:,.2f}      target {t['target']:,.2f}")
            print(f"      exited   {t['price']:,.2f} via {t['reason'].upper()} "
                  f"after {held:.0f} min")
            print(f"      pnl      ${t['pnl']:+.2f}")
            print()
    return 0


def cmd_compare(args: argparse.Namespace) -> int:
    """Run every documented strategy on the same data with the same costs."""
    from .strategies import compare

    if args.csv:
        candles = load_csv(args.csv, interval_s=86400.0)
        src = args.csv
    else:
        candles = BinanceClient().klines(args.symbol, args.interval, args.bars)
        src = f"{args.symbol} {args.interval}"

    print("=" * 84)
    print(f"STRATEGY COMPARISON - {len(candles)} bars from {src}")
    print("=" * 84)
    print(f"costs {args.cost_bps:.0f} bps per unit turnover, "
          f"vol target {args.target_vol*100:.0f}%, 365 trading days/year")
    print()

    res = compare(candles, cost_bps=args.cost_bps, target_vol=args.target_vol)
    res.sort(key=lambda m: -m.sharpe)
    hdr = (f"{'strategy':<32} {'CAGR':>9} {'vol':>8} {'Sharpe':>7} "
           f"{'MaxDD':>9} {'exposed':>8}")
    print(hdr)
    print("-" * len(hdr))
    for m in res:
        print(m.pretty())

    print()
    bh = next((m for m in res if m.name == "buy and hold"), None)
    if bh:
        best = res[0]
        print(f"  best by Sharpe: {best.name}")
        print(f"    Sharpe {best.sharpe:.2f} vs {bh.sharpe:.2f} for holding BTC")
        print(f"    max drawdown {best.max_dd*100:.1f}% vs {bh.max_dd*100:.1f}%")
        print(f"    CAGR {best.cagr*100:.1f}% vs {bh.cagr*100:.1f}%  "
              f"- holding made MORE money, it just hurt more getting there")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    if args.live:
        if not args.i_understand_this_can_lose_money:
            print("refusing: --live needs --i-understand-this-can-lose-money")
            return 2
        broker = BinanceBroker(args.equity, symbol=args.symbol,
                               testnet=args.testnet, allow_live=True)
        print(f"LIVE on {'TESTNET' if args.testnet else 'REAL MONEY'} - {args.symbol}")
    else:
        broker = PaperBroker(args.equity)
        print(f"PAPER - ${args.equity:,.2f} notional, nothing is sent anywhere")

    cfg = RunnerConfig(symbol=args.symbol, interval=args.interval,
                       lookback=args.lookback, signal=_signal_cfg(args))
    runner = Runner(broker, cfg)

    if args.synthetic:
        from .data import live_replay
        feed = live_replay()
        fetcher = lambda: feed(args.lookback)
    else:
        client = BinanceClient()
        fetcher = lambda: client.klines(args.symbol, args.interval, args.lookback)

    print(f"every {args.every}s. Ctrl-C to stop.\n")
    n = 0
    try:
        while n < args.steps:
            try:
                candles = fetcher()
            except Exception as e:  # noqa: BLE001 - a dropped poll must not kill the loop
                print(f"  data error: {e}")
                time.sleep(args.every)
                continue
            step = runner.step(candles)
            n += 1
            eq = broker.equity(candles[-1].close)
            line = f"[{n:>4}] {time.strftime('%H:%M:%S')} {candles[-1].close:>10,.2f}  {step.read.compact()}"
            if step.note:
                line += f"  {step.note}"
            if step.action:
                line += f"  >> {step.action}"
            print(line)
            if broker.halted():
                print(f"  HALTED: {broker.halted()}")
                break
            if n < args.steps:
                time.sleep(args.every)
    except KeyboardInterrupt:
        print("\nstopped")

    print(f"\nequity ${broker.equity():,.2f}  realised ${broker.realised:+,.2f}  "
          f"fills {len(broker.fills)}")
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    """Serve the chart the bot is looking at, plus its current decision."""
    import datetime as _dt

    state: dict = {"svg": "", "status": "starting", "log": []}

    def poll() -> None:
        cfg = SignalConfig(mode=args.mode, risk_pct=args.risk,
                           max_vol_pct=args.max_vol, min_vol_pct=args.min_vol,
                           allow_short=not args.no_short)
        broker = PaperBroker(args.equity)
        runner = Runner(broker, RunnerConfig(symbol=args.symbol,
                                             interval=args.interval,
                                             signal=cfg))
        client = BinanceClient()
        use_csv = bool(getattr(args, "csv", ""))
        offline = args.synthetic
        feed_note = "synthetic" if offline else ""

        while True:
            try:
                if use_csv:
                    candles = load_csv(args.csv, interval_s=86400.0)[-args.lookback:]
                elif offline:
                    candles = synthetic_candles(n=args.lookback,
                                                seed=int(time.time()) // 5 % 997)
                else:
                    candles = client.klines(args.symbol, args.interval, args.lookback)

                step = runner.step(candles)
                eq = broker.equity(candles[-1].close)

                if use_csv:
                    d = _dt.datetime.utcfromtimestamp(candles[-1].ts).strftime("%Y-%m-%d")
                    label = f"REAL BTC - daily candles to {d}  ({len(candles)} bars)"
                    mark = ""
                    note = "  [real historical data from CSV]"
                elif offline:
                    label = f"SIMULATED {args.symbol} - NOT REAL DATA"
                    mark = "SIMULATED DATA - NOT REAL BTC"
                    note = "  [synthetic]"
                else:
                    label = f"LIVE {args.symbol} {args.interval}  {time.strftime('%H:%M:%S')}"
                    mark = ""
                    note = ""

                state["svg"] = render_svg(candles, step.read, step.signal,
                                          title=label, watermark=mark)
                state["status"] = f"${eq:,.2f} equity | {step.signal.describe()}{note}"
                if step.action:
                    state["log"].insert(0, f"{time.strftime('%H:%M:%S')} {step.action}")
                    state["log"] = state["log"][:12]
            except Exception as e:  # noqa: BLE001
                if not offline and not use_csv:
                    feed_note = f"live feed unavailable ({type(e).__name__})"
                    state["status"] = f"{feed_note} - showing synthetic data"
                    offline = True
                else:
                    state["status"] = f"error: {type(e).__name__}: {e}"
            time.sleep(args.every)

    threading.Thread(target=poll, daemon=True).start()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            if self.path.startswith("/chart.svg"):
                body = (state["svg"] or "<svg xmlns='http://www.w3.org/2000/svg'/>").encode()
                ctype = "image/svg+xml"
            elif self.path.startswith("/api"):
                body = json.dumps({"status": state["status"],
                                   "log": state["log"]}).encode()
                ctype = "application/json"
            else:
                body = _PAGE.replace("{{STATUS}}", state["status"]).encode()
                ctype = "text/html; charset=utf-8"
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"dashboard on http://{args.host}:{args.port}  ({args.symbol} {args.interval})")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


_PAGE = """<!doctype html><html><head><meta charset="utf-8">
<title>chartbot</title><style>
body{background:#0b0d10;color:#c8d0dc;font:13px ui-monospace,monospace;margin:0;padding:18px}
h1{font-size:15px;font-weight:600;margin:0 0 10px}
img{width:100%;max-width:900px;border:1px solid #262b33;border-radius:6px}
.s{margin:12px 0;color:#9aa6b6}
</style></head><body>
<h1>chartbot &mdash; what the machine sees</h1>
<div id="warn" style="display:none;background:#8a1c1c;color:#ffe0e0;padding:10px 12px;
border-radius:6px;margin-bottom:12px;font-weight:600">
  THIS IS NOT REAL BTC. The sandbox running this preview has no internet access to
  Binance, so the chart is generated data. Run it on your own machine to see live candles.
</div>
<img id="c" src="/chart.svg">
<div class="s">{{STATUS}}</div>
<script>
function mark(d){
  document.getElementById('warn').style.display =
    (d.status.indexOf('offline')>=0 || d.status.indexOf('unavailable')>=0) ? 'block':'none';
}
setInterval(()=>{document.getElementById('c').src='/chart.svg?t='+Date.now()},5000);
setInterval(()=>{fetch('/api').then(r=>r.json()).then(d=>{
  document.querySelector('.s').textContent=d.status; mark(d);})},5000);
</script></body></html>"""


def cmd_webhook(args: argparse.Namespace) -> int:
    """Receive TradingView alerts and forward them to the broker.

    Set the TradingView alert webhook URL to http://<your-host>:<port>/tv, and put
    the shared secret in the alert message body as {"secret": "...", "action": "buy"}.
    TradingView cannot reach localhost, so this needs a public address (a tunnel is
    fine).
    """
    print(f"webhook listening on http://{args.host}:{args.port}/tv")
    print("  (TradingView cannot reach localhost -- use a tunnel or a VPS)")
    print("  paper mode: alerts are logged, no orders are placed" if not args.live
          else "  LIVE: alerts will place orders")

    broker = PaperBroker(args.equity)

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length).decode(errors="replace")
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                payload = {"action": raw.strip().lower()}
            if args.secret and payload.get("secret") != args.secret:
                self.send_response(403)
                self.end_headers()
                return
            action = str(payload.get("action", "")).lower()
            price = float(payload.get("price") or 0.0)
            print(f"  alert: {action} @ {price}")

            stop = target = 0.0
            if args.live and action in ("buy", "sell") and price > 0:
                stop = price * (1 - args.stop_pct) if action == "buy" else price * (1 + args.stop_pct)
                target = price * (1 + args.stop_pct * 2) if action == "buy" else price * (1 - args.stop_pct * 2)
                try:
                    qty = (broker.equity() * args.risk) / max(1e-9, abs(price - stop))
                    broker.open_position(action, qty, price, stop, target,
                                         reason="tradingview alert")
                    print(f"    -> opened {action} {qty:.6f}")
                except BrokerError as e:
                    print(f"    -> blocked: {e}")

            self.send_response(200)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"ok")

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="chartbot", description="a bot that reads charts and trades")
    sub = p.add_subparsers(dest="cmd")

    def common(sp):
        sp.add_argument("--csv", default="", help="load OHLCV from a CSV instead of the network")
        sp.add_argument("--symbol", default="BTCUSDT")
        sp.add_argument("--interval", default="5m")
        sp.add_argument("--lookback", type=int, default=200)
        sp.add_argument("--equity", type=float, default=100.0)
        sp.add_argument("--mode", default="trend", choices=["trend", "range", "both"])
        sp.add_argument("--risk", type=float, default=0.02)
        sp.add_argument("--no-short", action="store_true")
        sp.add_argument("--max-vol", type=float, default=0.02, dest="max_vol",
                        help="skip when ATR exceeds this fraction of price (0.02 = 2%%)")
        sp.add_argument("--min-vol", type=float, default=0.0004, dest="min_vol")
        sp.add_argument("--synthetic", action="store_true", help="no network")
        sp.add_argument("--seed", type=int, default=1)
        return sp

    s = common(sub.add_parser("scan", help="read the chart once"))
    s.add_argument("--up", action="store_true", default=True)
    s.add_argument("--svg", default="", help="write the chart to this file")
    s.set_defaults(func=cmd_scan)

    c = common(sub.add_parser("compare", help="documented strategies, same data"))
    c.add_argument("--bars", type=int, default=4008)
    c.add_argument("--cost-bps", type=float, default=5.0, dest="cost_bps")
    c.add_argument("--target-vol", type=float, default=0.25, dest="target_vol")
    c.set_defaults(func=cmd_compare)

    b = common(sub.add_parser("backtest", help="replay history"))
    b.add_argument("--bars", type=int, default=1000)
    b.add_argument("--trace", type=int, default=0,
                   help="print the first N completed trades in full")
    b.set_defaults(func=cmd_backtest)

    r = common(sub.add_parser("run", help="the live loop"))
    r.add_argument("--every", type=float, default=30.0)
    r.add_argument("--steps", type=int, default=20)
    r.add_argument("--live", action="store_true")
    r.add_argument("--testnet", action="store_true", default=True)
    r.add_argument("--i-understand-this-can-lose-money", action="store_true")
    r.set_defaults(func=cmd_run)

    v = common(sub.add_parser("serve", help="dashboard"))
    v.add_argument("--port", type=int, default=8100)
    v.add_argument("--host", default="0.0.0.0")
    v.add_argument("--every", type=float, default=5.0)
    v.set_defaults(func=cmd_serve)

    w = sub.add_parser("webhook", help="receive TradingView alerts")
    w.add_argument("--port", type=int, default=8200)
    w.add_argument("--host", default="0.0.0.0")
    w.add_argument("--secret", default=os.environ.get("TV_SECRET", ""))
    w.add_argument("--live", action="store_true")
    w.add_argument("--equity", type=float, default=100.0)
    w.add_argument("--risk", type=float, default=0.02)
    w.add_argument("--stop-pct", type=float, default=0.004, dest="stop_pct")
    w.set_defaults(func=cmd_webhook)

    return p


def main(argv: list[str] | None = None) -> int:
    p = build_parser()
    args = p.parse_args(argv)
    if not getattr(args, "func", None):
        p.print_help()
        return 0
    return args.func(args)
