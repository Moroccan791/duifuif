# pm5m — Polymarket 5-minute Up/Down research framework

A measurement instrument for Polymarket's 5-minute crypto Up/Down markets, with
an execution path bolted on and locked behind several deliberate gates.

**Read this part first.** The question I was asked was "can a 5-minute Polymarket
bot make huge profits?" The honest answer is *no, not the way it's usually
imagined*, and this repository is built to demonstrate why rather than to sell
you a bot. What it will do is:

* price these markets correctly, including the fee structure that most bots ignore
* record the data you would need to evaluate any strategy honestly
* tell you, in dollars, when a signal is not an edge
* refuse to trade when the risk gates say stop


---

## 0. The question behind the question

Two asks keep arriving together: *"is trading profitable?"* and *"build me
something that makes a lot of money a month."* They have very different answers.

### Is trading profitable?

Yes — for a very small number of people, and the published base rates are brutal:

| Source | Finding |
|---|---|
| Chague, De-Losso & Giovannetti (Brazil, 19,646 traders) | **97%** of those who persisted 300+ days lost money. 1.1% beat minimum wage. 0.5% beat a bank teller's salary. |
| Barber, Lee, Liu & Odean (Taiwan, every trade 1992–2006) | **<1%** predictably profitable net of fees. 93% gone within five years. |
| ESMA (EU, regulated brokers) | **74–89%** of retail CFD accounts lose money, average loss €1,600–29,000. |
| SEBI (India) | **91.1%** of retail derivatives traders lost money in FY2024. |

One statistic in that literature is worth more than all the rest: the probability
that a trader who had a profitable year followed it with another profitable year
was **6.6%**, while the probability that a losing year was followed by another
losing year was **67%**. Profitable years are mostly luck; unprofitable ones are
mostly skill.

The people making life-changing money exist. You are seeing a survivorship-biased
sample of them, because the other 97% do not post.

### The arithmetic of "a lot per month"

`python -m pm5m goal --capital 1000 --target 5000`:

```
Target: $5,000/month from $1,000 capital.
That requires 500.00% per month, or 217,678,233,500% per year, compounded.
Scale: not achievable by any known strategy; the arithmetic is negative.
```

That is not pessimism, it is division. And it is not fixed by choosing a
different venue — Binance, Polymarket and the NYSE are the same arithmetic.

What the same $5,000/month needs at honest returns:

| return | capital required |
|---|---|
| 8% / yr (very good) | $777,000 |
| 15% / yr (excellent) | $427,000 |
| 25% / yr (exceptional) | $266,000 |

**This is the central point: in trading, income comes from capital, not from the
bot.** A genuinely excellent 20%/yr strategy on $1,000 turns it into about $1,190
in a year. Leveraged 20x to chase $5,000/month instead, the median outcome is
**$4** and the probability of ruin is **78%**:

| | median outcome | P(ruin) |
|---|---|---|
| excellent strategy, 1x | $1,190 | 0.0% |
| exceptional strategy, 1x | $1,320 | 0.0% |
| leveraged 20x to chase target | **$4** | **78%** |

Leverage raises the odds of a good month and raises the odds of ruin faster. The
size needed to hit an aggressive target is the size that makes one bad month
terminal. That is not a strategy, it is a bet on the ordering of your bad months.

### So what do I actually build?

Not a money printer — that does not exist, and building something that claims to
be one would cost you money rather than make it. What is genuinely worth
building, and what this repo does:

1. **Measure whether an edge exists** before risking anything (`record`, `backtest`).
2. **Refuse to trade when it does not** (`band`, `goal`, risk gates).
3. **Price costs correctly**, because fees are what separate a real edge from a
   imagined one (`fees`, `band`).
4. **Tell the truth in advance** about what a given target requires (`goal`).

The honest ladder from here, cheapest first: build capital in something with a
real, boring edge (index funds, a job); paper-trade this thing until your fills
show a measurable edge; only then risk money you can lose entirely.


