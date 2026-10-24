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



def cmd_scan(args: argparse.Namespace) -> int:
    """Score a snapshot of live two-way markets against the calibrated model.

    Input is a JSON list of market dicts (as returned by
    ``gamma-api.polymarket.com/markets``). Use --live to fetch instead.
    """
    import json
    from . import band, live, tennis

    if args.live:
        markets = live.sports_markets(limit=args.limit)
    else:
        if not args.file:
            print("need --file snapshot.json or --live")
            return 2
        markets = json.load(open(args.file))

    if not args.asof:
        import datetime as _dt
        args.asof = int(_dt.date.today().strftime("%Y%m%d"))
    ratings = tennis.load_ratings(args.ratings)
    known = ratings.overall

    all_legs: list[band.Leg] = []
    unmatched = 0
    for mk in markets:
        legs = live.legs_from_market(mk)
        if len(legs) != 2:
            continue
        joined = []
        for lg in legs:
            player, conf = tennis.match_player(lg["name"], known)
            if player is None or conf < args.min_conf:
                unmatched += 1
                joined = []
                break
            joined.append((lg, player))
        if len(joined) != 2:
            continue
        (a, pa), (b, pb) = joined
        surface = args.surface or ""
        asof = args.asof
        p_a = tennis.predict_asof(ratings, pa, pb, asof, surface)
        p_b = tennis.predict_asof(ratings, pb, pa, asof, surface)
        all_legs.append(band.Leg(a["event"], a["name"], a["price"], p_a))
        all_legs.append(band.Leg(b["event"], b["name"], b["price"], p_b))

    inband = [l for l in all_legs if l.banded]
    print(f"markets read {len(markets)}   legs scored {len(all_legs)}   "
          f"unmatched names {unmatched}")
    print(f"legs in {band.BAND_LO:.0%}-{band.BAND_HI:.0%} band: {len(inband)}")
    if not inband:
        print("nothing in the band -- do nothing.")
        return 0

    print()
    for leg in sorted(inband, key=lambda l: l.edge, reverse=True)[: args.top]:
        print(" " + leg.line())

    picks = band.rank(all_legs, min_edge=args.min_edge)
    print()
    if not picks:
        print(f"No leg clears {args.min_edge:.0%} edge. Do nothing.")
    else:
        print(f"{len(picks)} leg(s) clear {args.min_edge:.0%} edge:")
        for l in picks[: args.top]:
            print("  " + l.line())
    return 0



def cmd_predict(args: argparse.Namespace) -> int:
    """Three-way soccer prediction for one fixture: home / draw / away.

    Ratings come from every match in --dir (closing odds are NOT read here --
    the model is odds-free), fitted on the training window and then walked
    forward so the fixture's ratings are current.
    """
    from . import band
    from .data import load_dir
    from .elo import build as build_model
    from .backtest import split

    matches = load_dir(args.dir)
    if not matches:
        print(f"no matches in {args.dir}")
        return 2
    cut = args.cut
    train, _test = split(matches, cut)
    model = build_model(train, matches)

    home, away = args.home, args.away
    table = model.table
    if not table:
        print("model has no rating table")
        return 2
    probs = model.predict(home, away)

    print(f"{home} vs {away}")
    print(f"  Elo {model.rating(home):.0f} vs {model.rating(away):.0f}   "
          f"(diff {model.elo_diff(home, away):+.0f})")
    print()
    labels = ["HOME", "DRAW", "AWAY"]
    names = [home, "Draw", away]
    prices = [args.price_home, args.price_draw, args.price_away]
    print(f"  {'side':<14} {'model':>7} {'price':>7} {'need':>7} {'edge':>8}  verdict")
    picks = []
    for i, lab in enumerate(labels):
        leg = None
        if prices[i] is not None:
            leg = band.Leg(f"{home} v {away}", lab, prices[i], probs[i])
            picks.append(leg)
            print(f"  {names[i]:<14} {probs[i]*100:6.1f}% {prices[i]*100:6.1f}c "
                  f"{leg.breakeven*100:6.1f}% {leg.edge*100:+7.1f}%  {leg.verdict()}")
        else:
            print(f"  {names[i]:<14} {probs[i]*100:6.1f}%       -       -        -  (no price)")
    total = sum(probs)
    print(f"\n  model sums to {total:.3f}")
    if picks:
        good = band.rank(picks, min_edge=args.min_edge)
        print()
        if good:
            for l in good:
                print("  BUY " + l.line())
        else:
            print(f"  No side clears {args.min_edge:.0%} edge in {band.BAND_LO:.0%}-{band.BAND_HI:.0%}. Do nothing.")
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
    sc = sub.add_parser("scan", help="score live two-way markets in the 50-65c band")
    sc.add_argument("--file", default=None, help="JSON snapshot of gamma markets")
    sc.add_argument("--live", action="store_true", help="fetch from gamma-api now")
    sc.add_argument("--limit", type=int, default=200)
    sc.add_argument("--ratings", default="/tmp/ten_ratings.json", help="saved tennis ratings")
    sc.add_argument("--sport", default="tennis", choices=["tennis"])
    sc.add_argument("--surface", default=None, help="Hard/Clay/Grass or blank")
    sc.add_argument("--min-conf", type=float, default=0.7, dest="min_conf",
                    help="name-match confidence floor")
    sc.add_argument("--min-edge", type=float, default=0.02, dest="min_edge")
    sc.add_argument("--top", type=int, default=15)
    sc.add_argument("--asof", type=int, default=0,
                    help="YYYYMMDD for inactivity decay (default: today)")
    sc.set_defaults(func=cmd_scan)

    pr = sub.add_parser("predict", help="three-way soccer prediction for one fixture")
    pr.add_argument("--dir", required=True, help="directory of football-data CSVs")
    pr.add_argument("--home", required=True)
    pr.add_argument("--away", required=True)
    pr.add_argument("--cut", default="2020-07-01", help="training cutoff YYYY-MM-DD")
    pr.add_argument("--price-home", type=float, default=None, dest="price_home")
    pr.add_argument("--price-draw", type=float, default=None, dest="price_draw")
    pr.add_argument("--price-away", type=float, default=None, dest="price_away")
    pr.add_argument("--min-edge", type=float, default=0.02, dest="min_edge")
    pr.set_defaults(func=cmd_predict)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not isinstance(getattr(args, "cut", None), tuple):
        if getattr(args, "cut", None):
            y, m, d = (int(x) for x in args.cut.split("-"))
            args.cut = (y, m, d)
    return args.func(args)
