# ROADMAP — what we actually do next

Short version, then the steps.

---

## 1. Is it possible? Yes — but not the way you want, at this size.

Three separate answers, because they have different answers:

| question | answer | why |
|---|---|---|
| Can the settlement be predicted better than the book? | **Maybe** | the rule is now computed correctly; the open question is whether the book knows the strike |
| Is the obvious trade profitable? | **Probably not** | a 2026 study already tested late-window taking on this exact market: **−5.4¢/share** after fees |
| Can this make life-changing money? | **No — not on BTC 5m alone** | see the volume number below |

That third row is the one I got wrong by never checking. Here it is.

---

## 1.5 Your plan: $80–100 in, $10–20/day out — measured

This is a testable claim, so I tested it (`python -m pm5m plan`).

**$15/day on $90 is 16.7% per day — 10,095% per month compounded.** That is not
a target, it is a different universe. The best long-run record of any fund ever
is Medallion at ~66% *per year*.

### What a day actually looks like

$90 bankroll, 40 trades/day, taker at 55¢:

| edge | stake/trade | mean day | sd | p5 | median | p95 | **P(losing day)** |
|---|---|---|---|---|---|---|---|
| 1pp | $0.00 | $0.00 | — | — | — | — | 100% (no trades) |
| 2pp | $0.14 | $0.02 | $0.79 | −$1.19 | $0.08 | $1.34 | **46%** |
| 3pp | $0.66 | $0.58 | $3.72 | −$5.62 | $0.37 | $6.36 | **41%** |
| 5pp | $1.70 | $3.98 | $9.53 | −$11.41 | $4.04 | $19.48 | **32%** |

Read the last row. Even assuming a **5pp edge** — far larger than anything the
research supports — you would still lose money on **one day in three**, and 43%
of days would make under $5.

**A "fixed" daily amount is not something a probabilistic edge can produce.** You
don't get $15 every day. You get −$11 some days, +$4 on a typical day, +$19 on a
good one, and you hope the mean is positive. Anyone who tells you otherwise is
either selling something or has not been through a losing week.

### What $15/day actually needs

| real edge | as a taker | as a maker (no fee) |
|---|---|---|
| 1pp | impossible | **$3,712** |
| 2pp | $51,456 | **$928** |
| 3pp | $2,292 | **$412** |
| 5pp | $345 | **$148** |

Two things fall out:

1. **Fees decide everything at small size.** At 55¢ a taker pays 1.73¢/share, so
   any edge below ~1.8pp is *negative after fees*. Quoting as a maker (zero fee)
   is not a preference here, it is the only way small capital survives.
2. **Capital is the binding constraint, not the model.** A realistic 1–2pp maker
   edge needs **$900–$3,700** to make $15/day.

### The honest conclusion about $90

At $90, a genuinely optimistic 3pp taker edge produces **$0.58/day** and a median
bankroll of **$153 after 90 days** — with a 16% chance of clearing $150 profit
and a 41% chance that any given day is a loser.

That is the true shape of it: not a daily wage, but a slow, noisy grind that
works if the edge is real and the fees are zero.

**So the plan should change in one of two ways:**

- **Keep $90 → change the goal.** The goal becomes "prove the edge without
  losing the stake", not $10/day. Treat it as tuition. If you clear $150 of
  profit in 90 days, that is a *good* outcome, not a disappointing one.
- **Keep the $10–20/day goal → change the capital.** You need $400–$900 minimum,
  as a maker, at an edge nobody has yet demonstrated. That is the honest price of
  the target.

What I will not do is tell you the $90 plan works. `pm5m plan` will re-run this
for any numbers you want to try.

---

## 2. The number that decides everything

Every BTC 5-minute market on **2026-06-15**, measured from the archive:

```
288 markets in the day          (288 x 5 min = exactly 24h, so this is complete)
TOTAL volume for the whole day   $ 27,226
median per market                $     42.54
mean                             $     94.53
max                              $    688.38
16% of markets                   under $10
```

Now do the arithmetic that matters. Even if you were **perfect** and could take a
share of all that money:

| your edge | take 5% of volume | take 20% of volume |
|---|---|---|
| 2% | $27/day | $109/day |
| 5% | $68/day | $272/day |
| 10% | $136/day | $545/day |

**You cannot take 20% of a market's volume.** You can take maybe 2–5% before you
move the price against yourself. So realistically this is **$5–$30 a day**, and
that is the *ceiling* with a flawless model.

This is why the last thing I should have done is spend another week on the
probability model. The model was never the bottleneck. **The venue is.**