---

## 0.5 How the 1% actually get there

The 1% are not separated from the 97% by conviction, or by a better indicator.
They are separated by **two habits**, both of which are boring and both of which
this repo implements:

1. **They measure the edge before sizing it.** Not "am I up?" — "is my
   expectancy per trade statistically distinguishable from zero?"
2. **They size on the lower confidence bound**, not the point estimate.

`python -m pm5m expectancy` is that measurement. Here is why it matters, using
a strategy with a *genuinely real* 58% win rate at a 55¢ entry:

| trades | observed | verdict |
|---|---|---|
| 25 | +52% win rate | INCONCLUSIVE — CI spans 30 percentage points |
| 400 | 56.5% win, p=**0.27** | NOT DISTINGUISHABLE FROM LUCK |
| 2,000 | significant | EDGE SUPPORTED |

**A real edge worth +$1.20/trade needs roughly 1,700 trades to prove at 80%
power.** That is the number nobody tells you. It means:

* A week of good results is not evidence. It is a sample.
* A month of good results is still not evidence.
* If you size up on 25 winning trades, you are betting on a coin flip that
  happened to land heads four times in a row.

The tool also computes the things that stop people from fooling themselves:

* **Wilson confidence interval on win rate** — correct at small n, unlike the
  normal approximation that produces "60% win rate!" from 20 trades.
* **Bootstrap CI on expectancy** — trades are fat-tailed and bounded, so
  normality is the wrong assumption.
* **Bonferroni correction for the number of variants you tried.** Test 20
  strategies and one will look profitable at p<0.05 by construction. Pass
  `--variants 20` and the tool will say so.
* **Half-split regime check** — flags when the edge changes between the first
  and second half of the sample, which is what overfitting looks like.
* **Conservative Kelly** on the *lower* bound of the win-rate CI. If that bound
  is below break-even, the size is exactly zero. This is why the tool answers
  "0.00% of bankroll" far more often than people expect — and why it keeps
  accounts alive long enough to find out whether an edge was real.

### The discipline this implies

```
paper trade  →  expectancy says p<0.05 and CI is above zero  →  size up slowly
             →  anything else                                 →  do not size up
```

That is the entire secret. Not a prediction model — a **decision rule about your
own evidence**. It is available to anyone, it costs nothing, and almost nobody
follows it, which is precisely why it works as an edge on its own.


---

## 0.6 "Can we have a genuine 70–80% win rate?"

**Yes. It's trivial. And it tells you nothing.**

`python -m pm5m winrate`. In a binary market the win rate is set by the price you
pay. Buy at 78¢ and you win ~78% of the time — because that's what the market
already believes. **A high win rate is a receipt for the price, not evidence of
skill.**

The proof is that the *same* 78% win rate is profitable or ruinous depending only
on entry price:

| 78% win rate, 1,000 trades | result |
|---|---|
| buy at 60¢ | **+$16,320** |
| buy at 78¢ | **−$1,201** |
| buy at 90¢ | **−$12,630** |

Same accuracy. Opposite outcomes. At 60¢ that 78% is an 18-point edge over the
market. At 90¢ it's an 11-point *deficit*.

| price | market says | you need | loss = | fee |
|---|---|---|---|---|
| 60¢ | 60% | 61.68% | 1.5× a win | 2.80% of stake |
| 78¢ | 78% | 79.20% | 3.5× a win | 1.54% of stake |
| 90¢ | 90% | 90.63% | 9.0× a win | 0.70% of stake |
| 98¢ | 98% | 98.14% | 49× a win | 0.14% of stake |

Higher prices need *less* absolute edge — but the events are near-certain, so
everyone prices them, and each loss costs more. **There is no price where a high
win rate by itself makes money.**

### The streaks are scheduled

