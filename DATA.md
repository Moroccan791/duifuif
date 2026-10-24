# DATA — where the history lives, and what a backtest can actually prove

Short version: **the data exists, it's free, and I downloaded some of it to
verify.** But the version of the game you want to backtest only started
**71 days ago**, and that fact is what breaks your assumption that a good
backtest will work in future.

---

## 1. What I verified, live, from this sandbox

Downloaded `marketlenstrade/polymarket-historical-data` via the GitHub API and
parsed it. These are measured numbers, not estimates:

| check | result |
|---|---|
| BTC 5m markets in the 2026-06-15 archive | **288** (one full day) |
| outcome split | 148 Up / 140 Down → **51.4% up rate** |
| median volume per market | **$43** |
| mean volume | $95 |
| max volume | $688 |
| markets under $10 volume | 47 of 288 |
| resolution latency | median **93s**, p90 128s |
| order book rows (one market) | **41,183** events, ms timestamps |
| event types | `snapshot` (full L2), `delta`, `trade` |
| book at the open (two sampled markets) | **0.515** and **0.475** implied P(up) |
| depth at the touch | **83–528 shares** ($40–$250) |

Two things stand out and both matter:

**The book opens at essentially 50/50.** 0.515 and 0.475. This is exactly the
premise H1 in `PLAN.md` depends on — the book does not appear to price anything
about the window at the open.

**Volume is tiny.** A median of **$43 per 5-minute market**. That is not a typo.
It means the capacity of any retail strategy here is measured in tens of dollars
per window, not thousands.

> ⚠️ **Unresolved discrepancy.** The Gamma API literature reports the
> `btc-up-or-down-5m` series at ~$23.7M per 24h, which would be ~$82,000 per
> market — **3 orders of magnitude** above the $43 median measured here. Either
> the archive undercounts captured trades, or the reported series figure includes
> something else (both sides, other assets, cumulative). This must be resolved
> before believing any capacity estimate, mine or anyone else's.

---

## 2. The sources, ranked by usefulness

### A. Full L2 history, post-TWAP — the one that can test H1

**`mileswangs/polymarket-historical-data`** (GitHub) → data on HuggingFace/Kaggle

* **2026-08-08 → 2026-08-21**, all **4,032** BTC Up/Down 5m markets
* 457M order book events, 6.7M trades, 21.3M on-chain fills
* **Chainlink Data Streams price + the 60s TWAP that decides each market**
* 5.6 GB Parquet
* Collected colocated in AWS London, 4.6 ms median latency, two redundant feeds

This is the gold standard dataset: it contains the settlement input itself.

⚠️ **It straddles a rule change.** 5m markets used a **30-second** TWAP from
7 Aug and switched to **60-second** on 14 Aug. So 8–13 Aug is a *different
settlement rule* from 14–21 Aug. Analysing it as one sample is an error.

### B. Larger but pre-TWAP archive

**`marketlenstrade/polymarket-historical-data`** (GitHub, and I confirmed it works)

* The full archive index lists **`btc-up-or-down-5m`: 52,289 markets,
  2026-02-28 → 2026-09-12**
* The public repo ships one sample day (2026-06-15, 288 5m markets, 111 MB) with
  markets + full L2 history; the rest is on marketlens.trade
* Columns: `winning_outcome`, `volume_usd`, `open_time`, `close_time`,
  `resolved_at`, per-market `history-*.parquet` with `snapshot`/`delta`/`trade`

Useful for: order book microstructure, fill modelling, latency behaviour,
volume distribution. **Not useful for testing H1**, because before 7 Aug the
strike *was* a spot snapshot (see §3).

### C. Outcomes only, no book — cheap and complete

The **Gamma API** with `closed=true` and `offset` pagination returns every
resolved market with its winning outcome. Slugs are deterministic
(`btc-updown-5m-<unix>`), so you can enumerate windows directly.

This is all you need for the **first half of the H1 test** — validate that
`Φ(g/σ)` predicts the outcome — because that question needs outcomes and a
spot series, not order books.

### D. Live reference price

