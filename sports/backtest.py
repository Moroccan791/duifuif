"""Does the model beat the closing line? Accuracy, log loss, and money.

Accuracy is the number people want and the number that matters least. A model
can be right more often than the market and still lose, because the market
charged a price. The three measures here are ordered by how much they matter:
return on stake first, log loss second, accuracy a distant third.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .data import Match


def split(matches: list[Match], cut: tuple[int, int, int]) -> tuple[list, list]:
    train = [m for m in matches if m.date < cut]
    test = [m for m in matches if m.date >= cut]
    return train, test


def accuracy(rows: list[tuple[list[float], int]]) -> float:
    if not rows:
        return 0.0
    return sum(1 for p, r in rows if max(range(3), key=lambda i: p[i]) == r) / len(rows)


def log_loss(rows: list[tuple[list[float], int]]) -> float:
    if not rows:
        return 0.0
    return -sum(math.log(max(p[r], 1e-9)) for p, r in rows) / len(rows)


def roi(bets: list[tuple[float, bool]]) -> tuple[int, float, float]:
    """(n, accuracy, return on stake) for (odds, won) pairs."""
    if not bets:
        return 0, 0.0, 0.0
    wins = sum(1 for _o, won in bets if won)
    pnl = sum((o - 1.0) if won else -1.0 for o, won in bets)
    return len(bets), wins / len(bets), pnl / len(bets)


@dataclass
class Report:
    n: int
    market_accuracy: float
    model_accuracy: float
    market_logloss: float
    model_logloss: float
    coin_flip_n: int
    coin_flip_market: float
    coin_flip_model: float

    def pretty(self) -> str:
        return "\n".join([
            f"matches assessed        {self.n:,}",
            f"market accuracy         {self.market_accuracy:.2%}",
            f"model accuracy          {self.model_accuracy:.2%}",
            f"market log loss         {self.market_logloss:.4f}",
            f"model log loss          {self.model_logloss:.4f}",
            "",
            f"coin flips (45-55% home) n={self.coin_flip_n:,}",
            f"  market called          {self.coin_flip_market:.2%}",
            f"  model called           {self.coin_flip_model:.2%}",
        ])


def walk_forward(model, test: list[Match]) -> list[tuple[list[float], int]]:
    """Predict each match BEFORE its result is used to update the ratings.

    This is the difference between a backtest and a fantasy. It also matters in
    the other direction: fitting the calibration table before walking the
    ratings puts every match in one bin, and the model collapses to "always
    home" -- which scores 42.74% and looks like a bad model rather than a bug.
    """
    rows: list[tuple[list[float], int]] = []
    for m in test:
        rows.append((model.predict(m.home, m.away), m.result))
        model.walk([m])
    return rows


def evaluate(test: list[Match], model_rows: list[tuple[list[float], int]]) -> Report:
    """Model vs market, on the same matches, with no peeking.

    Takes the walk-forward predictions rather than computing them, so that the
    betting test and the accuracy test necessarily use the same numbers. Calling
    `model.predict` again afterwards would be a look-ahead: by then the ratings
    have been updated with the results being predicted.
    """
    market_rows = [(m.market, m.result) for m in test]
    coin = [(m, i) for i, (m, p) in enumerate(zip(test, [m.market for m in test]))
            if 0.45 <= p[0] <= 0.55]
    coin_market = accuracy([(m.market, m.result) for m, _ in coin])
    coin_model = accuracy([model_rows[i] for _, i in coin])
    return Report(
        n=len(test),
        market_accuracy=accuracy(market_rows),
        model_accuracy=accuracy(model_rows),
        market_logloss=log_loss(market_rows),
        model_logloss=log_loss(model_rows),
        coin_flip_n=len(coin),
        coin_flip_market=coin_market,
        coin_flip_model=coin_model,
    )


def bet_disagreements(test: list[Match],
                      model_rows: list[tuple[list[float], int]],
                      edge: float) -> list[tuple[float, bool]]:
    """Back the model wherever it thought the price was wrong by more than `edge`.

    `model_rows` must come from `walk_forward`, not from `model.predict` — the
    model's ratings have already absorbed the results by the time this is called.
    """
    bets: list[tuple[float, bool]] = []
    for m, (probs, _result) in zip(test, model_rows):
        market = m.market
        for i in range(3):
            if probs[i] - market[i] > edge:
                bets.append((m.odds[i], m.result == i))
    return bets