At a 78% win rate over 1,000 trades you should *expect* a run of ~5 losses, each
costing 3.5× a win. That 5-loss run gives back 17 wins. It isn't bad luck — it's
the binomial distribution arriving on time. Sizing that assumes streaks won't
happen is what turns a scheduled event into a terminal one.

### So what actually makes money

```
expectancy = your true accuracy − all-in cost per share
```

Nothing else. Win rate is a *property of the price*, and the price is public
information. What is not public is whether you're better than it — and
`pm5m expectancy` measures that, which took ~1,700 trades last time we checked.


---

## 0.7 "Can we buy at 50–60¢ and win 70%?"

`python -m pm5m selectivity`. This is a well-posed question, because at a *fixed*
price a higher win rate **is** a higher edge — at 55¢ every extra point of
accuracy is a cent per share.

The only lever that raises win rate at a fixed price is **taking fewer, better
trades**. So I swept the edge threshold and measured the trade-off:

| mode | counterparty | peak win rate (≥30 trades) | p-value |
|---|---|---|---|
| maker | spot-watching **(hypothesis)** | **60.6%** on 99 trades | 0.096 — not significant |
| maker | informed (honest default) | 46.7% on 15 trades | 0.746 |
| taker | spot-watching | 54.5% on 22 trades | 0.508 |
| taker | informed | 28.6% on 7 trades | 0.884 |

**70% did not appear at any threshold, in any mode, on any sample worth
trusting.**

### The maker paradox (the most useful thing here)

Raising the required edge **lowers** the maker win rate instead of raising it:

| min edge | trades | win rate |
|---|---|---|
| 0.5¢ | 99 | **60.6%** |
| 2.0¢ | 62 | **56.5%** |
| 3.0¢ | 0 | — |

A maker who demands more edge must quote further below fair value, so the order
only fills when the market crashes down to meet it — which is exactly when the
counterparty is most likely to be right. **Demanding a bigger edge selects for
maximum adverse selection.**

Selectivity is only a real lever for *takers*, who choose when to act. For
makers the counterparty chooses, and it chooses the worst moments.

### And the 66.7% you'd screenshot

One cell did show **66.7%**: 18 trades, +$71, p=0.166. That's the number people
post. It is noise, and it will revert to the price you paid as the sample grows.
If you ever see a 70% win rate, **count the trades first**.

---

## 1. The economics, with numbers

### Fees are the whole story

Since 2026 these markets charge takers. Crypto is the most expensive category:

```
fee = shares × 0.07 × p × (1 − p)
```

| price | fee per share | fee as % of stake |
|-------|---------------|-------------------|
| 0.50  | 1.75¢         | **3.50%**         |
| 0.80  | 1.12¢         | 1.40%             |
| 0.95  | 0.33¢         | 0.35%             |
| 0.98  | 0.14¢         | 0.14%             |

A 50/50 coin-flip strategy pays 3.5% of stake per entry. There is no 5-minute
crypto signal that reliably returns 3.5% per trade; if there were, it would not
be available to a retail API. Makers pay **zero** and receive a 20% rebate from
collected taker fees — which is why the people making money here are the ones
quoting, not the ones taking.

The fee structure dictates the only viable taker shape: **buy near-certainty
cheaply, or don't trade.** At 95¢ the fee is 0.35% of stake. At 50¢ it is
unpayable.

### Ties and settlement

Resolution compares the **Chainlink BTC/USD 60-second TWAP stream**, averaged
across the 5-minute window, against its value at the window open. Ties go Up.
This is not a spot-vs-spot comparison, and a bot that assumes it is will be
systematically wrong in the last minute of every window.

### What that buys you

Because settlement is a *path average* rather than a point comparison, the
outcome converges as the window progresses. Four minutes in, one minute of
remaining movement can only drag the average so far. That convergence is the one
structural feature a public-data bot can exploit, and it is what `pm5m/model.py`
computes analytically.

### The catch

