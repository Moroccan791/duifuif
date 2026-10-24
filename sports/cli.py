"""python -m sports backtest  |  check  |  sides"""

from __future__ import annotations

import argparse

from .backtest import (bet_disagreements, evaluate, roi, split,
                       walk_forward)
from .data import load_dir
from .edge import devig, measure_sides
from .elo import EloModel


def cmd_backtest(args: argparse.Namespace) -> int:
    matches = load_dir(args.dir)
    if not matches:
        print(f"no matches found in {args.dir}")
        return 1
    train, test = split(matches, args.cut)
    print(f"{len(matches):,} matches  {matches[0].date} .. {matches[-1].date}")
    print(f"train {len(train):,}   test {len(test):,} (from {args.cut})\n")

    model = EloModel()
    model.walk(train)          # ratings as they stood at the cut
    model.fit(train)           # calibration learned on the training window only
    rows = walk_forward(model, test)      # predict, then update; once, up front
    print(evaluate(test, rows).pretty())

    print("\nbetting the model's disagreements with the close:")
    print(f"{'edge needed':>12}{'bets':>9}{'accuracy':>10}{'ROI':>9}")
    for edge in (0.0, 0.02, 0.04, 0.06, 0.10):
        n, acc, r = roi(bet_disagreements(test, rows, edge))
        if n:
            print(f"{edge:>11.0%}{n:>9,}{acc:>10.2%}{r:>9.1%}")

    margins = [m.overround for m in test]
    print(f"\nmean overround on the closing line: "
          f"{sum(margins)/len(margins):.2%} — you start each bet that far behind")
    return 0


def cmd_sides(args: argparse.Namespace) -> int:
    """Price a fixture against a sharp line. This is the part that trades."""
    odds = [args.home, args.draw, args.away]
    sharp = devig(odds)
    print(f"sharp line {odds}  (overround {sum(1/o for o in odds)-1:.2%})")
    print("de-vigged:  home {:.2%}  draw {:.2%}  away {:.2%}\n".format(*sharp))

    prices = {}
    for side, v in (("home", args.price_home), ("draw", args.price_draw),
                    ("away", args.price_away)):
        if v is not None:
            prices[{"home": 0, "draw": 1, "away": 2}[side]] = v
    if not prices:
        print("pass --price-home/--price-draw/--price-away to score the other side")
        return 0

    print(f"{'side':<7}{'sharp':>8}{'price':>8}{'edge/$':>9}{'take below':>12}{'verdict':>10}")
    for row in measure_sides(odds, prices, args.fee):
        print(f"{row['side']:<7}{row['sharp_prob']:>8.2%}{row['price']:>8.2f}"
              f"{row['edge']:>8.1%}{row['breakeven']:>12.3f}"
              f"{'TAKE' if row['take'] else 'pass':>10}")
    print("\nA side is only worth taking below its break-even price. If both")
    print("venues agree, every number above is negative and the answer is no.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="sports")
    sub = p.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("backtest", help="model vs closing line on real matches")
    b.add_argument("--dir", required=True, help="directory of football-data CSVs")
    b.add_argument("--cut", default="2020-07-01",
                   help="YYYY-MM-DD split; everything before is training")
    b.set_defaults(func=cmd_backtest)

    s = sub.add_parser("sides", help="score a Polymarket price against a sharp line")
    s.add_argument("--home", type=float, required=True, help="sharp decimal odds, home")
    s.add_argument("--draw", type=float, required=True)
    s.add_argument("--away", type=float, required=True)
    s.add_argument("--price-home", type=float, default=None, dest="price_home")
    s.add_argument("--price-draw", type=float, default=None, dest="price_draw")
    s.add_argument("--price-away", type=float, default=None, dest="price_away")
    s.add_argument("--fee", type=float, default=0.0, help="taker fee rate (default 0)")
    s.set_defaults(func=cmd_sides)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not isinstance(getattr(args, "cut", None), tuple):
        if getattr(args, "cut", None):
            y, m, d = (int(x) for x in args.cut.split("-"))
            args.cut = (y, m, d)
    return args.func(args)
