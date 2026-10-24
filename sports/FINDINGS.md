# Can a sports bot beat Polymarket?

Short answer: **not by predicting match winners.** A model that predicts from
results alone loses to the closing betting line, and betting its disagreements
with that line lost 5-11% of stake at every threshold I tested.

The profitable version of this idea is not a predictor. It is a **price
comparison** — and that is what `sports/edge.py` does.

## What was tested

21,659 matches from football-data.co.uk, five leagues (Bundesliga, Premier
League, Ligue 1, Serie A, La Liga), eleven seasons, 2013-08-09 to 2025-05-25.
Closing odds from **Pinnacle** where available, Bet365 otherwise. Matches are
split chronologically: 12,460 for training (to 2020-06-30), 9,199 for testing.

The model is deliberately ordinary — a standard Elo with a home-field term and a
goal-margin multiplier, converted to win/draw/loss by empirical calibration.
It never sees the odds, so "did it beat the market" is a real question.

```
python -m sports backtest --dir <directory of season CSVs>
```

## Result 1: the market wins

| | accuracy | log loss |
|---|---|---|
| market favourite | **53.73%** | **0.9706** |
| Elo model | 52.58% | 1.0010 |

Log loss is the honest measure: it scores the probability itself, not just
whether the top pick happened to land. The market is better on both, and its
advantage is not small — 0.03 nats per match is a lot in a market with a 2.96%
average overround.

## Result 2: nobody gets coin flips right

The 1,541 test matches the market priced between 45% and 55% for the home side:

| | called correctly |
|---|---|
| market | 49.32% |
| model | 48.80% |

This is the direct answer to "gets it right even at 50-50 outcomes". A 50c
market is not an under-predicted event; it is a market that has correctly
concluded the outcome is a coin flip. Everything within a few points of 50% is,
by construction, unpredictable — including by the people pricing it.

## Result 3: betting the disagreement loses

Backing every side where the model's probability exceeded the de-vigged market
probability, at the closing odds:

| edge required | bets | accuracy | ROI |
|---|---|---|---|
| 0% | 14,094 | 28.17% | **−7.1%** |
| 2% | 10,326 | 28.02% | **−6.7%** |
| 4% | 7,035 | 28.33% | **−7.1%** |
| 6% | 4,765 | 29.91% | **−5.1%** |
| 10% | 2,031 | 32.64% | **−11.5%** |

Note the pattern: requiring a *bigger* disagreement does not improve the return.
It barely moves accuracy and still loses roughly the overround plus the model's
own error. When a model's confident disagreements are as wrong as its modest
ones, the model is not adding information.

## Result 4: the one structural bias that showed up

Backing every outcome blind, by the price band it was offered at (test set):

| price band | bets | hit rate | ROI |
|---|---|---|---|
| 1.00–1.50 (heavy favourite) | 1,780 | 75.28% | **−0.1%** |
| 1.50–2.00 | 3,192 | 57.27% | −1.1% |
| 2.00–3.00 | 5,460 | 39.91% | −3.1% |
| 3.00–5.00 | 12,409 | 26.05% | −3.7% |
| 5.00–10.00 | 3,995 | 14.09% | **−11.3%** |
| 10.00+ | 761 | 7.49% | +0.7% |

That is the classic **favourite-longshot bias**: longshots are systematically
overpriced and heavy favourites are priced almost exactly right. The edge, if
there is one, is on the favourite side or against longshots — not in predicting
the outcome.

Caveat: this is measured against Pinnacle, the sharpest book in the market. A
softer book, or a venue with retail flow, will show more of it. Whether
Polymarket shows it is a separate measurement, and it is the one that matters.

## Result 5: adding data narrows the gap without closing it

The obvious objection is that the model had too little information in it. So:
four features instead of one — Elo, points from the last five matches, goal
difference over the last five, and days of rest.

| | accuracy | log loss |
|---|---|---|
| plain Elo | 52.58% | 1.0010 |
| Elo + form + goal diff + rest | 52.64% | 0.9910 |
| the closing line | **53.73%** | **0.9706** |

