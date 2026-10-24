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
