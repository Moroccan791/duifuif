# How this works, in plain language

## There are two separate things in this repo

I built two different bots and never clearly separated them. That is my fault.
They share a folder and nothing else.

| | `pm5m/` | `chartbot/` |
|---|---|---|
| what it trades | Polymarket "BTC up or down in 5 min" bets | BTC itself, on Binance |
| what decides | a probability model vs the quoted price | the chart (trend, momentum, structure) |
| makes money by | being right more often than the odds imply | letting winners run and cutting losers |
| status | researched, no edge found, **parked** | the one we are working on |

**Everything from here on is `chartbot`.** If `pm5m` is confusing you, ignore it.

### Which market, exactly

**Binance, BTC/USDT spot.** Real bitcoin. You buy BTC with dollars and sell it
later at a different price. Profit = the price difference, minus fees.

That is genuinely different from Polymarket, where you buy a *bet* ("will BTC be
up in 5 minutes?"), it pays $1 if you are right and $0 if you are wrong, and you
never own any bitcoin. Polymarket is parked; ignore it.

The dashboard on port 8100 shows **real BTC daily candles**, 2024-10 to
2025-09, loaded from `data/btc_daily.csv`. Earlier it showed generated data while
labelled BTCUSDT — that was wrong and it is now either real or loudly marked
`SIMULATED`.

---

## The strategy, in one paragraph

We follow trends, but we never buy at the top.

When price is trending up, we wait for it to pull back a little, and then we buy.
We put a stop just below, and a target about twice as far away. If we are right
55% of the time, the winners are roughly twice the size of the losers, and the
account grows. That is the entire idea. It is not clever, and it does not need to
be — it needs to be right slightly more than half the time and to not blow up.

Before any of that, six things must line up:

1. **a trend exists** — the 12-bar and 26-bar averages are separated
2. **the structure agrees** — higher highs *and* higher lows
3. **momentum has room** — RSI between 45 and 68. Above 75 we skip it entirely
4. **it is a pullback** — price is not already at the top of the range
5. **volume is not dying** — at least 0.8× normal
6. **nothing is breaking down** — no downward breakout against us

The number of those that pass is the confidence. Three or more, and it trades.

---

## How backtesting works

A backtest is not a separate program. It is the **same function the live bot
calls**, run in a loop over history.

```
live:      new candle arrives  ->  step()  ->  order goes to Binance
backtest:  candle from 2019    ->  step()  ->  order goes to a fake broker
```

That is the whole trick, and it matters: because it is the same `step()`, the
backtest cannot drift away from what the bot will actually do. A separate
backtest program is how people end up with a strategy that made 40% in
simulation and lost 20% live.

At every candle the bot only sees candles **up to that point**. It cannot see the
future, because the code is given `candles[:i+1]` and nothing else. There is no
way to peek even by accident.

Then we count: how many trades, how many hit the target, how many hit the stop,
what the account did, and how deep the worst dip was.

---

## What a backtest actually looks like

```
python -m chartbot backtest --symbol BTCUSDT --interval 5m --bars 900 --trace 3
```

```
  start            $100.00
  final            $99.80
  return           -0.20%
  trades           52  (26W / 26L)
  win rate         50.0%
  max drawdown     4.88%
  decision points  870

  #1  BUY 0.000487 BTC @ 102,591.32
      why      up-trend +1.00; rsi 52 has room; pullback (pos 0.69); volume 1.07x
      stop     102,059.46      target 103,566.40
      exited   103,545.69 via TARGET after 40 min
      pnl      $+0.47

  #2  BUY 0.000487 BTC @ 102,936.78
      why      up-trend +0.57; rsi 50 has room; pullback (pos 0.67); volume 0.83x
      stop     102,288.86      target 104,124.64
      exited   102,268.40 via STOP after 5 min
      pnl      $-0.33
```

Read `#1` as a sentence: *the trend was up, RSI was 52 so it had room to rise,
price had pulled back to 69% of its recent range, and volume was normal — so we
bought. Price hit the target 40 minutes later and we made 47 cents.*

Read `#2`: *same setup, but price fell to the stop in 5 minutes and we lost 33
cents.* One win $0.47, one loss $0.33. That is the shape of the whole business:
you lose often and small, win slightly less often and bigger.

Those numbers are from **random synthetic data with no real edge in it**, which
is why the result is a 0.2% loss — you just pay the fees. That is the correct
outcome. A backtest that makes money on random data is broken.

---

## What the backtest cannot tell you

It tells you what the rules did on that data. It does not tell you what happens
next month. Market behaviour changes, and a strategy tuned until it looks good on
the past is fitted to the past.

So the order is: backtest on many symbols and timeframes, then **paper trade**
(fake money, live prices, real fills), then real money at a size where losing it
is annoying rather than damaging.

---

## What to do next

1. See it think: open the dashboard (port 8100). It refreshes every few seconds.
2. Read one chart: `python -m chartbot scan --symbol BTCUSDT`.
3. Run the backtest on real data: `python -m chartbot backtest --symbol BTCUSDT --bars 1000 --trace 5`
4. Paper trade: `python -m chartbot run --every 60`

Step 3 needs internet to Binance, which this sandbox does not have, so run it on
your machine.