It helps — 0.010 nats — and it is still 0.020 nats behind the market. That
distance is what the bookmakers' own player-level information is worth, and they
had already priced it before the ball was kicked.

This is the wrong frame for the problem. A closing line is not a prediction made
from results; it is the settlement price after every well-informed bettor has
pushed money into it. Player data does not beat that price, because the price
already contains the player data. Beating it requires information the market
does not have yet — not more of the information it already has.

## Where this test does NOT generalise

The result is strong and its scope is narrow, and it is worth being precise
about which is which. What was tested: **three-way football moneylines, against
Pinnacle, at the close.** That is the hardest version of the problem — the
sharpest book, the most efficient market, the most-covered sport.

It says nothing about:

* **Binary markets.** Polymarket lists yes/no, not 1X2. Fewer outcomes is a
  structurally easier problem, and the crowd pricing a two-sided market is not
  the same crowd that prices a three-way one.
* **Fixtures the sharp books barely cover.** Lower leagues and niche sports are
  priced by retail flow rather than by professionals, and the favourite-longshot
  table above suggests where that goes wrong.
* **In-play.** A venue lagging the broadcast is a speed edge. Nothing in this
  document tests latency, because nothing here runs in real time.

Those three are where a sports bot could plausibly pay, and none of them are
excluded by anything measured here.

## So what would actually work

Not a predictor. Three things, in descending order of how much I believe them:

1. **Price comparison against a sharp line.** If Polymarket offers a side below
   the de-vigged Pinnacle close, that gap is the edge. It is not a prediction —
   it is noticing that two venues disagree. `sports/edge.py` computes exactly
   this, including the fee-aware break-even price.
2. **Illiquid markets.** The bias above is strongest in longshot prices, which
   is also where books hold the widest margins and where a thin market is
   easiest to move. A small account can exploit that; a large one cannot.
3. **Latency on in-play.** A real edge exists when a venue lags the broadcast.
   It is a speed competition, not a modelling one, and it is not something I can
   build or test from here.

The test that decides it: record Polymarket prices and sharp closing lines for
the same fixtures, then check whether the Polymarket price sits below the
break-even price often enough to cover the times it does not. That is a
measurement, not a model.

## Three bugs this work caught, all of which flattered the result

Worth recording because each produced a plausible number rather than an error:

1. **String-sorted dates.** `DD/MM/YY` sorts `01/02/14` after `31/12/2022`.
   Elo was being fed a scrambled history. Any backtest over these files must
   parse dates to tuples first, and `parse_date` exists for that reason.
2. **Fitting the calibration before walking the ratings.** Every match landed in
   one bin, the model collapsed to "always home", and it scored 42.74% — which
   reads as a weak model rather than as a broken one.
3. **Re-predicting after the walk.** Computing the betting table with
   `model.predict` after the ratings had absorbed the test results turned a
   −7.1% return into +2.1%. Roughly nine points of pure look-ahead, and it
   looked like the strategy working.

---

# The 50-65c band, tested

You asked for a system that buys only legs priced 50-65c. That is a specific,
checkable claim, so I checked it instead of assuming it. This section is the
answer, and it is **not the answer you were hoping for**.

## First, the arithmetic you cannot argue with

Buy a share at price `p`. It pays 1 or 0. Polymarket charges a taker fee on
notional of roughly `0.07 * p * (1 - p)` per share. So:

| price | fee | breakeven win rate | profit per $1 on a win |
|-------|-----|--------------------|------------------------|
| 50c | 1.75c | 51.8% | +0.93 |
| 55c | 1.73c | 56.7% | +0.74 |
| 58.5c | 1.70c | 60.2% | +0.64 |
| 60c | 1.68c | 61.7% | +0.59 |
| 65c | 1.59c | 66.6% | +0.48 |

**In this band you must win roughly 52-67% of the time just to break even.** A
model that is "57% accurate" is a losing model at 58.5c. This is the number that
matters, and it is why accuracy is a misleading headline.

