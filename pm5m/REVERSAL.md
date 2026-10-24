# The five-minute markets: what actually pays

Question: can you make quick money on the Polymarket 5-minute BTC up/down
markets? Answer: **one real edge exists, it is roughly 4 percentage points, and
whether it is worth money depends on a single number nobody has measured yet.**

This document separates those two things on purpose. The hit rate is measured.
The profit is arithmetic. The price is unmeasured, and it decides everything.

## The rule being tested

`pm5m/settlement.py`, verified against the venue:

```
Up  iff  TWAP60(close) >= TWAP60(open)      ties resolve Up
```

60-second lookback TWAP at both ends. With 1-minute klines the TWAP60 at a
boundary is the close of the minute ending there, so a window starting at
minute `m`:

    strike = close[m-1]        settle = close[m+4]

## The measurement

Binance 1-minute klines, 2022-01 to 2026-10 (BTC) and 2025-01 to 2026-10
(ETH, SOL), converted to the settlement rule above. 504,257 BTC windows.

The signal is: **fade a large five-minute move.** After the price moves more
than 0.40% in five minutes, the next window settles against that move.

| asset | span | windows | fade fires | hit rate | 95% CI | per day |
|---|---|---|---|---|---|---|
| BTC | 2022-01 → 2026-10 | 504,257 | 13,128 | **53.91%** | 53.05 – 54.76 | 7.5 |
| ETH | 2025-01 → 2026-10 | 188,627 | 9,372 | **54.46%** | 53.45 – 55.47 | 14.3 |
| SOL | 2025-01 → 2026-10 | 188,627 | 13,732 | **53.48%** | 52.65 – 54.31 | 21.0 |

Every confidence interval sits above 50% by a wide margin. Three assets, five
years, ~36,000 trades. This is not noise.

## Why it is probably real, not a data artifact

**It scales with the size of the move.** A real mechanism should intensify as
the shock gets bigger, and it does, monotonically:

| absolute 5-minute move | hit rate | 95% CI |
|---|---|---|
| 0.10 – 0.20% | 52.01% | 51.49 – 52.52 |
| 0.20 – 0.40% | 53.38% | 52.57 – 54.20 |
| 0.40 – 1.00% | 53.51% | 51.81 – 55.19 |
| above 0.60% | 54.25% | 52.79 – 55.71 |
| above 0.80% | 55.29% | 53.07 – 57.48 |
| above 1.00% | **57.61%** | 54.40 – 60.75 |

**It is visible in the order flow.** When aggressive takers drove the move
(taker-buy on the same side as the price), the fade hits 54.25%. When the move
happened without aggressive flow, it hits 50.22% — nothing. That is the
signature of forced selling and liquidations overshooting and partially
reverting, not of a stale print.

**It is stable across every calendar year**, including the out-of-sample ones:
2022 55.11%, 2023 55.16%, 2024 51.94%, 2025 54.24%, 2026 52.36%.

**It cannot be bid-ask bounce.** The threshold is 0.40%, roughly forty times
the BTC spread. Microstructure noise does not move a price forty basis points
in five minutes.

Reproduce all of it:

```
python -m pm5m reversal --csv '1m/*.csv'
```

## What it is worth, and the number that decides it

The fee is what kills most 5-minute strategies. Taker fee on crypto is
`0.07 * p * (1 - p)` per share — 1.75c per share at 50c, i.e. **3.5% of the
amount staked**. A coin flip bought at 50c loses 3.5% a trade no matter how
lucky you are. Break-even at a 50% event is 48.25c, not 50c.

Given a 53.91% event, buying it at price `q` is profitable only when

    q  <  52.16c

| price you pay | EV per $1 staked | verdict |
|---|---|---|
| 48c | +8.67% | take |
| 50c | **+4.32%** | take |
| 52c | +0.31% | break-even in practice |
| 54c | −3.39% | lose |
| 56c | −6.82% | lose badly |

So the entire business is: **be filled below ~52c on the reversion side, in the
seconds after a 0.4% move.** If the market charges 54c, this loses money at
almost exactly the fee rate, which is what an efficient market looks like.

### At $100

