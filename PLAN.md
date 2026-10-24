# PLAN — a working BTC 5-minute system

Status: analysis only. Nothing here is code. Every claim is either measured in
this repo, quoted from Polymarket's own documentation, or taken from published
research — and the provenance is marked.

---

## 0. First: the model we built is wrong, and it matters

`pm5m/settlement.py` currently assumes settlement is:

```
average of the BTC path over the whole 5 minutes  vs  spot at the open
```

**That is not the rule.** Polymarket's documentation
([concepts/resolution](https://docs.polymarket.com/concepts/resolution)) and the
13 Aug 2026 changelog both state it plainly:

```
Up wins  iff  TWAP60(close)  >=  TWAP60(open)
```

Both sides are **Chainlink 60-second trailing averages**, not a 5-minute average
and not a spot print:

| input | what it actually is |
|---|---|
| price to beat (strike) | mean of the Chainlink BTC/USD stream over the 60s **ending at the window open** |
| final price | mean of the same stream over the 60s **ending at the window close** |
| ties | resolve **Up** |

An independent collector verified this against ~252,000 windows across 6 series
and reports `priceToBeat[n+1] == finalPrice[n]` **bit-for-bit** — the close of one
market is, exactly, the strike of the next.

### Why this changes everything

Under my old (wrong) model, the whole 5 minutes contributed to the outcome. Under
the real rule:

* **Minutes 0–4 do not matter at all**, except that they move the strike, which is
  fixed the moment the window opens.
* **Only the final 60 seconds determine the result.**
* The last **2–3 seconds cannot change the outcome** — the settlement TWAP reads
  data ending ~2–3s before the boundary.

So the real object is a **60-second race at the end of a 5-minute window**, and
for 240 of those 300 seconds the outcome is genuinely undetermined by anything
you can compute. My earlier "convergence" story was directionally right and
structurally wrong.

**This is the first thing that must be fixed.** Every number the repo has
produced about fair value is conditioned on the wrong rule.

---

## 1. What the market actually is

| property | value | note |
|---|---|---|
| windows per day | **288** | every 5 minutes, back-to-back, 24/7 |
| slug | `btc-updown-5m-<unix_start>` | deterministic, no lookup needed |
| settlement | `TWAP60(close) ≥ TWAP60(open)` | ties → Up |
| TWAP window | 60s (was 30s, 7–14 Aug 2026) | lookback, not publication rate |
| taker fee | `0.07 × p × (1−p)` per share | **3.15% of stake at 50¢** |
| maker fee | **zero** | plus 20% rebate share |
| 500ms taker delay | **removed 18 Feb 2026** | no free cancel window for makers |
| typical per-market notional | ~$2k | series ≈ $24M/24h |

### The history that shapes the game

A Stanford / Singapore Management University study found that **821 wallets**
made **$8.2M over ~two months** by pushing Binance spot in the final seconds
before settlement; **93% of the losses landed on retail**, and market makers —
who quote passively and end each cycle flat — absorbed almost none of it. In
contested windows, "a bet the market treated as near-certain was overturned
**one time in three**."

Polymarket responded on **7 Aug 2026** by replacing snapshot settlement with
Chainlink TWAP, and put **$1M into liquidity rewards**.

Two consequences:

1. The obvious manipulation is now much harder — you must hold a price through a
   60-second average.
2. **The market is now full of bots built for the old game**, and the fee
   structure (3.15% at 50¢) was explicitly designed to kill latency arbitrage.

---

## 2. "We just gotta figure out if it goes up or down" — no

This is the most important strategic point in the document.

You do **not** need to predict Bitcoin. Over 5 minutes BTC is close to a fair
coin, and no retail-accessible model beats it. Measured result from the one
careful public study of exactly these markets: models trained on Binance features
came out **behind the market** — Brier gap −0.005 to −0.008, t ≈ −4.

What you actually need is one of two different things:

**(a) A pricing disagreement.** The market's quoted probability vs the true
probability of the *specific settlement event*. This requires no forecasting at
all — it requires computing the settlement event correctly, then comparing. This
is what market makers do, and it is the only frame in which a retail system has
ever worked.

**(b) Speed.** Seeing the determining data before the book reprices. The average
arbitrage window on these venues has compressed from 12.3s to **2.7s**, and 73%
of the profit goes to sub-100ms execution. A home connection does not win this.

So the question is not "up or down?" It is:

> **"Is the book's implied probability wrong, given the settlement rule and the
> data I can compute?"**

That is a question a system can answer with statistics instead of hope.

---

## 3. The candidate edges, honestly ranked

| # | edge | mechanism | verdict |
|---|---|---|---|
| 1 | **Strike-anchor error** | market prices spot vs strike; strike is a *lagging 60s average* | **untested, most promising** |
| 2 | Liquidity rewards + maker rebate | get paid to quote; aim for ~0 directional P&L | plausible *business*, not a prediction |
| 3 | Late-window convergence | outcome locks in over the final 60s | tested publicly: **−5.4¢/share** (z −2.2) |
| 4 | Pure market making | collect spread | tested at 50ms replay: **≤ break-even** |
| 5 | Out-forecasting the market | ML on Binance features | tested: **behind market**, t ≈ −4 |
| 6 | Favourite–longshot bias | systematic mispricing of tails | tested: **loses in every bucket** |
| 7 | Manipulation | push Binance spot pre-settlement | structurally closed by TWAP; and illegal-adjacent |

Items 3–6 are not speculation — they are the published results of a research
project that did the work with real data and millisecond replay. That is a very
strong prior against the obvious ideas, and it should be respected rather than
argued with.

**Which leaves #1.** It has not been tested by that project as far as I can tell,
and it has three properties the others lack: it needs no forecast, it is a
mechanical consequence of the rule change, and the rule change is only weeks old
— which is exactly when mispricings exist.

---

## 4. The hypothesis worth building for

### H1 — the strike lags the spot price, and the book may not know

At the moment a window opens:

* the **strike** is `B = TWAP60(open)` — an average of the **previous 60 seconds**
* the **spot** is `S` — the current price

These are different whenever the market is moving. Define the gap `g = S − B`.

The settlement is `TWAP60(close) ≥ B`. Since the mean of a driftless random walk
is a martingale, `E[TWAP60(close)] = S`. So:

```
P(up) = Φ( g / σ_settle )
```

with, for BTC at σ_daily ≈ 3%:

```
σ_settle = σ_√s × √(240 + 60/3)  ≈  1.65e-3   ≈  0.165% of price
```

On a $100,000 BTC that is **≈ $165 of standard deviation**. Which means:

| gap `g` at the open | `x = g/σ` | fair P(up) | edge if the book says 50¢ |
|---|---|---|---|
| $0 | 0.00 | 50.0% | 0 |
| $50 | 0.30 | 61.9% | **+11.9pp** |
| $100 | 0.61 | 72.8% | **+22.8pp** |
| $165 | 1.00 | 84.2% | **+34.2pp** |
| $250 | 1.52 | 93.6% | **+43.6pp** |

If the book opens near 50/50 while `g` is $100+, that is a 20-point edge, and it
requires predicting **nothing** — only computing a strike that has already been
published.

**Why this might be real.** The window opens every 5 minutes with a fresh book.
Participants anchor on the visible spot chart. The strike is a 60-second average
that is *not shown* on that chart. When spot has moved recently, the strike and
the chart disagree — and the loser of that disagreement is whoever traded the
chart.

**Why it might not be.** Professional makers on this venue certainly know the
rule. The 50/50 opening quote may already reflect a correct `Φ(g/σ)`. The honest
answer is: unknown, and cheap to test.

### The measurement that settles it

For every window, from recorded data:

```
x = g / σ_settle                    (computable at the open)
y = market's opening price for UP   (observable at the open)
z = realised outcome                (known 5 minutes later)
```

Then fit `z` against `x` and compare to `y`. Two curves on one chart:

* **calibration curve** — realised win rate vs `x`
* **market curve** — quoted price vs `x`

If the calibration curve sits **above** the market curve in any region of `x`,
that region is your edge, and its size in cents is your expectancy. If the two
curves lie on top of each other, H1 is dead and we stop.

This is one chart. It answers the entire question.

---

## 5. The plan — six phases, each with a kill criterion

The point of a kill criterion is to stop you spending three months on a dead
idea. Every phase below can end the project, and that is a feature.

### Phase 0 — Fix the foundation (1–2 days)

* Rewrite the settlement engine to `TWAP60(close) vs TWAP60(open)`.
* Model the 2–3s dead zone where the outcome is already fixed.
* Re-derive fair value; discard every earlier number as conditional on a wrong
  rule.
* **Kill criterion:** none. This is mandatory.

### Phase 1 — Get real data, no waiting (1 day)

We do **not** need to record for weeks. Multiple public datasets exist, and I
verified one of them by downloading and parsing it — see `DATA.md`.

> `mileswangs/polymarket-historical-data` — **4,032 BTC 5m markets,
> 2026-08-08 → 08-21**, full L2 book, trades, on-chain fills, and the Chainlink
> 60s TWAP that decides each market.
>
> `marketlenstrade/polymarket-historical-data` — 52,289 BTC 5m markets in the
> archive index (Feb–Sep 2026); one full day (288 markets, 111 MB) ships in the
> public repo and I parsed it successfully.

**Critical constraint discovered:** the only regime that matters started
**2026-08-14** (60s TWAP). That is ~71 days = ~20,450 windows of usable history.
The mileswangs set straddles the 30s→60s switch on 14 Aug and must be split.

* **Kill criterion:** if outcomes cannot be reproduced from the dataset's own
  TWAP columns to >99%, trust nothing else in it — find another source.

### Phase 2 — Test H1 (2–3 days) ← **the decisive test**

Using Phase 1 data:

1. Compute `g` and `x = g/σ_settle` at every window open.
2. Extract the book's UP price at open, and at +5s, +15s, +30s.
3. Plot calibration vs market curves (section 4).
4. Compute expectancy of trading the gap, after fees, in cents/share.

* **Kill criterion:** no region where the calibration curve clears the market
  curve by more than the fee (≈0.3¢ at 95¢, ≈1.7¢ at 55¢) after a realistic
  latency haircut. **If this fails, stop.** Everything downstream is wasted
  effort.

### Phase 3 — Test the runner-up (1–2 days)

Only if Phase 2 fails: measure the **liquidity-rewards** business. Model expected
reward income per unit of quoted liquidity against expected adverse-selection
loss. This is a spread-and-subsidy business, not a prediction. Its kill criterion
is simply: does reward income exceed measured adverse selection, net?

### Phase 4 — Paper trading with the survivors (1–2 weeks)

Run `pm5m paper` on the specific strategy that cleared Phase 2/3, with the
settlement engine now correct.

* **Kill criterion:** live paper expectancy must be within 1 standard error of
  the Phase 2 backtest. A large gap means the backtest was optimistic —
  especially on fills.

### Phase 5 — Statistical validation

At 288 windows/day this is the one genuine structural advantage of a 5-minute
market: **statistics accumulate fast.** A real 58% edge needs ~1,700 trades;
at 50 trades/day that is **~5 weeks**, not five years.

* **Kill criterion:** `ci_low_1s ≤ 0` (the one-sided 95% lower bound on
  expectancy fails to clear zero) at 1,500+ trades. Then it is not an edge.

### Phase 6 — Tiny live capital, then scale

Start at the minimum size that produces a measurable fill rate. Scale only on
`ci_low_1s > 0` re-measured live, in steps, with the daily-loss kill switch armed
at all times.

---

## 6. What "mostly profits" honestly means here

You have asked twice for a system that wins most of the time. Here is what that
looks like at each price, and the honest cost:

| entry | win rate | loss = | fee | needs | realistic? |
|---|---|---|---|---|---|
| 50–60¢ | ~60% | 1.5× a win | 2.8–3.5% | 1.7pp edge | needs H1 to be real |
| 80¢ | ~80% | 4× a win | 1.4% | 1.1pp edge | same, thinner |
| 95¢ | ~95% | 19× a win | 0.35% | 0.33pp edge | same, thinnest |

**"Mostly profits" is a statement about price, not skill.** At 95¢ you will win
about 95% of the time whether or not you have an edge — the question that decides
your account is whether your true rate is 95.0% or 95.4%.

That is a measurement problem of roughly ±0.2%, which is exactly why Phase 2 is a
calibration curve and not a hunch.

### Capacity, honestly

Measured directly from the archive (`DATA.md` §1): **median $43 volume per
5-minute market**, depth at the touch of $40–250. That is a far tighter ceiling
than the ~$2k figure I quoted earlier, which came from a secondary source.

Even at a generous 10× and a large share of the available edge, this is a
**tens-of-dollars-per-window** business, not a thousands-per-day one. There is an
unresolved three-orders-of-magnitude discrepancy between this archive and the
reported series volume that must be settled before any capacity claim — mine
included — is worth anything.

A 5-minute market is a **high-frequency, low-capacity** game. It accumulates
statistical evidence quickly and money slowly.

A 5-minute market is a **high-frequency, low-capacity** game. It accumulates
statistical evidence quickly and money slowly.

---

## 7. What would make me say stop

Written down in advance, because rules invented after a drawdown are not rules:

1. Phase 2 shows no calibration gap above fees. → **stop**
2. Phase 2 gap exists but vanishes after a 300ms latency haircut. → **stop**
3. Paper trading diverges from backtest by more than 1 SE. → **stop and debug**
4. `ci_low_1s ≤ 0` at 1,500 trades. → **stop**
5. Two consecutive months below the daily-loss limit. → **stop**
6. I find myself explaining a losing month as "unlucky". → **stop**

---

## 8. The honest prior

Published, careful research on exactly these markets — with real settlement data
and millisecond book replay — concluded there is **no tradeable edge** for anyone
without sub-second, two-legged execution. Every straightforward strategy was
tested and lost.

Our advantage over that conclusion is narrow but real: it predates full
understanding of the 60-second TWAP anchor, and H1 is a specific, falsifiable
mechanism that can be checked in two days with data someone else already
collected.

That is worth two days. It is not worth believing in before then.

**Expected outcome, stated now:** most likely Phase 2 kills it, and we will have
spent a week to learn that for free instead of a year and an account.