**PolyBolt WebSocket** `price.crypto.twap` streams the actual Chainlink 60s TWAP
that settles these markets. Requires CLOB credentials. This is the correct
real-time input; **do not use Binance spot as the strike.**

### What I could not reach from this sandbox

`huggingface.co`, `gamma-api.polymarket.com`, `data.chain.link` and
`api.binance.com` are all blocked here. Everything in §1 came through
`api.github.com`. On your machine, the download is:

```bash
git clone --depth 1 https://github.com/marketlenstrade/polymarket-historical-data
pip install huggingface_hub
huggingface-cli download mileswangs/polymarket-historical-data --repo-type dataset
huggingface-cli download mileswangs/polymarket-btc-updown-5m-orderbook-tick-data --repo-type dataset
```

---

## 3. Why "backtest works → future works" is false here

This is the part worth reading twice, because it is the assumption that costs
people their accounts.

### The rules changed six times in one year

| period | settlement rule | what a backtest on it proves |
|---|---|---|
| before 2026-08-07 | single spot snapshot at close | **nothing about today** |
| 2026-08-07 → 08-13 | Chainlink **30s** TWAP both ends | **nothing about today** |
| **2026-08-14 → now** | Chainlink **60s** TWAP both ends | the only regime that matters |

Fees moved too: taker fees introduced Jan 2026, formula changed (V1 → V2),
expanded to all crypto 6 Mar, rebate tiers 19 Oct. A backtest crossing any of
those lines is measuring a different market.

### The deeper point — and it is specific to H1

**Before 7 Aug, the strike was a spot print at the open.** So the gap
`g = spot − strike` was approximately **zero by construction**. Which means:

> The 60-second-lag opportunity H1 describes **did not exist before 7 Aug, and
> only existed in its current form from 14 Aug.**

Any backtest on pre-August data will show `g ≈ 0` and therefore no edge — not
because the idea is wrong, but because you tested it against rules under which
it could not appear. Conversely, a backtest on the Aug 8–13 slice tests a 30s
window, which is a different (weaker) version.

**The clean dataset is 14 Aug → today: 71 days × 288 = ~20,450 windows.**
That is the entire history of the game as it is currently played.

### And backtest overfitting is not hypothetical

A backtest is a hypothesis test where you also got to choose the hypothesis.
With 20,000 windows and a free choice of threshold, bucket, side, size, timing
and filter, finding a profitable configuration is *guaranteed* — the question is
whether it survives data you did not look at. The repo already has the tooling
for this: `pm5m expectancy` with Bonferroni correction, and the half-split regime
check.

---

## 4. So is 20,450 windows enough? Yes — for the decisive test

This is the good news, and it's why a 5-minute market is worth the trouble:
statistics accumulate about 1,000× faster than in a daily-timeframe strategy.

Required sample to detect a calibration gap (one-sided α=0.05, power 0.8):

| gap to detect | windows needed **in that bucket** |
|---|---|
| 10pp | ~155 |
| 5pp | ~620 |
| 2pp | ~3,900 |

If the interesting bucket (say `|x| = |g|/σ > 1`) is ~20% of windows, then over
71 days you have ~4,090 windows in that bucket — **enough to resolve a 5pp gap
with room to spare**, and comfortably enough for 10pp.

So the decisive test is not underpowered. It can be run properly, once.

---

## 5. The test plan, using this data

**Test 2a — validate the predictor (outcomes only, cheap)**
1. Get Binance 1-second BTC prints for the window (free, `data.binance.vision`).
2. Approximate the strike as the mean of the 60s before each window open.
3. Compute `x = (spot_at_open − strike) / σ_settle` with `σ_settle ≈ 0.165%`.
4. Bucket windows by `x`; plot realised up-rate against predicted `Φ(x)`.
5. **Pass:** realised tracks `Φ(x)`. **Fail:** the strike proxy is wrong, or
   Chainlink diverges from Binance too much for this to work.

**Test 2b — is the book wrong? (needs the order book data)**
6. Join each window's book price at open, +5s, +15s, +30s.
7. Plot market-implied probability against `x`.
8. The gap between the calibration curve (2a) and the market curve (2b), minus
   the fee, **is** the edge in cents per share.
