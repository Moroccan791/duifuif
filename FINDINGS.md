# FINDINGS — measured, not argued

Everything here is a number this repo produced. Where a number contradicts
something said earlier, the earlier claim was wrong and this is the correction.

---

## F1. The settlement rule was wrong, and it was flattering

**What we had.** `pm5m/settlement.py` modelled settlement as the trapezoidal
average of the BTC path over the whole 300s window, compared to spot at the open.

**What the rule is.** `docs.polymarket.com/concepts/resolution`, the 13 Aug 2026
changelog, and an independent collector's ~252,000-window verification all agree:

```
Up  iff  TWAP60(close) >= TWAP60(open)          ties -> Up
```

Both ends are Chainlink **60-second trailing** averages. For 5-minute markets the
stream used a 30s window from 2026-08-07 and a 60s window from 2026-08-14.
`priceToBeat[n+1] == finalPrice[n]` bit-for-bit: one market's close is exactly
the next market's strike.

**Cost of the error.** These are the same 300-window adversarial simulations,
before and after the correction, with nothing else changed:

| scenario | old (wrong rule) | correct rule | swing |
|---|---|---|---|
| C. maker, spot-watching counterparty | **+16.83%** | **−5.50%** | −22.3pp |
| D. maker, 6s-stale counterparty | **+26.98%** | **−55.75%** | −82.7pp |
| A. taker, fair counterparty | −51.83% | −10.85% | (refuses more trades) |
| B. maker, fair counterparty | −16.27% | −19.61% | |

The two scenarios that carried the entire hope of profit were **artifacts**. The
old rule made the market look exploitable because it gave the window's early
seconds weight they never have.

**Why the edge shrank.** Under the real rule both sides of the comparison are
60-second means. Over a 5-minute window a trailing mean tracks spot closely, so
someone pricing "spot vs strike" is nearly right most of the time. There is no
large structural gap to farm. This is a much weaker thesis than the plan assumed,
and it is now the honest one.

---

## F2. The correct model

`pm5m/twap.py`, verified by Monte Carlo.

The settlement is `L = TWAP60(close)`, and the trade is `Up iff L >= strike`.

**Variance.** For a trailing window over `[a, b] = [240, 300]`:

```
Var(L) = 260 · sigma²        at the open
```

collapsing as the window runs. Two different conditionings, and the difference
matters:

| t (s) | path-blind `Var` | path-known `Var` |
|---|---|---|
| 0 | 260.0 | 260.0 |
| 60 | 200.0 | 200.0 |
| 120 | 140.0 | 140.0 |
| 180 | 80.0 | 80.0 |
| 240 | 20.0 | 20.0 |
| 270 | 4.8 | 2.5 |
| 300 | **17.0** | **0.0** |

*Path-blind* means "I know only the current price". It floors at 17, because
knowing the final print does not tell you the average of the 60 seconds that
produced it. *Path-known* means "I have the tick history", which is our actual
situation: the last minute has already printed, so the answer is known exactly at
the bell, and the variance is genuinely zero. **This is why a last-second spike
cannot flip a decided market** — by then the average has been taken.

**Mean.** `E[L | W(t)] = W(t)`. The first implementation used `k = cov/t`, giving
a mean of `0.867·spot` at the close. With a $1 cushion in a decided market that
produced `z = −6` and `P(Up) = 0.0001` — a confident call for **the wrong side**.
A sign error that manufactures certainty is the worst class of bug in this
project, so it is pinned by `test_mean_is_the_spot_at_every_time`.

**Fair value.** On $100k BTC at 3% daily vol, `sigma_settle ≈ 0.165% ≈ $165`:

| gap `g` = spot − strike | z | P(Up) | edge vs 50¢ |
|---|---|---|---|
| $0 | 0.00 | 50.0% | 0.0pp |
| $25 | 0.15 | 56.0% | +6.0pp |
| $50 | 0.30 | 61.9% | +11.9pp |
| $100 | 0.61 | 72.8% | +22.8pp |
| $165 | 1.00 | 84.2% | +34.2pp |
| $250 | 1.52 | 93.6% | +43.6pp |

**This table is only as good as your strike.** That is now the single largest
risk, and it is what Phase 2a measures.

---

## F3. A cushion of $5 on $100k is worth nothing

The maker test fixture assumed a +$5 cushion put fair value at 55c. Under the
correct model it is **51.9c** — a 0.05σ move. Fair value only reaches ~55.7c
around **+$15**, and needs about **+$28** to reach 56c at mid-window.

This is the practical scale that was missing from earlier discussion: on $100k
BTC, the gaps that matter are **tens of dollars**, and the settlement SD is
**$165 at the open, $42 in the final minute**.

---

## F4. The analysis harness refuses to find edges in noise

`python -m pm5m phase2` — the first version reported **"EDGE CANDIDATE"** on 63
windows of pure noise, because it compared the realised win rate's *point
estimate* to the market price. That is the exact overfitting trap the whole
project is meant to avoid.

The fix is the same discipline already used in `pm5m/expectancy.py`: the
**one-sided 95% lower bound** must clear `price + fee`. Verified by control:

| injection | verdict |
|---|---|
| none (control) | NOT PROVEN — best bucket +1.3pp on 80 windows, CI too wide |
| book cheap by 5pp | EDGE CANDIDATE — +5.9pp on 582 windows |
| book cheap by 20pp | EDGE CANDIDATE — +20.9pp on 582 windows |

A tool that finds edges in noise is worse than no tool.

---

## F5. What is still unmeasured, and why

Phase 2a — "is our Binance-derived strike good enough?" — **has not run against
real data.** This sandbox cannot reach `gamma-api.polymarket.com`,
`api.binance.com`, or `data.chain.link`; only `api.github.com` resolves. The
harness is written and tested against fixtures; it needs one run on a machine
with normal internet.

Until it runs, every fair-value number above is conditional on the strike being
accurate to within a small fraction of 16.5bp. `phase2` refuses to report an edge
if the proxy error exceeds 2bp, which is 12% of the signal.

**Run this first, on your machine:**

```bash
python -m pm5m phase2 --windows 2000
```

It prints the proxy error, the calibration table, the edge per bucket, and a
verdict. If the verdict says the proxy is unusable, the fix is Chainlink Data
Streams history, not a better regression.
