"""Command line for the clipping pipeline."""

from __future__ import annotations

import argparse
import json
import subprocess

from . import campaigns as C
from . import plan as P
from . import render as R


def cmd_score(args: argparse.Namespace) -> int:
    raw = json.load(open(args.file))
    if isinstance(raw, dict):
        raw = raw.get("items", raw.get("campaigns", []))
    cands = [C.from_dict(d) for d in raw]
    if not cands:
        print("no campaigns in that file")
        return 2

    print(f"Assuming {args.views:,.0f} views per clip and {args.minutes:.0f} min per clip, "
          f"floor ${args.floor:.0f}/hr\n")
    print(f"{'campaign':<26} {'rate':>9}  {'views':>10}  {'$/clip':>10}  {'$/hr':>8}  verdict")
    print("-" * 82)
    for c in C.rank(cands, args.views, args.minutes, args.floor):
        print(c.row(args.views, args.minutes, args.floor))
        for w in c.warnings(args.views):
            print(f"    ! {w}")

    print(f"\nViews needed per clip to clear ${args.floor:.0f}/hr:")
    for name, need in C.breakeven_views(cands, args.minutes, args.floor):
        if need == float("inf"):
            print(f"  {name[:30]:<30} impossible (rate is zero)")
        else:
            flag = "  <-- you cannot reach this" if need > args.views * 3 else ""
            print(f"  {name[:30]:<30} {need:>9,.0f} views{flag}")

    port = C.Portfolio(cands, clips_per_week=args.clips)
    w = port.weekly(args.views, args.minutes)
    print(f"\n{args.clips} clips/week on the best campaign: "
          f"${w['revenue']:.2f}/wk over {w['hours']:.1f} hr = ${w['hourly']:.2f}/hr "
          f"({w['campaign'][:24]})")
    return 0


def cmd_plan(args: argparse.Namespace) -> int:
    segs = P.plan(args.transcript, args.min_seconds, args.max_seconds, args.top, args.min_gap)
    if not segs:
        print("no candidate segments -- check the transcript has sentence punctuation")
        return 2
    print(f"{len(segs)} candidates from {args.transcript}:\n")
    for s in segs:
        print(s.line())
    print("\nReview these before rendering. The score finds good *structure*; "
          "whether a moment is actually interesting is your call.")
    if args.json:
        json.dump({"segments": [{"start": s.start, "end": s.end, "score": s.score}
                                for s in segs]}, open(args.json, "w"), indent=2)
        print(f"wrote {args.json}")
    return 0


def cmd_render(args: argparse.Namespace) -> int:
    segs = P.plan(args.transcript, args.min_seconds, args.max_seconds, args.top, args.min_gap)
    if args.only:
        segs = [segs[i - 1] for i in args.only if 0 < i <= len(segs)]
    cues = P.load_transcript(args.transcript)
    opts = R.RenderOptions(reframe=args.reframe, captions=not args.no_captions,
                           crf=args.crf, preset=args.preset)

    problems = []
    for s in segs:
        problems += R.validate(s)
    if problems:
        for p in problems:
            print(f"  ! {p}")

    jobs = R.build_batch(args.video, segs, args.outdir, opts, cues, stem=args.stem)
    print(f"{len(jobs)} clip(s) -> {args.outdir}\n")
    for argv, _ass in jobs:
        if args.run:
            print(" $ " + " ".join(argv[:6]) + f" ... -> {argv[-1]}")
            r = subprocess.run(argv)
            if r.returncode != 0:
                print(f"   ffmpeg failed with {r.returncode}")
                return 1
        else:
            print("ffmpeg " + " ".join(f"'{a}'" if " " in a else a for a in argv[1:]))
            print()
    if not args.run:
        print("(dry run -- pass --run to encode)")
    return 0



