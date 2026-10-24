# chartbot — reads the chart, places the trades

A bot that looks at candles, decides, and sends orders to Binance. There is no
Polymarket dependency; if the 5-minute prediction markets are too thin or too
awkward, this is the other route.

```
python -m chartbot scan       # read the chart once and print what it sees
python -m chartbot backtest   # replay history through the exact same code
python -m chartbot run        # the loop (paper by default)
python -m chartbot serve      # dashboard: the chart the bot is looking at
python -m chartbot webhook    # receive TradingView alerts
```

---

## What "seeing the chart" means here

A trader glancing at a chart does six things at once. Each one is computable, and
all six are computed in `chartbot/reader.py`:

| what you look at | how it is measured |
|---|---|
| trend | EMA(12) vs EMA(26), normalised by price into a −1…+1 strength |
| momentum | Wilder RSI(14), plus whether it is rising or falling |
| structure | fractal swing highs/lows — higher highs *and* higher lows? |
| levels | nearest swing above (resistance) and below (support) |
| volatility | ATR(14) as a percentage of price |
| volume | last 5 bars vs the previous 35 |

`read_chart()` returns those as numbers. `narrate()` turns them into the sentence
a trader would actually say:

> UP trend (strength +1.00), structure higher highs/lows; momentum 94.7 and
> rising; price at 100% of the recent range; BREAKOUT up; resistance +0.00%
> away, support −4.57% away; volume 1.07x normal.

And the same numbers are drawn back onto the candles in the dashboard, so you can
check the machine's opinion against your eyes.

**What this deliberately does not do:** try to recognise "head and shoulders" from
pixels. The numbers above are strictly more informative than the picture, because
a chart image is a lossy compression of them. If you want a vision model in the
loop, `render_svg()` already produces the image — you can send it to a multimodal
API and compare its answer to the numeric read. That comparison is a genuinely
interesting experiment; the numeric read is what actually trades.

---

## The decision

`chartbot/signals.py`. Every rule is in the open — no hidden weights, no fitted
parameters pretending to be insight.

**Trend mode** (default). Buys pullbacks *inside* a trend:

1. a trend exists (|strength| ≥ 0.15)
2. structure agrees (higher highs/lows for longs)
3. RSI has room — 45–68 for longs, **≥ 75 is skipped entirely**
4. price is not already at the extreme of the range (range position < 0.7)
5. volume is not collapsing (≥ 0.8× baseline)
6. nothing is breaking out the wrong way

The count of the conditions that pass **is** the confidence. Three or more and it
trades. Mirrored for shorts.

**Range mode** fades the edges of a quiet range at half size.

Stops sit at 1.2× ATR, targets at 2.2× ATR. The stop is checked **before** the
target on every bar, so a bar that touches both counts as a loss — the pessimistic
reading, because the optimistic one quietly inflates backtests.

---

## Sizing, and why $100 is awkward

Risk-based sizing says: `units = (equity × risk%) / stop_distance`.

With a 0.4% ATR stop and a 2% risk budget that asks for a position worth **417% of
your account**. There is no leverage on spot, so the position is capped by capital
instead, and the real risk per trade comes out far below 2%.

This is not a bug, it is the arithmetic that makes small accounts hard, and the
bot now says so out loud rather than silently shrinking:

```
capped by capital: 0.24% risk not 2.0%
```

On $100 you are not really risking 2% a trade — you are risking whatever 50% of
your equity in BTC allows, which is less.

---

## Safety gates on real money

The live path is refused unless **all** of these hold:

1. `BINANCE_API_KEY` and `BINANCE_API_SECRET` are set in the environment
2. `--live` **and** `--i-understand-this-can-lose-money` are both passed
3. the order's notional is under the cap (50% of equity)
4. the daily loss limit has not been hit (5%), and the daily trade cap (20)

Testnet is the default whenever a key exists, so a typo sends orders to a fake
exchange rather than your account.

```bash
# rehearse on testnet (free, real exchange, fake money)
export BINANCE_API_KEY=...  BINANCE_API_SECRET=...
python -m chartbot run --live --testnet --i-understand-this-can-lose-money

# paper, no keys at all
python -m chartbot run --every 60
```

---

## TradingView

Two ways to involve it, and it is worth being clear about which is which.

**1. Look at the chart yourself.** TradingView's charts are better than anything
worth rebuilding here. Use it to watch; use `python -m chartbot serve` to see what
the bot sees. They should agree, and when they don't, one of you is wrong.

**2. Let a TradingView alert trigger trades.** Available on paid TradingView
plans. Create an alert, tick *Webhook URL*, and point it at:

```bash
python -m chartbot webhook --port 8200 --secret YOUR_SECRET
```

TradingView cannot reach `localhost`, so this needs a public address — a tunnel
(`cloudflared tunnel`, `ngrok`) or a small VPS.

The alert message body:

```json
{"secret": "YOUR_SECRET", "action": "buy", "price": {{close}}}
```

Alerts are logged and ignored unless the webhook is started with `--live`. The
secret is checked on every request; a wrong secret gets a 403 and does nothing.

This makes TradingView the **trigger** and chartbot the **execution engine** — you
get their charting and alerting, and you get stops, sizing and loss limits on the
side that touches money.

---

## Reading a backtest honestly

```bash
python -m chartbot backtest --symbol BTCUSDT --interval 5m --bars 1000
```

On a synthetic random walk the bot loses about the fee rate — that is the correct
result and the one to expect, because there is no edge in the data to find. If a
backtest on noise shows profit, the harness is broken, not the strategy.

Things that make a backtest lie, all of which are handled here: stops checked only
on new signals, targets checked before stops, fees omitted, sizing that ignores
available capital, and a strategy that never trades being described as "safe".

The one thing this cannot fix is **overfitting**. One backtest with good numbers is
a hypothesis, not a result. Run it on several symbols, several periods, and papers
trade before risking anything.

---

## Status

- 36 tests in `tests/test_chartbot.py`; 214 across the repo.
- The live Binance connection is **untested** — this sandbox cannot reach
  `api.binance.com`. It is written to the documented REST spec and will need one
  real run on your machine.
- `scan`, `backtest`, `run --synthetic`, `serve` and `webhook` all work offline.