**Correction.** An earlier version of this document said ~$7-9 a day on $100 at
50c. That was wrong. It assumed full Kelly (4.47% of bankroll per bet) across
43 trades a day. In practice the stake is capped at 2% — Kelly's largest
positions land on the >1% move band measured from only 927 windows, and sizing
off the least-sampled bucket is how this stops being a backtest and starts being
a story. With the cap, $100 at 50c produces roughly **$3.40 a day**, not $8.

Live simulation, `python -m pm5m fade`:

```
python -m pm5m fade --csv '1m/*.csv' --target 15
```

## What $15 a day costs

The capital that makes the *expected* daily profit equal $15, by the price you
actually pay. Three columns because the edge is only known to within a
confidence interval (53.05% – 54.76%), and at the interesting prices that
interval is the difference between a small account and no trade at all.

| fill price | if edge is 53.05% | if 53.91% | if 54.76% | worst day | max drawdown |
|---|---|---|---|---|---|
| 46c | $166 | **$143** | $125 | −$48 | −85% |
| 48c | $278 | **$221** | $183 | −$79 | −83% |
| 50c | $1,091 | **$442** | $317 | −$191 | −80% |
| 51.5c | **never** | $4,198 | $804 | −$1,226 | −58% |
| 52c | **never** | $69,229 | $1,789 | −$16,200 | −49% |
| 54c | never | never | never | — | — |

40.6 bets a day across BTC, ETH and SOL, measured over 2025-01 to 2026-10 —
the period all three assets actually cover. Worst day and drawdown are at the
middle column's capital.

Three things in that table matter more than the numbers:

1. **A one-cent move in the fill price changes the answer by 10×.** 50c → $442.
   51.5c → $4,198. 52c → $69,229. This is not a strategy with a smooth
   sensitivity to execution; it has a cliff, and the cliff is right where the
   market probably is.
2. **"never" is a real outcome.** At 51.5c, if the true edge is at the bottom of
   its confidence interval, the fee eats it and no amount of capital helps.
   Buying at or above the break-even price is not a worse business, it is not a
   business.
3. **The drawdown is brutal.** −80% at 50c, −58% at 51.5c, over 1.8 years. The
   worst *day* at 51.5c is −$1,226 against a $4,198 float — a quarter of the
   account in 24 hours. This is a short-volatility strategy: it sells insurance
   against the next five minutes being volatile, and it loses repeatedly when it
   is wrong.

### Why the estimate is arithmetic, not a backtest

The first version of `fade_sim` measured realised P&L from a single path and
divided. That is worthless at this sample size: 3,519 bets on BTC with a
half-cent edge per bet have a daily standard error *larger than the edge*, so
the same strategy reported +$0.03/day and, on a different slice, a capital
requirement of half a million dollars. The capital figures above come from
expected value — bets per day × stake fraction × edge per stake — and the
simulation is used only for the risk shape, which is what it can actually
measure.

## What is NOT known — the thing to measure next

**The ask price of the reversion side after a 0.4% move.** Nothing in this
document measures it. Klines cannot; it lives in Polymarket's order book.

Everything above is a necessary condition. The sufficient condition is that
retail flow in a 5-minute market chases momentum and thereby prices the
reversion side below 52c. That is plausible — it is the same overreaction the
signal is detecting — but it is an assumption until recorded.

`pm5m record` exists to capture it. It needs connectivity to Polymarket's CLOB,
which the build environment does not have. So the next step happens on a
machine that does:

1. Run `python -m pm5m record` through a few hundred windows.
2. For each window where the previous 5-minute move exceeded 0.40%, log the
   ask on the reversion side 1, 5, 15 and 30 seconds after the window opens.
3. Compare that distribution to the 52.16c break-even.

If the median ask is below 52c, this is a real business at small size. If it
is above 54c, the market already knows, and the honest move is to stop.

## Risks beyond the price

- **Capacity.** Five-minute markets are thin. The earlier estimate of $27,226
  a day of capacity across these markets is an upper bound, not a promise.
- **The edge is not scalable by stake, only by count.** Profit is per-trade
  edge × number of trades. Betting bigger does not help; it only raises
  variance and market impact.
- **No stop losses exist here.** Settlement is 5 minutes away; the only risk
  control is position size.
- **This is a maker's market economically.** Makers pay zero fee and receive a
  share of taker fees; takers pay 3.5% of stake at the coin flip. A taker
  strategy needs the full 4-point edge just to claw back the fee.