Convergence only produces certainty if there is a **cushion** — if the running
average is already far from the open. A path that sits exactly at the open price
is a coin flip at t=299s, no matter how little time is left. This asymmetry is
what separates a real edge from the illusion of one, and it is covered by a test.

### Independent evidence

A two-month study of these exact markets (Oct 2026) concluded they are not
beatable with public data after fees without sub-second, two-legged execution.
`pm5m sim` scenario B — an informed counterparty plus realistic basis risk —
reproduces that result from first principles: **−5.9% ROI**, not profit.


---

## 2. If you specifically want 50–60¢

This is the more interesting request, and it changes the design. At 55¢ a taker
pays 1.73¢ per share — **3.15% of stake** — so break-even moves from 55% to
**56.73%**. You would need to be 1.73 percentage points more accurate than the
market on every trade, forever. That is a real forecasting requirement, and it
is why taker mode should be off in this band.

A maker pays **zero** and rests inside the spread, so break-even drops to the
quote price itself:

```
 price  fee/sh  fee %stake  TAKER need  edge vs mkt  MAKER need   payout
  0.50   1.75c       3.50%      51.75%       1.75pp      50.00%   100.0%
  0.55   1.73c       3.15%      56.73%       1.73pp      55.00%    81.8%
  0.60   1.68c       2.80%      61.68%       1.68pp      60.00%    66.7%
```

`python -m pm5m band` prints the full table. **The conclusion is structural: in
this band you quote, you do not take.** That is what `pm5m/maker.py` does.

### The one mechanism that could make mid-range work

Settlement is a *path average*, not a spot comparison. Most participants appear
to price the market as "will BTC finish above the open?" — which is a different
question, and the difference is largest exactly in the 50–60¢ region, where
nothing is decided yet. If the book is pricing the spot question while
settlement resolves the average question, there is a genuine edge, and it is
biggest at mid prices.

That is a **hypothesis, not a fact**, so the simulator lets you test it against
a counterparty that prices the spot question:

| scenario | win rate | ROI |
|---|---|---|
| A. taker, fair counterparty | 28.6% | **−51.8%** |
| B. maker, fair counterparty | 46.7% | **−16.3%** |
| C. maker, spot-watching counterparty | 63.4% | **+16.8%** |
| D. maker, counterparty 6s stale | 66.7% | **+27.0%** |
| E. taker, spot-watching counterparty | 54.5% | **−19.7%** |

Three things to take from that table:

* **B is the honest default.** Against an informed book, a mid-range maker loses
  16% with zero fees. That loss is pure adverse selection: your resting bid is
  only hit when informed flow disagrees with you. This is what kills naive
  market makers, and it emerges from the fill mechanism rather than being
  assumed.
* **C and D are the only worlds where this works**, and both require the
  counterparty to be systematically wrong.
* **E is the punchline.** Even when the mispricing is real, takers lose money
  reaching it because the 3% fee eats the edge. A vs E is the entire argument
  for maker mode.

So: is a profitable 50–60¢ bot possible? Only if your counterparty is wrong in a
specific, measurable way. `pm5m record` → `pm5m backtest` is how you find out
whether it is, using your own fills and your own adverse-selection number —
which you cannot derive from a model.

### Making it concrete

```python
from dataclasses import replace
from pm5m.config import Settings

settings = replace(
    Settings(),
    strategy_mode="maker",   # not "taker"
    min_price=0.50,
    max_price=0.60,
    maker_edge_cents=1.5,    # rest 1.5c inside fair value
    maker_quote_shares=40.0,
)
```

Or set `PM5M_STRATEGY_MODE=maker PM5M_MIN_PRICE=0.50 PM5M_MAX_PRICE=0.60`.

Failures to expect, in order of likelihood: you get filled only on losers
(adverse selection), your bid never fills at all (you are too far inside), or
you fill fine and lose the fee-sized edge to someone faster. All three are
visible in `pm5m report`.