9. **Pass:** a region where the gap survives a 300 ms latency haircut.
   **Fail:** stop the project.

**Test 2c — out of sample, for real**
10. Fit on 14 Aug – 15 Sep. Do not look at 16 Sep – 24 Oct until the strategy
    is frozen.
11. Evaluate once. That second period is the closest thing to "the future"
    available.

---

## 6. What a backtest can and cannot prove — plainly

**Can:** that a hypothesis is consistent with a specific past regime, under
specific fill and latency assumptions, after fees.

**Cannot:** that it will work in future. A backtest is evidence, not proof, and
the strength of the evidence depends entirely on how many times you looked.

Three habits make it real rather than theatre, and they are already in the repo:

1. **Freeze the strategy before touching the holdout period.** One look.
2. **Count your variants** and correct for them (`--variants N`).
3. **Require the lower confidence bound to clear zero** (`ci_low_1s > 0`), not
   the point estimate.

If Phase 2 passes, that will mean: *the edge existed for 71 days under the
current rules, out of sample, after fees, with a conservative fill model.* That
is a genuinely strong statement — stronger than most retail systems can make —
and it will still not be a promise about tomorrow.

---

## 7. "Why do we need Binance if we trade on Polymarket?"

Because **a Polymarket 5-minute market has no price of its own.**

The order book is a bet *about* BTC. It tells you what other traders believe;
it contains zero information about where BTC actually is. If the book says 50/50
and BTC has moved $100 in the last minute, the book is either right (the crowd
knows something) or wrong (the crowd is anchored on a chart that lags the
strike). **You cannot tell which without your own measurement of BTC.** That
measurement comes from a spot feed.

The answer key lives outside Polymarket. Settlement is decided by Chainlink's
BTC/USD TWAP — an external oracle. Polymarket only publishes the verdict.

### But the answer splits three ways

| use | what to use | why |
|---|---|---|
| **backtest** — the strike | **Gamma `eventMetadata.priceToBeat`** | Polymarket publishes the *actual* strike per market. No approximation needed. |
| **backtest** — the spot path | **Binance 1s klines** (`data.binance.vision`, free) | we need the 60s of price history *before* each open to compute `g`; Gamma can't give a spot path |
| **live** — the strike | **PolyBolt `price.crypto.twap`** = the real Chainlink 60s TWAP | must be the actual settlement input, not a proxy |

So the answer is: **we need the underlying price, yes — but not necessarily
Binance, and definitely not Binance for live trading.**

### The detail that makes this operationally important

Polymarket **does not tell you the strike while you can still trade on it**:

* `eventMetadata.priceToBeat` is published only **30–300 s after the market
  closes**
* `finalPrice` appears about **6–7 minutes** after

Which means at the moment a window opens, the strike is **not readable from any
Polymarket API**. To know it live you must subscribe to the Chainlink TWAP feed
yourself. That is not a convenience — it is the entire operational requirement of
H1, and it disqualifies anyone trading off the Polymarket UI.

### The free gift: Gamma lets us *measure* our proxy error

Two facts combine nicely:

1. `priceToBeat[n] == finalPrice[n-1]`, bit-for-bit — it is the same TWAP at the
   boundary between consecutive markets.
2. Gamma publishes both, historically, for every market.

So for the backtest we can reconstruct the **true strike series** from Gamma, and
then check our Binance-derived 60s-average proxy against it. That converts basis
risk from an assumption into a **measured number** — which is exactly what
`PLAN.md` Phase 2a needs, and it costs nothing.

**Revised 2a data plan:**

```
strike_true  <- Gamma eventMetadata.priceToBeat      (exact, published)
spot_path    <- Binance 1s klines around each open   (free, complete)
strike_proxy <- mean(spot_path[-60s:])               (our approximation)
```

Then measure `|strike_proxy − strike_true|` in basis points. If that error is
small relative to `σ_settle ≈ 0.165%`, the proxy is safe for research. If it is
not, H1 cannot be tested with Binance data and we need Chainlink history.

That single calibration curve is worth more than any backtest that skips it.

---
