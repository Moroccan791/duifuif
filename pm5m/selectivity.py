"""Selectivity: can you get the win rate up to 70% at 50-60c?

The question is legitimate even though "win rate" alone is meaningless, because
at a FIXED price band a higher win rate is unambiguously a higher edge. At 55c,
every extra point of accuracy is worth a cent per share. So "can I win 70% of
trades at 55c?" is a well-posed question about how much edge is available.

There is exactly one lever that raises win rate at a fixed price, and it is not
a better model: **take fewer, better trades.** Every filter you add removes the
marginal trades first, so the surviving subset has a higher hit rate. The cost
is trade count, and therefore total profit -- a 75% win rate on 20 trades earns
less than a 63% win rate on 400.

So the real question is not "can I win 70%" but "what does 70% cost me in
opportunity", and whether the surviving trades are enough to compound.

This module answers both by sweeping the filter threshold and reporting the
full trade-off curve, then breaking the edge down by condition so you can see
WHERE the edge concentrates rather than guessing.

The honest caveat, stated once and loudly: in simulation the edge comes from a
HYPOTHESIS about the counterparty. A 70% win rate against a spot-watching book
is a 70% win rate against a book that may not exist. Only recorded data settles
that.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field, replace

from .config import Settings
from .expectancy import TradeRecord, analyze
from .sim import run_simulation


@dataclass
class SweepRow:
    min_edge_cents: float
    min_z_lock: float
    trades: int
    win_rate: float
    avg_price: float
    roi: float
    total_pnl: float
    expectancy: float
    p_value: float
    windows: int

    def pretty(self) -> str:
        return (
            f"{self.min_edge_cents:9.1f}c {self.min_z_lock:8.1f} "
            f"{self.trades:8d} {self.win_rate*100:9.1f}% "
            f"{self.avg_price*100:9.1f}c {self.roi*100:+11.1f}% "
            f"${self.total_pnl:+10.2f} {self.p_value:9.3f}"
        )


HEADER = (
    f"{'min edge':>10} {'min z':>8} {'trades':>8} {'win rate':>10} "
    f"{'avg fill':>10} {'roi':>12} {'pnl':>11} {'p-value':>9}"
)


def sweep(
    scenarios: dict[str, dict] | None = None,
    windows: int = 300,
    edge_grid: tuple[float, ...] = (0.5, 1.0, 2.0, 3.0, 5.0, 8.0, 12.0),
    z_grid: tuple[float, ...] = (0.0,),
    seed: int = 11,
    base_price: float = 0.55,
) -> dict[str, list[SweepRow]]:
    """Run the sim across a grid of edge thresholds.

    `scenarios` maps a label to run_simulation kwargs, so you can compare a
    favourable counterparty against an informed one in the same table.
    """
    if scenarios is None:
        scenarios = {
            "MAKER, spot-watching counterparty (the hypothesis)": dict(
                counterparty="naive_spot", basis_sd=2e-5, sigma_noise_sd=0.5,
                counterparty_noise_cents=1.5, _mode="maker",
            ),
            "MAKER, informed counterparty (honest default)": dict(
                counterparty="fair", basis_sd=2e-5, sigma_noise_sd=0.5,
                counterparty_noise_cents=1.5, _mode="maker",
            ),
            "TAKER, spot-watching counterparty": dict(
                counterparty="naive_spot", basis_sd=2e-5, sigma_noise_sd=0.5,
                _mode="taker",
            ),
            "TAKER, informed counterparty": dict(
                counterparty="fair", basis_sd=2e-5, sigma_noise_sd=0.5,
                _mode="taker",
            ),
        }

    out: dict[str, list[SweepRow]] = {}
    for label, spec in scenarios.items():
        kwargs = {k: v for k, v in spec.items() if not k.startswith("_")}
        mode = spec.get("_mode", "maker")
        rows: list[SweepRow] = []
        for me in edge_grid:
            for zl in z_grid:
                cfg = replace(
                    Settings(),
                    strategy_mode=mode,
                    min_price=0.50,
                    max_price=0.60,
                    min_edge_cents=me,
                    min_z_lock=zl,
                    maker_quote_shares=40.0,
                )
                res = run_simulation(cfg, n_windows=windows, seed=seed, **kwargs)
                px = res.avg_price or base_price
                shares = 40.0
                # Reconstruct each trade: a win pays `shares`, a loss pays 0,
                # and both cost shares*px. Fees are zero in maker mode.
                records = [
                    TradeRecord(
                        ts=float(i),
                        slug="s",
                        side="up",
                        price=px,
                        shares=shares,
                        fee=0.0,
                        won=i < res.wins,
                        pnl=(shares if i < res.wins else 0.0) - shares * px,
                    )
                    for i in range(res.trades)
                ]
                exp = analyze(records) if records else None
                rows.append(
                    SweepRow(
                        min_edge_cents=me,
                        min_z_lock=zl,
                        trades=res.trades,
                        win_rate=res.wins / res.trades if res.trades else 0.0,
                        avg_price=res.avg_price,
                        roi=res.roi,
                        total_pnl=res.pnl,
                        expectancy=res.pnl / res.trades if res.trades else 0.0,
                        p_value=exp.p_value if exp else 1.0,
                        windows=res.windows,
                    )
                )
        out[label] = rows
    return out


def highest_win_rate(rows: list[SweepRow], min_trades: int = 30) -> SweepRow | None:
    """Best win rate that still has enough trades to mean something."""
    eligible = [r for r in rows if r.trades >= min_trades]
    if not eligible:
        return None
    return max(eligible, key=lambda r: r.win_rate)


def best_by_profit(rows: list[SweepRow]) -> SweepRow | None:
    return max(rows, key=lambda r: r.total_pnl) if rows else None


# ---------------------------------------------------------------------------
# conditional breakdown
# ---------------------------------------------------------------------------
@dataclass
class Bucket:
    label: str
    n: int
    wins: int
    avg_edge: float
    pnl: float

    @property
    def win_rate(self) -> float:
        return self.wins / self.n if self.n else 0.0

    @property
    def expectancy(self) -> float:
        return self.pnl / self.n if self.n else 0.0

    def pretty(self) -> str:
        return (
            f"{self.label:<22} {self.n:7d} {self.win_rate*100:8.1f}% "
            f"{self.avg_edge*100:9.2f}c ${self.expectancy:+8.3f}"
        )


def bucket_by(
    trades: list[TradeRecord],
    key: str,
    boundaries: list[float],
    labels: list[str] | None = None,
) -> list[Bucket]:
    """Group trades into buckets of `key` and report win rate per bucket.

    This is how you find WHERE the edge lives instead of assuming it is uniform.
    A strategy whose edge is concentrated in one bucket should trade only that
    bucket -- and knowing which is worth more than any indicator.
    """
    if labels is None:
        labels = [f"{boundaries[i]:.0f}-{boundaries[i+1]:.0f}" for i in range(len(boundaries) - 1)]
    buckets: list[list[TradeRecord]] = [[] for _ in labels]
    for t in trades:
        v = float(getattr(t, key, 0.0))
        for i in range(len(boundaries) - 1):
            if boundaries[i] <= v < boundaries[i + 1]:
                buckets[i].append(t)
                break
    out = []
    for label, group in zip(labels, buckets):
        if not group:
            continue
        out.append(
            Bucket(
                label=label,
                n=len(group),
                wins=sum(1 for t in group if t.won),
                avg_edge=sum(t.edge for t in group) / len(group),
                pnl=sum(t.pnl for t in group),
            )
        )
    return out


BUCKET_HEADER = f"{'bucket':<22} {'trades':>7} {'win rate':>9} {'avg edge':>10} {'exp/trade':>10}"


def sample_trades(n: int = 400, win_rate: float = 0.62, price: float = 0.55) -> list[TradeRecord]:
    """Synthetic trades for testing the bucketing machinery."""
    rng = random.Random(3)
    out = []
    for i in range(n):
        won = rng.random() < win_rate
        pnl = (40.0 if won else 0.0) - price * 40.0
        out.append(
            TradeRecord(
                ts=float(i), slug=f"s{i}", side="up", price=price, shares=40.0,
                fee=0.0, won=won, pnl=pnl,
                edge=rng.uniform(0.01, 0.10),
                z=rng.uniform(0.0, 5.0),
                remaining_s=rng.uniform(20.0, 280.0),
            )
        )
    return out
