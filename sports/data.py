"""Loading match results and closing odds, without silently mangling either.

The source format is football-data.co.uk: one CSV per league per season, with a
`Date` column written `DD/MM/YY` for older files and `DD/MM/YYYY` or ISO for
newer ones. That inconsistency has a specific failure mode worth naming: string
sorting puts `01/02/14` after `31/12/2022`, which silently scrambles the
chronology. Every rating model and every backtest downstream then reads the
future. It is the same class of bug as the look-ahead in the five-minute work,
and it is just as invisible in the output.
"""

from __future__ import annotations

import csv
import glob
import os
from dataclasses import dataclass

HOME, DRAW, AWAY = 0, 1, 2


def parse_date(text: str) -> tuple[int, int, int]:
    """`DD/MM/YY`, `DD/MM/YYYY` or `YYYY-MM-DD` -> (year, month, day)."""
    t = text.strip()
    for sep in ("/", "-"):
        if sep in t:
            parts = [p for p in t.split(sep) if p]
            if len(parts) != 3:
                continue
            if len(parts[0]) == 4:                 # ISO
                return (int(parts[0]), int(parts[1]), int(parts[2]))
            day, month, year = int(parts[0]), int(parts[1]), int(parts[2])
            if year < 100:
                year += 2000 if year < 70 else 1900
            return (year, month, day)
    raise ValueError(f"unrecognised date {text!r}")


def _odds(row: dict) -> list[float] | None:
    """Closing odds, sharpest book first: Pinnacle, then Bet365."""
    for cols in (("PSH", "PSD", "PSA"), ("B365H", "B365D", "B365A")):
        try:
            vals = [float(row[c]) for c in cols]
        except (KeyError, ValueError, TypeError):
            continue
        if all(v > 1.0 for v in vals):
            return vals
    return None


@dataclass
class Match:
    league: str
    date: tuple[int, int, int]
    home: str
    away: str
    home_goals: int
    away_goals: int
    odds: list[float]            # [home, draw, away], closing

    @property
    def result(self) -> int:
        if self.home_goals > self.away_goals:
            return HOME
        if self.home_goals == self.away_goals:
            return DRAW
        return AWAY

    @property
    def market(self) -> list[float]:
        """De-vigged implied probabilities. These sum to 1 by construction."""
        inv = [1.0 / o for o in self.odds]
        total = sum(inv)
        return [x / total for x in inv]

    @property
    def overround(self) -> float:
        """How far behind you start: the book's margin, as a fraction."""
        return sum(1.0 / o for o in self.odds) - 1.0


def load_dir(path: str) -> list[Match]:
    """Every match in a directory of football-data.co.uk season files."""
    out: list[Match] = []
    for f in sorted(glob.glob(os.path.join(path, "*.csv"))):
        league = os.path.basename(f)[:2]
        with open(f, errors="ignore", newline="") as fh:
            for row in csv.DictReader(fh):
                try:
                    hg, ag = int(row["FTHG"]), int(row["FTAG"])
                except (KeyError, ValueError, TypeError):
                    continue
                odds = _odds(row)
                if odds is None:
                    continue
                try:
                    date = parse_date(row["Date"])
                except (KeyError, ValueError):
                    continue
                out.append(Match(league, date, row["HomeTeam"], row["AwayTeam"],
                                 hg, ag, odds))
    # Chronological, and this is exactly the line that must not be a string sort
    out.sort(key=lambda m: (m.date, m.league, m.home))
    return out