### The one contradiction to resolve first

Earlier notes quote **~$23.7M/24h** for this series. The archive says
**$27,226/24h**. Those differ by 870×, and I never resolved it. One of them is
wrong by three orders of magnitude, and the entire business case flips on which.

- If **$23.7M/day** is real → 5% of volume at a 5% edge = **~$59,000/day**. Worth
  every hour you can put in.
- If **$27k/day** is real → this is a hobby, and we should say so and pivot.

**Resolving this is step 1 and it takes five minutes.** Nothing else is worth
doing until it is answered.

---

## 3. What we do, in order

### Step 1 — Measure live volume (5 minutes, today)

On your machine, where Gamma is reachable:

```bash
python -c "
import httpx, json
r = httpx.get('https://gamma-api.polymarket.com/events',
    params={'series_slug':'btc-up-or-down-5m','closed':'true','limit':500},
    timeout=30).json()
v = [float(m.get('volumeNum') or 0) for e in r for m in e.get('markets',[])]
print(len(v), 'markets,', sum(v), 'total')
"
```

Whatever comes back is the truth. Also do the same with `closed=false` to see
today's live total.

> **Kill criterion:** if a full day is under ~$100k, BTC-5m alone cannot pay you.
> Go to step 4 (widen the universe) instead of step 2.

### Step 2 — Fix the strike (about an hour of my work)

This is a **real bug I found while writing this plan**, and it is the most
important code change left.

`pm5m/engine.py:95` currently does this:

```python
if open_price is None and self.last_spot is not None:
    open_price = self.last_spot[1]        # <-- invents the strike
```

and again at line 117. The model's single most important input — the strike — is
being **substituted with the current spot price** whenever Gamma hasn't published
`priceToBeat` yet.

That makes `g = spot - strike ≈ 0` by construction, so the model reports ~50%
when the truth might be 70%, and it does so silently. Under the old fake rule
this mattered less. Under the correct rule it destroys the system.

The fix: take the strike only from `eventMetadata.priceToBeat`, and **refuse to
trade** until it arrives (it is stamped a few seconds after the window opens, and
it equals the previous window's `finalPrice` exactly). No substitution, ever.

### Step 3 — Record 48 hours, then test (zero risk, two days of waiting)

No money involved. Run the recorder and let it capture books, spot, and strikes:

```bash
python -m pm5m record --hours 48
```

Then the decisive analysis:

```bash
python -m pm5m phase2 --windows 2000
```

It reports the proxy error against `sigma_settle`, then, per `x = gap/σ` bucket,
the realised win rate next to the market's price. The verdict only fires when the
**95% lower bound clears price + fee** — point estimates are not accepted.

> **Kill criterion:** no bucket where the lower bound clears price+fee → there is
> no edge at the sizes you can trade. Stop, and say so plainly.

### Step 4 — If 5m is too small, widen before giving up

The model is not specific to Bitcoin or to 5 minutes. `TWAP60(close) vs
TWAP60(open)` is the same rule for **15-minute** and **hourly** markets, and the
same structure exists for ETH and SOL. From the same day's archive:

- 15m BTC: 96 markets, more volume per market than the 5m
- hourly BTC: 24 markets, more again
- adding ETH/SOL multiplies the market count

Same code, same model, more liquidity. If BTC-5m fails on capacity rather than on
edge, this is the fix — and it costs nothing but a config change.

### Step 5 — Paper, then tiny live

Only after step 3 clears:

```bash
python -m pm5m paper --minutes 120          # fake money, real books
python -m pm5m live --minutes 30 --i-understand-this-can-lose-money
```

Start at a size where losing everything is annoying, not damaging.

---

## 4. What I need from you

1. **Run the step 1 command** and paste the number. Everything else waits on it.
2. **Decide the pivot now, while it's cheap:** if $27k/day turns out to be the
   real number, do we (a) widen to 15m + hourly + ETH, or (b) stop? Choosing this
   after two more weeks of work would be a waste.
3. **Your honest bankroll number** — the amount you can lose entirely without it
   changing your life. Sizing depends on it and I will not guess.

---

## 5. What I got wrong, so it doesn't repeat

- Built a settlement model on a rule that **does not exist**, and reported its
  simulated profits. Correcting it turned +16.8% into −5.5%.
- Never checked whether the venue was **big enough to matter** until now.
- Let the engine **invent the strike** from spot and call it an open price.

All three are fixed or in this plan. The pattern worth noticing: each error made
the system look *better* than it was. That is the failure mode to watch for, and
it is why every step above has a kill criterion rather than a target.
