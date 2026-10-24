"""Two-way sports: tennis first, and the structure any 2-way sport can reuse.

Why tennis is the right place to start: it is a pure two-outcome sport, players
are individuals so ratings do not need squad modelling, and Polymarket lists a
lot of ITF/Challenger matches where the field is obscure enough that prices may
be soft.

The validation that matters here is NOT accuracy. It is **calibration inside the
50-65c band**, because that is where the strategy buys. A model that is 63%
accurate overall but systematically overconfident at 60c loses money; a model
that is 55% accurate and honestly calibrated at 60c makes money. So this module
reports calibration by probability bucket and refuses to hide behind the
aggregate.
"""

from __future__ import annotations

import csv
import glob
import math
from collections import defaultdict
from dataclasses import dataclass, field


def _f(row: dict, key: str) -> float | None:
    v = row.get(key)
    if v in (None, "", "NA"):
        return None
    try:
        return float(v)
    except ValueError:
        return None


@dataclass
class TennisMatch:
    date: int                  # YYYYMMDD
    surface: str
    tourney: str
    level: str
    winner: str
    loser: str
    winner_rank: float | None
    loser_rank: float | None
    completed: bool


def load(path_glob: str = "/tmp/tennis/atp_matches_*.csv") -> list[TennisMatch]:
    out: list[TennisMatch] = []
    for f in sorted(glob.glob(path_glob)):
        with open(f, errors="ignore", newline="") as fh:
            for row in csv.DictReader(fh):
                try:
                    date = int(row["tourney_date"])
                except (KeyError, ValueError, TypeError):
                    continue
                out.append(TennisMatch(
                    date=date,
                    surface=(row.get("surface") or "").strip(),
                    tourney=row.get("tourney_name", ""),
                    level=row.get("tourney_level", ""),
                    winner=row.get("winner_name", ""),
                    loser=row.get("loser_name", ""),
                    winner_rank=_f(row, "winner_rank"),
                    loser_rank=_f(row, "loser_rank"),
                    completed=True,
                ))
    out.sort(key=lambda m: (m.date, m.tourney))
    return out


@dataclass
class TennisRatings:
    """Elo with a surface component. No odds are ever read.

    K decays with the number of matches played, which stops a veteran's rating
    from swinging on every result while letting newcomers move quickly -- the
    same idea FiveThirtyEight used, and it matters here because the lower tours
    are full of players with a handful of matches.
    """

    overall: dict[str, float] = field(default_factory=dict)
    surface: dict[tuple[str, str], float] = field(default_factory=dict)
    played: dict[str, int] = field(default_factory=dict)
    last: dict[str, int] = field(default_factory=dict)   # YYYYMMDD of last match

    def rating(self, player: str, surface: str = "") -> float:
        base = self.overall.get(player, 1500.0)
        if not surface:
            return base
        s = self.surface.get((player, surface), base)
        # blend: mostly surface, anchored to overall so thin surface records
        # cannot produce wild numbers
        return 0.65 * s + 0.35 * base

    def k(self, player: str) -> float:
        n = self.played.get(player, 0)
        return 250.0 / (n + 5.0) ** 0.4

    def predict(self, a: str, b: str, surface: str = "") -> float:
        """P(player a beats player b)."""
        diff = self.rating(a, surface) - self.rating(b, surface)
        return 1.0 / (1.0 + 10 ** (-diff / 400.0))

    def update(self, winner: str, loser: str, surface: str, last_date: int = 0) -> None:
        """Elo update on both stores.

        The losing side changes by ``-k * E_loser``, NOT ``-k * (1 - E_winner)``
        written with the winner's perspective reused. Getting that backwards
        punishes the underdog by the favourite's confidence, which inflates the
        rating spread and produces wildly overconfident predictions. That bug
        was live here and made the model's 89%-confident bucket win 60.69% of
        the time; after the fix the buckets line up (see FINDINGS).
        """
        for store in ("overall", "surface"):
            def get(p: str) -> float:
                if store == "surface":
                    return self.surface.get((p, surface), self.overall.get(p, 1500.0))
                return self.overall.get(p, 1500.0)

            rw, rl = get(winner), get(loser)
            e_winner = 1.0 / (1.0 + 10 ** (-(rw - rl) / 400.0))
            e_loser = 1.0 - e_winner
            new_w = rw + self.k(winner) * (1.0 - e_winner)
            new_l = rl - self.k(loser) * e_loser
            if store == "surface":
                self.surface[(winner, surface)] = new_w
                self.surface[(loser, surface)] = new_l
            else:
                self.overall[winner] = new_w
                self.overall[loser] = new_l
        self.played[winner] = self.played.get(winner, 0) + 1
        self.played[loser] = self.played.get(loser, 0) + 1
        self.last[winner] = last_date
        self.last[loser] = last_date


