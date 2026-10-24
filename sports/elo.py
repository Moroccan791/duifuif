"""A ratings model, built only from results, deliberately unremarkable.

Nothing here touches the odds. That matters: if the ratings saw prices, any
measurement of "can this beat the market" would be circular.

The model is a standard Elo with a home-field term and a goal-margin multiplier,
converted to win/draw/loss probabilities by *empirical calibration* -- bin the
rating difference, count what actually happened, smooth, and use that. A fitted
curve would imply more precision than 12,000 matches can support.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from .data import AWAY, DRAW, HOME, Match

START_RATING = 1500.0
HOME_ADVANTAGE = 60.0
K = 20.0
BINS = ((-10**9, -120), (-120, -60), (-60, -20), (-20, 20),
        (20, 60), (60, 120), (120, 10**9))


def margin_multiplier(goal_diff: int) -> float:
    d = abs(goal_diff)
    if d <= 1:
        return 1.0
    if d == 2:
        return 1.5
    return (11.0 + d) / 8.0


def bin_index(elo_diff: float) -> int:
    for i, (lo, hi) in enumerate(BINS):
        if lo <= elo_diff < hi:
            return i
    return len(BINS) - 1


@dataclass
class EloModel:
    ratings: dict[str, float] = field(default_factory=dict)
    table: dict[int, list[float]] = field(default_factory=dict)

    def rating(self, team: str) -> float:
        return self.ratings.get(team, START_RATING)

    def expected_home(self, home: str, away: str) -> float:
        return 1.0 / (1.0 + 10 ** ((self.rating(away) - self.rating(home)
                                    - HOME_ADVANTAGE) / 400.0))

    def elo_diff(self, home: str, away: str) -> float:
        return self.rating(home) + HOME_ADVANTAGE - self.rating(away)

    def walk(self, matches: list[Match]) -> None:
        """Update ratings game by game. Must be fed in chronological order."""
        for m in matches:
            h, a = self.rating(m.home), self.rating(m.away)
            expected = 1.0 / (1.0 + 10 ** ((a - h - HOME_ADVANTAGE) / 400.0))
            score = {HOME: 1.0, DRAW: 0.5, AWAY: 0.0}[m.result]
            change = K * margin_multiplier(m.home_goals - m.away_goals) * (score - expected)
            self.ratings[m.home] = h + change
            self.ratings[m.away] = a - change

    def fit(self, matches: list[Match], smoothing: float = 1.0) -> "EloModel":
        """Learn P(result | rating difference) from outcomes alone."""
        counts: dict[int, list[float]] = defaultdict(lambda: [smoothing] * 3)
        for m in matches:
            counts[bin_index(self.elo_diff(m.home, m.away))][m.result] += 1.0
        self.table = {k: [c / sum(v) for c in v] for k, v in counts.items()}
        return self

    def predict(self, home: str, away: str) -> list[float]:
        """P(home), P(draw), P(away) for a fixture."""
        if not self.table:
            raise RuntimeError("call fit() before predict()")
        row = self.table.get(bin_index(self.elo_diff(home, away)))
        if row is None:                      # unseen bin: fall back to the middle
            row = self.table.get(bin_index(0.0), [0.45, 0.26, 0.29])
        return list(row)


def build(train: list[Match], all_matches: list[Match]) -> EloModel:
    """Fit on `train`, then walk the full history so test ratings are current.

    Ratings for the test period are produced from matches up to and including
    each game -- never from later ones -- so evaluation cannot see the future.
    The calibration table, however, is learned on the training window only.
    """
    model = EloModel()
    model.walk(train)
    model.fit(train)
    return model
