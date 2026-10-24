# Can we build an automated Whop clipper?

Short answer: **three of your four requirements are buildable, one is not, and
one of them sits on Vercel where it physically cannot run.** This file is the
honest breakdown, because the version I could have written -- a diagram of a
beautiful pipeline -- would have wasted your money.

What you asked for:

1. finds campaigns at Whop
2. does the video editing
3. posts too
4. runs on a website, deployed to Vercel

Scored against what I verified:

| # | requirement | verdict | why |
|---|-------------|---------|-----|
| 1 | find campaigns | **buildable, fragile** | no public API; scraping only |
| 2 | edit video | **buildable, and this is the win** | ffmpeg is deterministic |
| 3 | post automatically | **no** | OAuth + per-platform approval, and brands reject slop |
| 4 | run on Vercel | **UI yes, video no** | 4.5 MB body cap, 5 min, 1 vCPU |

---

## 1. Finding campaigns: buildable, but there is no API

Whop's Content Rewards has **no public API**. Verified: `contentrewards.com`
publishes none, and every working scraper on the market (five different Apify
actors) is built on "the site's internal Next.js server actions and Whop's
GraphQL endpoints." That means:

- There is no supported endpoint to call. Anything you build is scraping an
  undocumented internal interface.
- It breaks whenever Whop ships a frontend change.
- Automated access may violate Whop's terms, which is a real risk to an account
  you intend to earn from.

**The honest options.** Pay for an Apify actor (they exist, cost per run, and
someone else maintains the breakage) and have your code consume their clean
output. Or export a JSON by hand once a week -- which for a market of a few
dozen live campaigns is genuinely fine and costs nothing.

`clipper/campaigns.py` does not care where the JSON came from. That was
deliberate: the interesting work is the economics, not the scraping.

## 2. The editing: this is the part that actually removes your hurdle

You said you don't want editing hurdles. This is where automation delivers, and
it delivers fully, because editing is the one part of clipping with a *correct
answer*. Given a transcript, cutting on sentence boundaries, reframing to 9:16,
burning readable captions and normalising loudness is deterministic work.

`clipper plan` turns a transcript into ranked clip candidates.
`clipper render` turns those into ffmpeg commands.

    python -m clipper plan  --transcript talk.srt --top 6
    python -m clipper render --video talk.mp4 --transcript talk.srt --outdir out --run

Three real decisions baked in:

- **Reframe `pad` by default, not `crop`.** Cropping takes the centre 9:16 slice
  and decapitates the second person in any two-shot podcast. A blur is merely
  unfashionable; a wrong crop is unwatchable.
- **Captions burned in as ASS, not uploaded as a sidecar.** Platforms handle
  sidecar captions inconsistently and mostly silently drop them.
- **`loudnorm` to -14 LUFS.** A quiet clip sounds broken next to everything else
  in a feed, and no amount of editing skill fixes it later.

What it does **not** do: tell you which moment is interesting. The scorer uses
speech density, hook language and payoff patterns -- proxies. A hook-word
dictionary cannot tell whether something is funny. Treat the output as a
shortlist to review. That is still most of the labour saved, because watching 90
minutes of podcast to find four moments is the slow part, not the cutting.

## 3. Posting automatically: no, and this would break your account

Three separate walls:

- **TikTok, Instagram and YouTube each require OAuth and a reviewed app.**
  TikTok's Content Posting API needs explicit approval; Instagram Reels needs a
  business account and Graph API access; YouTube needs OAuth verification. Three
  approval processes, three sets of quota rules.
- **Whop brands review submissions by hand.** Views are verified through the
  platform APIs, then a human decides. Auto-generated clips that miss the brief
  get rejected *after* they have views -- the reward is not yours.
- **Automated posting is what platform ToS enforcement is built to catch.** The
  account is the asset. Losing it costs more than the automation saves.

The correct shape is **semi-automatic**: the pipeline produces finished vertical
clips plus a caption, and you tap post. That is roughly 90 seconds of human work
per clip against 20 minutes of editing, which is the whole point.

## 4. Vercel: the UI can live there, the video cannot

This is the requirement that cannot work as described, and it is not a
configuration problem.

| limit | value | consequence |
|-------|-------|-------------|
| request/response body | **4.5 MB** | the video can never pass through the function |
| max duration (Hobby) | **300 s** | one 1080p encode at 1 vCPU ≈ real time |
| memory / CPU (Hobby) | 2 GB / **1 vCPU** | libx264 runs at roughly 1x realtime |
| bundle (uncompressed) | 250 MB | ffmpeg is 76 MB, so it fits -- not the problem |

A 30-second 9:16 encode at 1 vCPU takes on the order of a minute, so the timeout
is survivable. **The 4.5 MB cap is what kills it**: a 60-second 1080p source is
tens of megabytes, so the video cannot be an upload to a Vercel function and
cannot be returned from one. Vercel's own guidance is that running this kind of
workload as a serverless function is not recommended.

**The architecture that does work:**

```
  Vercel (Next.js)            Worker (Railway / Fly / a $5 VPS)
  - campaign dashboard   ->   - ffmpeg
  - pick clips                - burns captions, reframes
  - job queue (Postgres)      - uploads results
        ^                            |
        |                            v
        +------- Cloudflare R2 / S3 (video storage) -------+
```

Vercel keeps the UI, the auth and the database. The worker holds the actual
Blender-and-hacksaw work. Storage is R2 because egress is free and video egress
is the cost that surprises people. A small VPS at ~$5/month is enough for one
person's clipping volume, and it has no duration limit at all.

---

## The economics, which matter more than the automation

`clipper/campaigns.py` exists because campaign choice dominates everything. Run
it against the fixture:

    $ python -m clipper score --file tests/fixtures/campaigns.json --views 300

```
campaign                        rate       views      $/clip      $/hr  verdict
Bonus Heavy                $ 1.20/1k     300 views  $  7.52/clip  $ 37.62/hr  STRONG
Big Rate Small Reach       $ 5.00/1k     300 views  $  0.83/clip  $  4.12/hr  MARGINAL
    ! high CPM but low views -- rate is bait, reach is the constraint
```

**The $1.20 campaign beats the $5.00 campaign by 9x per hour.** Every clipper
chases the headline rate. The rate is not the number; `views / 1000 * cpm *
approval_rate / (minutes / 60)` is the number.

And the constraint that no software touches: at 300 views per clip, a $5/1k
campaign pays **$1.50**. To clear $8/hour you need roughly 600+ views per clip
on that campaign. **Your bottleneck is reach, not editing speed.** Automating the
editing makes each clip cheaper to produce, which is real, but it multiplies a
number that is currently small. It does not create an audience.

## What I'd build, in order

1. **`clipper score`** against a real JSON of live campaigns and *your actual
   view numbers*. This takes an afternoon and it might tell you not to bother --
   which is the cheapest possible outcome.
2. **`clipper plan` + `clipper render`** on your own machine. No Vercel, no
   server. Prove the output is good enough that you would post it.
3. **Only then** the worker and the dashboard. Deployment is the last step, not
   the first, and it is the least valuable part of this.

## Status of this code

`clipper/campaigns.py`, `clipper/plan.py` and `clipper/render.py` are built and
tested (56 tests). The planning and command generation are verified.

**Not verified here:** actual encoding, because ffmpeg is not installed in this
environment. `render` is dry-run by default and needs `--run` on your machine.
Nothing in this package has been run against live Whop, because the sandbox has
no network access to it.

**Deliberately not built:** posting, and scraping. Both are listed above with the
reason. A module that pretends to post would be a module that gets your account
banned.
