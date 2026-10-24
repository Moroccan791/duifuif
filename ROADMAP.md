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