---

## 3. What is actually buildable

| Component | File | Status |
|---|---|---|
| Exact fee maths (incl. maker rebate) | `pm5m/fees.py` | complete, tested |
| Settlement-index state machine (no lookahead) | `pm5m/settlement.py` | complete, tested |
| Analytic probability model (integral of GBM) | `pm5m/model.py` | complete, MC-validated |
| Kelly sizing on all-in cost | `pm5m/strategy.py` | complete, tested |
| Risk gates (kill switch, loss cap, cooldown) | `pm5m/risk.py` | complete, tested |
| Paper broker (walks the real book, charges fees) | `pm5m/broker.py` | complete, tested |
| SQLite recorder + replay backtester | `pm5m/storage.py`, `backtest.py` | complete |
| Gamma/CLOB/WS clients | `pm5m/feeds.py` | complete |
| Spot feeds (Binance/Coinbase/OKX/Bybit) | `pm5m/spot.py` | complete |
| Mid-range maker strategy (50–60c) | `pm5m/maker.py` | complete, tested |
| Goal arithmetic + ruin Monte Carlo | `pm5m/goals.py` | complete, tested |
| Expectancy / significance testing | `pm5m/expectancy.py` | complete, tested |
| Win-rate vs profit decoupling | `pm5m/winrate.py` | complete, tested |
| Selectivity / win-rate ceiling search | `pm5m/selectivity.py` | complete, tested |
| Gated live execution | `pm5m/live.py` | complete, **disarmed by default** |
| Adversarial simulator (4 counterparty models) | `pm5m/sim.py` | complete |

121 tests. Two of them exist specifically because the simulator once reported a
9,000% return: the maker believed the window had already closed, and the engine
was feeding the Up book into the Down decision.

---

## 4. Quickstart

```bash
python3 -m venv .venv && .venv/bin/pip install -e .
.venv/bin/python -m pm5m demo          # offline: see the economics, no network
.venv/bin/python -m pm5m doctor        # check endpoints, deps, live gates
```

Then, in order — and this order is the whole method:

```bash
# 1. Record. Nothing about this project works without your own data.
.venv/bin/python -m pm5m record --minutes 120

# 2. Check the model's error against reality before trusting any signal.
.venv/bin/python -m pm5m report

# 3. Replay what you recorded. Look at ROI *and* fee drag.
.venv/bin/python -m pm5m backtest

# 4. Only then paper trade against live books.
.venv/bin/python -m pm5m paper --minutes 60
```

Live trading requires every gate at once:

```bash
export PM5M_LIVE=1 PM5M_DRY_RUN=0 POLYMARKET_PRIVATE_KEY=0x...
.venv/bin/pip install '.[live]'
.venv/bin/python -m pm5m live --minutes 30 --i-understand-this-can-lose-money
```

`python -m pm5m live` without the flag prints exactly which gate stopped it and
refuses to start. There is no silent fallback from live to paper.

---

## 5. The model

Let `L` be window length, `K` the integral of observed prices so far, `P₀` the
open, `τ` the time remaining, and `R` the unknown integral over the remaining
block. Then

```
S = (K + R) / L        Up wins ⟺ S ≥ P₀ ⟺ R ≥ P₀·L − K
```

For GBM at spot `P` with per-√second vol `σ`:

```
E[R]   = P·τ
Var[R] = 2·P²·τ²·f(σ²τ),   f(u) = (eᵘ − 1 − u − u²/2)/u²
       → P²σ²τ³/3  as σ → 0     (integral of Brownian motion)

p_up = Φ( (E[R] − (P₀L − K)) / sd(R) )
```

`f` is evaluated by its series expansion. The naive closed form subtracts two
numbers of order `u⁻²` and loses the answer entirely at realistic volatilities —
it returned a variance of exactly zero, hence `p_up = 1.0`, hence an infinite
Kelly stake. There is a test for it and a Monte-Carlo check of the variance to
within 15%.

