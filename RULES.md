# What actually works — read from the literature, then tested

I stopped inventing rules and went and found out what the evidence says. Then I
ran every documented strategy on the same 11 years of real Bitcoin with the same
costs, so the comparison means something.

Run it yourself:

```bash
python -m chartbot compare --csv data/btc_daily.csv
```

---

## What the published evidence says

**1. The effect that exists is time-series momentum — but it is smaller and more
fragile than the sales pitch.**

The strongest crypto-specific test (Grobys et al. 2025, 30 large caps,
2016–2023) finds raw TSMOM **statistically indistinguishable from zero** over the
full sample. Marginally positive before July 2020 (+1.74%/week, significant only
at 10%), and **negative and insignificant after July 2020**. Their raw result is
dragged down by heavy tails: *a single one-day 1,400% outlier accounted for 37%
of the entire compounded return.* Trim it and you get 1.51%/week with t = 2.63.

**2. Most of the benefit is volatility management, not prediction.**

This is the finding that changed how I'd build the thing. Kim, Tse & Wald show a
large share of published trend performance comes from the *volatility-scaling
step* — shrinking positions when markets get wild — not the directional signal.
On crypto (Summitward's replication):

| variant | Sharpe | max drawdown |
|---|---|---|
| buy and hold | 0.73 | −84% |
| vol-managed buy and hold *(no trend signal at all)* | 0.74 | −17% |
| raw trend, no vol scaling | 0.71 | −80% |
| full trend + vol scaling | 0.79 | −15% |

Look at rows 2 and 4. **Volatility management alone got almost all the drawdown
reduction.** The trend signal added 0.05 of Sharpe. As one practitioner put it:
trend following is a volatility strategy wearing a directional costume.

**3. Long-only beats long/short on crypto.**

The ACFR study (2013–2023, 2,000+ coins) finds the short leg is exposed to jump
risk and loses money; the profit comes from the long leg. Their best is 28-day
lookback / 5-day holding at Sharpe 1.51 versus 0.84 for the market.

**4. Reported Sharpe ratios above 1.5 from 2020–2025 are suspect.** They ride one
enormous uptrend. Classic trend following's honest long-run Sharpe, net of costs,
is nearer 0.4.

---

## What survived 11 years of real BTC

4,008 daily candles, 2014-10 to 2025-09, $330 → $109,049. 5bps cost per unit of
turnover, annualised on **365** days not 252 (crypto trades every day; using 252
overstates Sharpe by ~17%).

| strategy | CAGR | vol | Sharpe | max DD | time in market |
|---|---|---|---|---|---|
| **SMA 50 long/flat + vol target** | 40.1% | 21.1% | **1.70** | **−28.1%** | 57% |
| **TSMOM 1/3/12m long only + vol target** | 32.0% | 18.4% | **1.60** | **−17.3%** | 65% |
| SMA 50 long/flat | 79.3% | 48.5% | 1.45 | −57.9% | 57% |
| SMA 200 long/flat + vol target | 34.0% | 22.3% | 1.42 | −29.9% | 62% |
| Donchian 55/20 long + vol target | 28.1% | 19.5% | 1.37 | −25.7% | 42% |
| *buy and hold + vol target* | 36.2% | 28.0% | 1.24 | −43.9% | 100% |
| *buy and hold* | 69.6% | 67.6% | 1.12 | **−83.4%** | 100% |
| Donchian 55/20 long/short | 35.9% | 56.5% | 0.82 | −67.2% | 62% |

**Two things are true at once, and both matter:**

- The best strategy's Sharpe of **1.70** beats holding BTC at **1.12**, and cuts
  the worst drawdown from **−83%** to **−28%**.
- **Buy-and-hold still made more money in absolute terms**: 69.6% CAGR versus
  40.1%. Holding a thing that went up 330× wins on raw return. It just takes you
  through an 83% loss to get there.

Saying "the strategy beats buy and hold" would be a lie. It beats it on
risk-adjusted return and on pain. That is a real and sellable thing; it is not
the same thing.

### And my previous strategy

The one I hand-built last turn, with ATR stops and pullback entries: **−17.61%,
Sharpe negative, 174 trades.** It was badly shaped — overtrading a weak signal
with tight stops. The published approach trades 107 times in 11 years, not 174
times in 11 years with 70 of them in the first 18 months. Simplicity won.

---

## Robustness — the part that decides whether to believe any of it

**Both halves of the sample.** Fitted on nothing, tested everywhere:

| period | strategy | CAGR | Sharpe | max DD |
|---|---|---|---|---|
| 2014–2019 | TSMOM long only + vol | 41.5% | **1.86** | −17.3% |
| 2020–2025 | TSMOM long only + vol | 23.9% | **1.34** | −15.5% |
| 2014–2019 | SMA 200 long/flat + vol | 47.1% | **1.78** | −21.4% |
| 2020–2025 | SMA 200 long/flat + vol | 24.3% | **1.15** | −29.9% |
| 2014–2019 | buy and hold | 80.0% | 1.17 | −83.4% |
| 2020–2025 | buy and hold | 60.5% | 1.08 | −76.6% |

It survives the recent regime — the same regime where Grobys found raw TSMOM
went negative. TSMOM long-only + vol still scores 1.34 against buy-and-hold's
1.08.

**Every parameter window.** Sharpe across SMA lookbacks 20–250: **1.36 to 1.70**.
No cliff, no lucky number.

**Costs up to 50bps.** Sharpe 1.73 at zero cost, 1.49 at 50bps. It trades rarely,
so costs barely bite — the opposite of my hand-built version.

---

## What this does NOT prove

Being straight about the limits, because this is where people fool themselves:

1. **One asset, one sample.** BTC is the crypto that survived. A strategy tested
   only on the winner is exactly the "young market artifact" the sceptics warn
   about. The honest test needs ETH, SOL, and cross-sectional work — not done.
2. **Buy-and-hold's 330× is in the sample.** A long-biased filter over the
   greatest bull run in the asset's history will look good. It may look much
   worse over the next decade, and probably will.
3. **Sharpe 1.70 in-sample is not Sharpe 1.70 forward.** A bootstrap interval
   around a Sharpe from 11 years of one asset is wide — the Summitward analysis
   puts an 8.5-year interval at 0.1 to 1.5. Mine would be similar.
4. **No live trading yet.** Every number here is a backtest. Nothing has been
   executed against real books, with real slippage, on a real day.

---

## What this changes in the bot

The current chartbot signals (RSI gates, pullback entries, 1.2×ATR stops) are
the wrong shape and are now known to lose money. The evidence points at:

- **position = long/flat above a moving average** (simple, robust, 107 trades in
  11 years)
- **size scaled by inverse volatility** toward a 25% annual target
- **long-only**, because the short leg pays jump risk and loses
- **no tight stops** — they are what turned a mediocre signal into a −17% year

That is a much smaller, slower machine than what is there now. It is also the
only version with published evidence behind it.

---

## Sources

- Grobys, Junttila et al., *Cryptocurrency momentum has (not) its moments*, Springer, 2025.
- *Time-Series and Cross-Sectional Momentum in the Cryptocurrency Market*, ACFR, 2013–2023.
- Zarattini, Pagani & Barbon, *Catching Crypto Trends*, SSRN 5209907 — Donchian ensemble.
- Kim, Tse & Wald — the volatility-scaling critique of TSMOM.
- Summitward, *Does Trend Following Work on Crypto?* — replication and the decomposition table.
- Marrazzo, *Trend-Following: Mostly a Volatility Story*, 2026.
- Moskowitz, Ooi & Pedersen, *Time Series Momentum*, JFE 2012 — the original recipe.