## Soccer, three-way, measured per leg

21,659 matches, five leagues, train before 2020-07-01, test after. Prices are the
closing line plus its actual overround, not the de-vigged fair value.

| band | legs | avg price | hit rate | ROI per trade |
|------|------|-----------|----------|---------------|
| 35-50c | 4,691 | 43.1c | 42.21% | **-2.06%** |
| **50-65c** | **2,928** | **58.6c** | **57.68%** | **-1.37%** |
| 65-80c | 1,498 | 73.5c | 74.17% | +1.20% |

Two things to read from this:

1. **The band is efficient.** At 58.6c the market delivers 57.68%. The price is
   almost exactly right, and the overround is what kills you. There is no free
   money sitting in the middle of the book.
2. **The only positive band is 65-80c** (+1.20% on 1,498 legs). That is the
   favourite-longshot bias, it is small, and it is *not* the band you asked for.

## And the model actively hurts inside the band

Filtering to legs where the model thinks the price is too low made it worse,
not better:

| filter | legs | model says | actually won | ROI |
|--------|------|-----------|--------------|-----|
| model > price by 0c | 1,372 | 63.7% | 55.9% | **-4.08%** |
| model > price by 2c | 1,062 | 64.8% | 55.1% | **-5.18%** |
| model > price by 4c | 763 | 66.0% | 55.2% | **-4.68%** |
| model > price by 6c | 500 | 67.5% | 55.4% | **-3.75%** |

When the model disagrees with the close inside this band, **the model is wrong
by about 8 points on average.** Its confidence is the tell. This is not a small
effect; it is the single clearest result in this whole file.

## Tennis, two-way, and a bug that mattered

79,299 ATP matches, 2000-2026. Held out 2024 onward: 7,261 matches where both
players already had a history.

Raw Elo got 63.79% accuracy and log loss 0.6402. But in the band it claimed
57.46% and delivered **54.35%** -- overconfident by 3.1 points, same direction as
soccer.

One parameter fixes it. Shrink the logit toward 50% by a factor fitted on
2022-2023 and frozen:

    calibrated = sigmoid(0.68 * logit(raw))

Out of sample on 2024+:

| metric | raw | calibrated |
|--------|-----|------------|
| band prediction | 57.46% | 57.18% |
| band actual | 54.35% | **57.13%** |
| band n | 3,146 | 4,353 |
| 95% CI | [52.6, 56.1] | **[55.7, 58.6]** |
| log loss | 0.6402 | **0.6309** |

The calibrated tennis model is honest inside the band across every bucket
(all within 1.8 points). That makes it **safe to use as a probability** -- and
it still does not make the band pay, because being calibrated at 57% when the
price is 58.5c is a 1.5c loss per trade.

### The bug worth remembering

The first version of the tennis update dropped the *loser* by
`k * (1 - E_winner)` instead of `k * E_loser`. For a 1900-vs-1500 pairing that
punished the underdog by **119 rating points instead of 12** -- ten times too
hard. Symptoms: 63.79% accuracy alongside a log loss of 0.1934, which is
arithmetically impossible, and a bucket where the model was "89% confident" and
won 60.69%.

Lesson: when two aggregate metrics contradict each other, one of them is a bug.
I caught it only because accuracy and log loss refused to agree.

## So what is this system actually good for?

It is a **calibrated probability engine with a band filter**, not an edge.

- Use `sports predict` for a three-way soccer read.
- Use `sports scan` for two-way tennis, with the shrink applied and inactivity
  decay so retired players stop generating fake edges.
- The band filter will mostly say **do nothing**, and that is the correct output.

What it will not do is beat a sharp closing line, because the measurements above
say it cannot. The thing that pays in this repo remains what it always was:
buying when a *price* is demonstrably below a *sharp de-vigged probability*,
which is `sports sides` and `sports/edge.py`, and which requires a real
disagreement with a real book -- not a better guess at team strength.