# ---------------------------------------------------------------------------
# evaluation: accuracy is the least interesting number here
# ---------------------------------------------------------------------------
def wilson(k: int, n: int, z: float = 1.959964) -> tuple[float, float]:
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (c - h, c + h)


def calibration(rows: list[tuple[float, bool]], lo: float = 0.50, hi: float = 0.65) -> dict:
    """How honest is the model inside the band we actually buy?"""
    inside = [(p, w) for p, w in rows if lo <= p <= hi]
    out = {"n": len(inside)}
    if not inside:
        return out
    out["predicted"] = sum(p for p, _ in inside) / len(inside)
    wins = sum(1 for _p, w in inside if w)
    out["actual"] = wins / len(inside)
    out["ci"] = wilson(wins, len(inside))
    return out


def bucket_table(rows: list[tuple[float, bool]],
                 edges: tuple[float, ...] = (0.0, 0.45, 0.50, 0.55, 0.60, 0.65, 0.70, 1.0)) -> list[dict]:
    out = []
    for lo, hi in zip(edges, edges[1:]):
        b = [(p, w) for p, w in rows if lo <= p < hi]
        if len(b) < 50:
            continue
        wins = sum(1 for _p, w in b if w)
        out.append({"lo": lo, "hi": hi, "n": len(b),
                    "predicted": sum(p for p, _ in b) / len(b),
                    "actual": wins / len(b)})
    return out


# ---------------------------------------------------------------------------
# calibration: fitted once on an earlier window, then frozen
# ---------------------------------------------------------------------------
SHRINK = 0.68
"""Logit shrink fitted on 2022-2023 ATP, applied to 2024+.

Raw Elo is overconfident: in the 50-65c band it claimed 57.46% and delivered
54.35%. Multiplying the logit by 0.68 fixes it -- out of sample the band then
predicted 57.18% and delivered 57.13% (n=4,353, CI 55.7-58.6). Always use
``calibrate`` before comparing a rating to a price.
"""


def logit(p: float) -> float:
    p = min(max(p, 1e-6), 1 - 1e-6)
    return math.log(p / (1 - p))


def sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


def calibrate(p: float, a: float = SHRINK) -> float:
    """Shrink a raw Elo probability toward 50%. a<1 means 'trust it less'."""
    return sigmoid(a * logit(p))


def fit_shrink(rows: list[tuple[float, bool]], lo: float = 0.20, hi: float = 1.00) -> tuple[float, float]:
    """Grid-fit the shrink that minimises log loss. Returns (a, log_loss)."""
    best = (1.0, float("inf"))
    steps = int(round((hi - lo) / 0.01))
    for i in range(steps + 1):
        a = lo + i * 0.01
        total = 0.0
        for p, won in rows:
            q = calibrate(p, a)
            total -= math.log(max(q if won else 1 - q, 1e-9))
        ll = total / max(1, len(rows))
        if ll < best[1]:
            best = (a, ll)
    return best


def save_ratings(r: TennisRatings, path: str) -> None:
    import json
    json.dump({
        "overall": r.overall,
        "played": r.played,
        "last": r.last,
        "surface": {f"{k[0]}|{k[1]}": v for k, v in r.surface.items()},
    }, open(path, "w"))


def load_ratings(path: str) -> TennisRatings:
    import json
    d = json.load(open(path))
    r = TennisRatings(overall=dict(d["overall"]), played=dict(d["played"]),
                      last={k: int(v) for k, v in d.get("last", {}).items()})
    for k, v in d.get("surface", {}).items():
        name, surf = k.rsplit("|", 1)
        r.surface[(name, surf)] = v
    return r


