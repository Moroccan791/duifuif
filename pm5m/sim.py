"""Adversarial simulation harness.

The first version of this file priced the book from the bot's own model, so the
simulator could only ever report profit -- a straight arbitrage of itself. That
is the single most common way trading backtests lie, so it is gone.

This harness is built to FALSIFY the strategy. It models the three things that
actually decide the outcome in production:

1. **The counterparty is better informed than you.** By default, the market
   maker quotes at the *true* fair value of the settlement index (it knows
   sigma). If a bot has no informational advantage, it can only lose: it pays
   the spread and the fee, and its own estimation error does the rest.
2. **Basis risk.** Settlement uses Chainlink's BTC/USD 60s TWAP; the bot sees
   some other venue. `basis_sd` applies a persistent per-window log offset
   between the two. This is a *systematic* error, not noise you can average out.
3. **Sigma estimation error.** The bot estimates vol from a finite window while
   sigma drifts. `sigma_noise_sd` perturbs the bot's estimate, independently of
   the truth.

Set `maker_efficiency=0` to see the opposite extreme: a market maker quoting
stale prices. That is the world you would need for this to work, and the whole
point is to ask honestly whether it resembles reality.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

from .broker import PaperBroker
from .config import Settings
from .engine import Engine, WindowState
from .model import fair_up
from .risk import RiskManager
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
    log: list[str] = field(default_factory=list)

    @property
    def roi(self) -> float:
        return self.pnl / self.staked if self.staked else 0.0

    def pretty(self) -> str:
        lines = [
            f"windows simulated : {self.windows}",
            f"trades            : {self.trades}",
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
                f"(0.25 = coin flip, lower is better; {self.scored} windows)"
            )
            lines.append(f"model accuracy    : {self.accuracy:.1%}")
        return "\n".join(lines)


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
    seed: int = 11,
    dt: float = 2.0,
) -> SimResult:
    """Simulate `n_windows` consecutive windows against an informed maker.

    maker_efficiency: 1.0 = quotes true fair value (strong opponent),
                      0.0 = quotes a fixed 50/50 (clueless opponent).
    basis_sd:         sd of the persistent log offset between our feed and the
                      settlement feed, per window. 2e-5 is 2bp (~$2 on 100k).
    sigma_noise_sd:   sd of log error in the bot's sigma estimate, per window.
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

    for w in range(n_windows):
        start = t0 + w * window_s
        end = start + window_s
        path = _synth_path(rng, spot, sigma, n_ticks, dt)

        # The bot's feed is offset from the settlement feed for the whole window.
        basis = rng.gauss(0.0, basis_sd) if basis_sd else 0.0
        # The bot's vol estimate is wrong in a way it cannot detect.
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

        for i, p_true in enumerate(path):
            ts = start + i * dt
            observed = p_true * math.exp(basis)
            engine.on_spot(ts, observed)

            # Maker fair value is computed from the TRUE path at the TRUE sigma.
            maker_p_up = fair_up(
                _true_index(path[: i + 1], dt, start, end),
                p_true,
                sigma,
                ts,
            ).p_up
            if maker_efficiency < 1.0:
                maker_p_up = maker_efficiency * maker_p_up + (1 - maker_efficiency) * 0.5
            ask_up = min(0.99, max(0.01, maker_p_up + half))
            ask_dn = min(0.99, max(0.01, (1 - maker_p_up) + half))

            for token, ask in ((mk.token_up, ask_up), (mk.token_down, ask_dn)):
                engine.on_book(
                    Book(
                        token_id=token,
                        ts=ts,
                        bids=(Level(max(0.01, ask - 2 * half), book_size),),
                        asks=(Level(ask, book_size),),
                    ),
                    "up" if token == mk.token_up else "down",
                    mk.slug,
                )

        # Score against the true settlement, then settle at the true outcome.
        ws = engine.windows.get(mk.slug)
        # Score calibration mid-window, not at the last tick. Late in the window
        # the outcome is nearly decided and every model scores well; that would
        # flatter the Brier score into meaninglessness.
        p_final = ws.mid_p_up if ws and ws.mid_p_up is not None else 0.5
        true_avg = _path_average(path, dt)
        outcome_up = true_avg >= path[0]
        briers.append((p_final - (1.0 if outcome_up else 0.0)) ** 2)
        correct += int((p_final > 0.5) == outcome_up)
        scored += 1

        engine.tick(end + 1e-6, outcome_fn=lambda _ws, up=outcome_up: "up" if up else "down")
        spot = path[-1]

    result.windows = n_windows
    result.trades = broker.n_trades
    result.wins = broker.n_wins
    result.losses = broker.n_losses
    result.pnl = broker.realized_pnl
    result.fees = broker.fees_paid
    result.staked = sum(f.notional + f.fee for f in broker.fills)
    result.log = engine.log[-40:]
    result.scored = scored
    result.accuracy = correct / scored if scored else 0.0
    result.brier = sum(briers) / len(briers) if briers else 0.25
    return result


def _true_index(true_path, dt, start, end):
    """Settlement index built from the TRUE path, for the maker's fair value.

    `end` must be the real window close. Getting this wrong (using the elapsed
    time instead) makes the maker believe the window has already ended, which
    makes it quote fake certainty and hands the bot free money -- a bug that
    once made this simulator report a 9,000% return.
    """
    from .settlement import SettlementIndex

    st = SettlementIndex(start_ts=start, end_ts=end, open_price=true_path[0])
    for i, p in enumerate(true_path):
        st.add(start + i * dt, p)
    return st


def breakeven_edge_cents(settings: Settings, price: float = 0.95) -> float:
    from .fees import taker_fee_per_share

    return taker_fee_per_share(price, settings.taker_fee_rate) * 100.0