def cmd_run(args: argparse.Namespace) -> int:
    """Campaign + video + transcript -> postable clips and a manifest."""
    from . import job as J

    raw = json.load(open(args.campaigns))
    if isinstance(raw, dict):
        raw = raw.get("items", raw.get("campaigns", []))
    cands = [C.from_dict(d) for d in raw]
    if not cands:
        print("no campaigns in that file")
        return 2

    if args.campaign:
        match = [c for c in cands if args.campaign.lower() in c.name.lower()]
        if not match:
            print(f"no campaign matching {args.campaign!r}. "
                  f"Options: {', '.join(c.name for c in cands)}")
            return 2
        chosen = match[0]
    else:
        chosen = C.rank(cands, args.views, args.minutes, args.floor)[0]

    hourly = chosen.dollars_per_hour(args.views, args.minutes)
    print(f"campaign: {chosen.name}  ${chosen.cpm:.2f}/1k  ->  ${hourly:.2f}/hr "
          f"at {args.views:,.0f} views/clip")
    if hourly < args.floor:
        print(f"  ! below your ${args.floor:.0f}/hr floor -- this campaign is not "
              f"worth the hours, but rendering anyway so you can judge the output")
    print()

    opts = R.RenderOptions(reframe=args.reframe, captions=not args.no_captions,
                           crf=args.crf, preset=args.preset)
    man = J.run(chosen, args.video, args.transcript, args.outdir, args.views,
                top=args.top, min_seconds=args.min_seconds, max_seconds=args.max_seconds,
                opts=opts, dry_run=not args.run, force=args.force,
                upload_url=args.upload_url)
    print(J.summary(man))
    if not args.run:
        print("\n(dry run -- pass --run to encode)")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="clipper", description="Whop clipping pipeline")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("score", help="rank campaigns by dollars per hour")
    s.add_argument("--file", required=True, help="JSON list of campaigns")
    s.add_argument("--views", type=float, required=True,
                   help="REAL views a typical clip of yours gets -- do not flatter this")
    s.add_argument("--minutes", type=float, default=C.DEFAULT_MINUTES_PER_CLIP)
    s.add_argument("--clips", type=int, default=10, help="clips per week")
    s.add_argument("--floor", type=float, default=8.0, help="dollars/hour you require")
    s.set_defaults(func=cmd_score)

    pl = sub.add_parser("plan", help="find candidate clips in a transcript")
    pl.add_argument("--transcript", required=True, help=".srt or .vtt")
    pl.add_argument("--min-seconds", type=float, default=18.0, dest="min_seconds")
    pl.add_argument("--max-seconds", type=float, default=48.0, dest="max_seconds")
    pl.add_argument("--top", type=int, default=6)
    pl.add_argument("--min-gap", type=float, default=30.0, dest="min_gap")
    pl.add_argument("--json", default=None)
    pl.set_defaults(func=cmd_plan)

    r = sub.add_parser("render", help="build (or run) the ffmpeg commands")
    r.add_argument("--video", required=True)
    r.add_argument("--transcript", required=True)
    r.add_argument("--outdir", required=True)
    r.add_argument("--reframe", choices=["pad", "crop"], default="pad")
    r.add_argument("--no-captions", action="store_true", dest="no_captions")
    r.add_argument("--only", type=int, nargs="*", default=None, help="1-based segment picks")
    r.add_argument("--crf", type=int, default=20)
    r.add_argument("--preset", default="medium")
    r.add_argument("--stem", default="clip")
    r.add_argument("--run", action="store_true", help="actually call ffmpeg")
    r.add_argument("--min-seconds", type=float, default=18.0, dest="min_seconds")
    r.add_argument("--max-seconds", type=float, default=48.0, dest="max_seconds")
    r.add_argument("--top", type=int, default=6)
    r.add_argument("--min-gap", type=float, default=30.0, dest="min_gap")
    r.set_defaults(func=cmd_render)
    rn = sub.add_parser("run", help="one campaign, one video -> postable clips + manifest")
    rn.add_argument("--campaigns", required=True, help="JSON of campaigns")
    rn.add_argument("--campaign", default=None, help="name substring; default = best by $/hr")
    rn.add_argument("--video", required=True)
    rn.add_argument("--transcript", required=True)
    rn.add_argument("--outdir", required=True)
    rn.add_argument("--views", type=float, required=True, help="YOUR real views per clip")
    rn.add_argument("--minutes", type=float, default=C.DEFAULT_MINUTES_PER_CLIP)
    rn.add_argument("--floor", type=float, default=8.0)
    rn.add_argument("--reframe", choices=["pad", "crop"], default="pad")
    rn.add_argument("--no-captions", action="store_true", dest="no_captions")
    rn.add_argument("--crf", type=int, default=20)
    rn.add_argument("--preset", default="medium")
    rn.add_argument("--top", type=int, default=6)
    rn.add_argument("--min-seconds", type=float, default=18.0, dest="min_seconds")
    rn.add_argument("--max-seconds", type=float, default=48.0, dest="max_seconds")
    rn.add_argument("--upload-url", default=None, dest="upload_url",
                    help="presigned PUT template, e.g. https://r2.example/{name}")
    rn.add_argument("--force", action="store_true", help="re-render cached clips")
    rn.add_argument("--run", action="store_true", help="actually call ffmpeg")
    rn.set_defaults(func=cmd_run)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)