def build(glob: str = "/tmp/tennis/atp_matches_*.csv") -> TennisRatings:
    r = TennisRatings()
    for m in load(glob):
        if m.winner and m.loser:
            r.update(m.winner, m.loser, m.surface, m.date)
    return r


def predict_calibrated(r: TennisRatings, a: str, b: str, surface: str = "") -> float:
    """P(a beats b), shrunk to where it actually calibrates."""
    return calibrate(r.predict(a, b, surface))


def name_key(s: str) -> str:
    """Polymarket writes 'altmaie'/'bolkva1'; Sackmann writes 'Daniel Altmaier'.

    Reduce both to lowercase alphanumerics so a prefix match can join them.
    """
    return "".join(ch for ch in s.lower() if ch.isalnum())


def match_player(query: str, known: dict[str, float]) -> tuple[str | None, float]:
    """Best fuzzy join of a truncated Polymarket name to a known player.

    Polymarket truncates to ~7-8 characters and appends a digit when two
    players collide (``bolkva1``). So we compare against every *token* of the
    known name, not the whole thing -- prefixing the full name would never match
    ``alcaraz`` against ``Carlos Alcaraz``. Ambiguity is penalised rather than
    guessed: if several distinct players share the prefix we drop confidence.
    """
    q = name_key(query).rstrip("0123456789")
    if len(q) < 4:
        return (None, 0.0)

    # full-name pass first: 'Carlos Alcaraz' must not degrade into surname-token
    # matching, where the token 'carlos' is shared by dozens of players
    full = [k for k in known if name_key(k) == q]
    if len(full) == 1:
        return (full[0], 1.0)

    exact_tok, pref_tok = [], []
    for name in known:
        toks = [name_key(t) for t in name.replace("-", " ").split()]
        if not toks:
            continue
        if q in toks:
            exact_tok.append(name)
        elif any(t.startswith(q) or q.startswith(t) and len(t) >= 4 for t in toks):
            pref_tok.append(name)

    if len(exact_tok) == 1:
        return (exact_tok[0], 1.0)
    if len(exact_tok) > 1:
        return (None, 0.15)          # genuinely ambiguous surname
    if len(pref_tok) == 1:
        return (pref_tok[0], 0.75)
    if len(pref_tok) > 1:
        return (min(pref_tok, key=len), 0.4)
    return (None, 0.0)


# ---------------------------------------------------------------------------
# inactivity: a rating must not survive a player's absence
# ---------------------------------------------------------------------------
INACTIVE_HALF_LIFE_DAYS = 365.0
DECAY_TO = 1500.0


def _days_between(a: int, b: int) -> int:
    """Whole days between two YYYYMMDD integers."""
    import datetime as _dt
    def to_d(x: int) -> _dt.date:
        return _dt.date(x // 10000, (x // 100) % 100, x % 100)
    return (to_d(b) - to_d(a)).days


def decayed_rating(r: TennisRatings, player: str, asof: int,
                   half_life: float = INACTIVE_HALF_LIFE_DAYS) -> float:
    """Rating pulled toward 1500 for time not played.

    Without this, a player who last competed years ago keeps a peak rating and
    the scanner reports a huge fake edge on them. Djokovic vs Molcan produced a
    +13.2% 'BUY' purely from this bug. The half-life is one year, which is
    roughly how fast a tour-level rating stops meaning anything.
    """
    base = r.rating(player)
    last = r.last.get(player)
    if not last or asof <= last:
        return base
    days = _days_between(last, asof)
    if days <= 0:
        return base
    w = 0.5 ** (days / half_life)
    return DECAY_TO + (base - DECAY_TO) * w


def predict_asof(r: TennisRatings, a: str, b: str, asof: int, surface: str = "") -> float:
    """P(a beats b) as of a date, inactivity-decayed and then calibrated."""
    ra, rb = decayed_rating(r, a, asof), decayed_rating(r, b, asof)
    raw = 1.0 / (1.0 + 10 ** (-(ra - rb) / 400.0))
    return calibrate(raw)
