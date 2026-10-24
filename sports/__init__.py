"""Sports prediction, and the thing that actually decides whether it pays.

THE SHORT VERSION

A model that predicts match results from results alone does not beat a closing
betting line. Measured on 21,659 matches across five leagues and eleven seasons
(`FINDINGS.md`), a standard Elo model scored 52.58% against the market's 53.73%,
and had worse log loss (1.0002 vs 0.9706). Betting its disagreements with the
close lost money at every threshold: -4% to -9% return on stake.

So this package does not pretend to predict. It does two things:

1. `elo`     - build and calibrate a ratings model, so any claim can be checked
               against the closing line rather than against "did I guess right".
2. `edge`    - given a price on Polymarket and a sharp reference price, decide
               whether the difference is large enough to survive the vig.

The bot's job is not to know who wins. It is to notice when Polymarket's price
disagrees with a sharp book's price by more than the cost of being wrong, and
to bet only then. That is the only one of these that was ever profitable.

WHY "GETTING 50-50S RIGHT" IS THE WRONG TARGET

A market priced at 50c is not a market that is hard to predict; it is a market
where the crowd has already concluded the outcome is a coin flip, and they are
right. In this dataset the 1,541 matches priced within 45-55% were called
correctly 49.32% of the time by the market and 48.80% by the model. Nobody is
right on coin flips. Profit comes from the price being wrong, not from the
event being predictable.
"""

__all__ = ["data", "elo", "edge", "backtest"]