The model's real weakness is not the maths: it is **basis risk**. Your feed is
not Chainlink's TWAP stream. That error is systematic and no amount of
modelling removes it, which is why certainty is capped, prices are capped, and
the simulator charges you for it by default.

---

## 6. What would have to be true for this to work

Not "it doesn't work" — but here is the honest list of requirements, all of
which must hold simultaneously:

1. **Your counterparty must be slower or worse informed than you.** Scenario C
   (+28% ROI) is the world where the maker quotes halfway to 50/50. That is the
   entire requirement.
2. **Your spot feed must track Chainlink's TWAP closely enough** that the basis
   is smaller than the cushion you are trading. Measure this. Scenario B loses
   5.9% purely from a 2bp basis.
3. **You must win more than spread + fee, per trade, for thousands of trades.**
   With a 1¢ spread and 0.33¢ fee at 95¢, that is ~1.4¢ per trade of genuine
   mispricing.
4. **Sizing must be small enough that one bad window doesn't end the experiment.**
   Kelly at quarter strength, hard caps, and a kill switch exist for this reason.

If requirement 1 fails, nothing else matters. That is the finding, not a
technicality.

### The strategy most people end up with

Buy the near-certain side in the last 30–60 seconds when the book has not caught
up. It is real, it is crowded, and the competition is co-located. Sizing it
large enough to be "huge profit" is exactly what makes a fat-finger event or a
single resolution surprise wipe the account: at 95¢ you risk 95¢ to make 5¢, so
one loss erases nineteen wins.

---

## 7. Architecture

```
spot feed ──┐
            ├──▶ Engine (pure, sync, no I/O) ──▶ RiskManager ──▶ Broker
book feed ──┘        │                            │              │
                     ├── SettlementIndex          │              ├── Paper
                     ├── model.fair_up            │              └── Live (gated)
                     └── strategy.evaluate        └── caps, cooldown, kill switch
                                │
                                └──▶ Store (sqlite) + JSONL raw capture
```

The engine is a pure state machine: feeds push events, it emits decisions. That
is what makes the same code path testable offline, replayable from recordings,
and safe to run live.

---

## 8. Operating rules

* **Never put a private key in a file that is committed.** It belongs in the
  environment. `.env` is gitignored; there is no code that reads one.
* **Start at $5–10 per trade.** The point of the first week is not profit, it is
  discovering whether your fills resemble your model's assumptions.
* **Keep the kill switch accessible.** `touch STOP` halts everything; the bot
  checks on every order.
* **Record before you optimise.** A strategy tuned on data you did not collect
  is a strategy tuned on someone else's market.
* **Watch fee drag as a first-class metric.** `pm5m report` prints fees as a
  percentage of stake. If that number is not small, the strategy is dead
  regardless of win rate.

Prediction markets are regulated differently across jurisdictions and this is
not financial advice. Trading on borrowed money, or with funds you cannot afford
to lose, is the actual risk here — not the model.

---

## 9. Honest limitations

* Spot feeds are proxies for Chainlink's TWAP; the basis is measurable but not
  currently corrected.
* Recordings store top-of-book only, so replays are optimistic for size.
* The simulator assumes GBM. Crypto has fat tails and vol clustering; both make
  tail losses worse than the sim shows.
* No fee-tier accounting (rebates are indicative only) and no gas/withdrawal
  costs.
* Live execution is untested against a funded account by necessity. Treat the
  live path as reviewed-but-unproven.
* **Maker mode is paper-only.** `live_gates` refuses to arm it, because the live
  order client can only send marketable orders -- running a maker strategy
  through it would silently pay the fee the strategy exists to avoid. Real maker
  execution needs place/cancel/replace plus fill confirmation from the user
  websocket. That is the next honest piece of work, not something to fake.
