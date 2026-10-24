# The simple version

One decision a day. That is the whole strategy.

```bash
python -m chartbot today --csv data/btc_daily.csv --equity 100
```

It prints one of three lines:

```
BUY 45% of equity  ($45.00)
HOLD - no change
SELL EVERYTHING - stay in cash
```

Do that, once a day, after the daily candle closes. Nothing in between. That is
the entire operating manual.

## Why this and not something cleverer

It is the strategy that survived being tested against everything else on real
data — nine strategies, three assets, eleven years. It beat buy-and-hold on
Sharpe and drawdown on BTC, ETH and SOL. It needs no stop losses, no chart
reading, no screen time, and about ten trades a year.

The old RSI bot, with stops and pullback entries, lost 17.6% over eleven years.
Tight stops on a slow signal is what killed it.

## What it does, mechanically

```
position = 1 if close > 50-day average, else 0
size     = 25% / recent volatility, capped at 100% of your money
```

Two rules. In or out. When volatility is high it holds less; when it is low it
holds more. That is all.

## What it has actually done

| | strategy | buy & hold |
|---|---|---|
| BTC 2014-2025 | 41%/yr, **−33%** worst | 70%/yr, −83% worst |
| ETH 2017-2026 | 25%/yr, **−21%** worst | 25%/yr, −94% worst |
| SOL 2020-2026 | 31%/yr, **−27%** worst | 75%/yr, −96% worst |

It makes less money than holding and loses far less when things break. If you
just want the most money, hold. If you want to stay in the game through a −80%
year, this is the trade.

## The $10-15 a day question

At 41%/yr — the best of those three, in the friendliest sample available —
$10 a day takes **$8,859**. At a defensible 15%, **$24,333**.

With $100 it makes **$0.11 a day**. That is not a flaw in the strategy; it is
what a percentage means at small size. Check your own number:

```bash
python -m chartbot income --target 10 --capital 100
```

Nothing in trading turns $100 into $10 a day. Anything that claims to is
leveraging you to the point where a normal week deletes the account.

## Three rules, and they are not optional

1. **Paper first.** `python -m chartbot run --csv data/btc_daily.csv` replays
   eleven years in a few seconds. Then let it run for a month before any real
   money touches it.
2. **Expect a 30% drawdown.** It happened on all three assets. It will happen
   again, and it is the reason this works: you are being paid for surviving the
   parts other people cannot.
3. **Never add a stop loss.** The exit is the average crossing, at daily
   granularity. A stop that fires inside a day fights the signal it is supposed
   to protect — that is measured, not an opinion.

## The honest summary

Simple: yes. Profitable on the evidence: yes, on three assets, over a decade.
$10 a day from $100: no, and no strategy makes that true.

The strategy is solved. The capital is the missing piece.
