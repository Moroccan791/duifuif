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

## 2. What is actually buildable

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
| Gated live execution | `pm5m/live.py` | complete, **disarmed by default** |
| Adversarial simulator | `pm5m/sim.py` | complete |

93 tests. Two of them exist specifically because the simulator once reported a
9,000% return: the maker believed the window had already closed, and the engine
was feeding the Up book into the Down decision.

---

## 3. Quickstart

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

## 4. The model

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

## 5. What would have to be true for this to work

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

## 6. Architecture

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

## 7. Operating rules

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

## 8. Honest limitations

* Spot feeds are proxies for Chainlink's TWAP; the basis is measurable but not
  currently corrected.
* Recordings store top-of-book only, so replays are optimistic for size.
* The simulator assumes GBM. Crypto has fat tails and vol clustering; both make
  tail losses worse than the sim shows.
* No fee-tier accounting (rebates are indicative only) and no gas/withdrawal
  costs.
* Live execution is untested against a funded account by necessity. Treat the
  live path as reviewed-but-unproven.
