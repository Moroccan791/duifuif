"""Adversarial simulation harness.

This harness exists to FALSIFY strategies, not to flatter them. An earlier
version priced the book from the bot's own model, so the bot was arbitraging
itself and the harness reported a 9,000% return. Everything here is arranged so
that cannot happen silently.

Four things decide the outcome in production, and all four are modelled:

1. **The counterparty.** `counterparty="fair"` means the book quotes the true
   fair value of the settlement index. A bot with no informational advantage
   then pays the spread and loses. `counterparty="naive_spot"` means the book
   prices "will it finish above the open?" -- the retail model, which ignores
   the averaging. That is the one plausible source of genuine mid-range
   mispricing, and it is a hypothesis you test, not an assumption you make.
2. **Quote noise.** A resting bid only fills when someone wants to sell to you,
   which is precisely when the thing is about to be worth less. This is adverse
   selection, and it emerges here endogenously from the fill condition.
3. **Basis risk.** Settlement uses Chainlink's BTC/USD 60s TWAP; the bot sees a
   different venue. `basis_sd` applies a persistent per-window log offset. This
   is a systematic error, not noise you can average out.
4. **Sigma estimation error.** `sigma_noise_sd` perturbs the bot's vol estimate
   independently of the truth.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

from .broker import PaperBroker
from .config import Settings
from .engine import Engine
from .model import fair_up, norm_cdf
from .risk import RiskManager
from .settlement import SettlementIndex
from .types import Book, Level, Market
from .vol import SpotBuffer


@dataclass
class SimResult:
    windows: int = 0
    trades: int = 0
    wins: int = 0
    losses: int = 0
    pnl: float = 0.0
    fees: float = 0.0
    staked: float = 0.0
    brier: float = 0.0
    accuracy: float = 0.0
    scored: int = 0
    avg_price: float = 0.0
    log: list[str] = field(default_factory=list)

    @property
    def roi(self) -> float:
        return self.pnl / self.staked if self.staked else 0.0

    def pretty(self) -> str:
        lines = [
            f"windows simulated : {self.windows}",
            f"trades            : {self.trades}"
            + (f"  avg fill {self.avg_price * 100:.1f}c" if self.trades else ""),
            f"outcome           : {self.wins}W / {self.losses}L"
            + (f"  ({self.wins / self.trades:.1%} win rate)" if self.trades else ""),
            f"total staked      : ${self.staked:,.2f}",
            f"fees paid         : ${self.fees:,.2f}",
            f"net pnl           : ${self.pnl:,.2f}",
            f"roi on stake      : {self.roi:+.2%}",
        ]
        if self.scored:
            lines.append(
                f"model brier       : {self.brier:.4f} "
                f"(0.25 = coin flip; {self.scored} windows)"
            )
        return "\n".join(lines)


def retail_p_up(spot: float, open_price: float, sigma: float, tau: float) -> float:
    """How a spot-watcher prices it: "will it finish above the open?"

    Ignores that settlement is a path average. This is what most participants
    appear to do, and it is the most plausible source of real mid-range
    mispricing -- a hypothesis, not a fact.
    """
    if tau <= 1.0 or spot <= 0 or open_price <= 0:
        return 1.0 if spot > open_price else 0.0
    z = math.log(spot / open_price) / (sigma * math.sqrt(tau))
    return min(0.999, max(0.001, norm_cdf(z)))


def _synth_path(
    rng: random.Random, spot0: float, sigma: float, n: int, dt: float
) -> list[float]:
    out = [spot0]
    for _ in range(n):
        p = out[-1] * math.exp(
            -0.5 * sigma**2 * dt + sigma * math.sqrt(dt) * rng.gauss(0, 1)
        )
        out.append(p)
    return out


def _path_average(path: list[float], dt: float) -> float:
    if len(path) < 2:
        return path[0] if path else 0.0
    total = sum(path[1:-1]) * dt + 0.5 * (path[0] + path[-1]) * dt
    return total / (dt * (len(path) - 1))


def _index_upto(path: list[float], dt: float, start: float, end: float) -> SettlementIndex:
    """Settlement index of the TRUE path. `end` must be the real window close:
    using the elapsed time instead makes the counterparty believe the window has
    already ended, which hands the bot free money."""
    st = SettlementIndex(start_ts=start, end_ts=end, open_price=path[0])
    for i, p in enumerate(path):
        st.add(start + i * dt, p)
    return st


def run_simulation(
    settings: Settings | None = None,
    n_windows: int = 200,
    sigma: float = 8.5e-5,
    spot0: float = 100_000.0,
    half_spread_cents: float = 1.0,
    book_size: float = 500.0,
    basis_sd: float = 0.0,
    sigma_noise_sd: float = 0.0,
    maker_efficiency: float = 1.0,
    counterparty_noise_cents: float = 0.0,
    counterparty: str = "fair",
    lag_ticks: int = 3,
    seed: int = 11,
    dt: float = 2.0,
) -> SimResult:
    """Simulate consecutive 5-minute windows.

    maker_efficiency: 1.0 = quotes true fair value (strong opponent),
                      0.5 = quotes halfway to 50/50,
                      0.0 = always quotes 50/50 (clueless).
    counterparty:     "fair" | "naive_spot" | "lagged"
    """
    settings = settings or Settings(bankroll_usd=1000.0)
    rng = random.Random(seed)
    risk = RiskManager(settings=settings)
    broker = PaperBroker(settings=settings, risk=risk)
    engine = Engine(
        settings=settings,
        broker=broker,
        risk=risk,
        spot_buffer=SpotBuffer(),
        sigma_override=sigma,
    )

    result = SimResult()
    briers: list[float] = []
    correct = 0
    scored = 0
    window_s = int(settings.window_s)
    n_ticks = max(2, int(window_s / dt))
    spot = spot0
    t0 = 1_700_000_000.0
    half = half_spread_cents / 100.0
    noise_sd = counterparty_noise_cents / 100.0

    for w in range(n_windows):
        start = t0 + w * window_s
        end = start + window_s
        path = _synth_path(rng, spot, sigma, n_ticks, dt)

        basis = rng.gauss(0.0, basis_sd) if basis_sd else 0.0
        engine.sigma_override = (
            sigma * math.exp(rng.gauss(0.0, sigma_noise_sd))
            if sigma_noise_sd
            else sigma
        )

        mk = Market(
            slug=f"{settings.asset}-updown-5m-{int(start)}",
            condition_id=f"sim{w}",
            asset=settings.asset,
            start_ts=start,
            end_ts=end,
            token_up=f"UP{w}",
            token_down=f"DOWN{w}",
            open_price=path[0] * math.exp(basis),
        )
        engine.on_market(mk)
        fair_hist: list[float] = []

        for i, p_true in enumerate(path):
            ts = start + i * dt
            engine.on_spot(ts, p_true * math.exp(basis))

            true_idx = _index_upto(path[: i + 1], dt, start, end)
            tau = end - ts
            fair_true = fair_up(true_idx, p_true, sigma, ts).p_up
            fair_hist.append(fair_true)

            if counterparty == "naive_spot":
                cp = retail_p_up(p_true, path[0], sigma, tau)
            elif counterparty == "lagged":
                cp = fair_hist[max(0, i - lag_ticks)]
            else:
                cp = fair_true
            cp = maker_efficiency * cp + (1 - maker_efficiency) * 0.5
            if noise_sd:
                cp = min(0.999, max(0.001, cp + rng.gauss(0.0, noise_sd)))

            ask_up = min(0.995, max(0.005, cp + half))
            ask_dn = min(0.995, max(0.005, (1 - cp) + half))
            for token, ask, side in (
                (mk.token_up, ask_up, "up"),
                (mk.token_down, ask_dn, "down"),
            ):
                engine.on_book(
                    Book(
                        token_id=token,
                        ts=ts,
                        bids=(Level(max(0.005, ask - 2 * half), book_size),),
                        asks=(Level(ask, book_size),),
                    ),
                    side,
                    mk.slug,
                )

        ws = engine.windows.get(mk.slug)
        p_mid = ws.mid_p_up if ws and ws.mid_p_up is not None else 0.5
        true_avg = _path_average(path, dt)
        outcome_up = true_avg >= path[0]
        briers.append((p_mid - (1.0 if outcome_up else 0.0)) ** 2)
        correct += int((p_mid > 0.5) == outcome_up)
        scored += 1

        engine.tick(end + 1e-6, outcome_fn=lambda _ws, up=outcome_up: "up" if up else "down")
        spot = path[-1]

    staked = sum(f.notional + f.fee for f in broker.fills)
    result.windows = n_windows
    result.trades = broker.n_trades
    result.wins = broker.n_wins
    result.losses = broker.n_losses
    result.pnl = broker.realized_pnl
    result.fees = broker.fees_paid
    result.staked = staked
    result.avg_price = (
        sum(f.price * f.shares for f in broker.fills) / sum(f.shares for f in broker.fills)
        if broker.fills
        else 0.0
    )
    result.log = engine.log[-40:]
    result.scored = scored
    result.accuracy = correct / scored if scored else 0.0
    result.brier = sum(briers) / len(briers) if briers else 0.25
    return result


def breakeven_edge_cents(settings: Settings, price: float = 0.95) -> float:
    from .fees import taker_fee_per_share

    return taker_fee_per_share(price, settings.taker_fee_rate) * 100.0
