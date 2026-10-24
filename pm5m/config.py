"""Runtime configuration.

Every knob that can lose you money is here, in one place, with a default that
is deliberately conservative. Values are read from environment variables so a
shell history or a stray commit does not leak a private key.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, replace


def _env_str(name: str, default: str) -> str:
    return os.environ.get(name, default)


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    return default if raw is None or raw == "" else float(raw)


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    return default if raw is None or raw == "" else int(raw)


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    # ---- venue -----------------------------------------------------------
    gamma_url: str = "https://gamma-api.polymarket.com"
    clob_url: str = "https://clob.polymarket.com"
    ws_url: str = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
    series_slug: str = "btc-up-or-down-5m"
    asset: str = "btc"
    window_s: float = 300.0

    # ---- fees (verified Oct 2026) ---------------------------------------
    # Crypto fee category: fee = shares * rate * p * (1 - p). Makers pay 0.
    taker_fee_rate: float = 0.07
    maker_rebate_share: float = 0.20

    # ---- settlement model ------------------------------------------------
    # Settlement compares the Chainlink 60s-TWAP stream averaged across the
    # window against the price at window open. We approximate that index as the
    # time-average of our own spot feed across the window. Test that
    # approximation with scripts/validate.py before trusting any signal.
    vol_lookback_s: float = 1800.0
    vol_fallback_per_sqrt_s: float = 8.5e-5  # ~2.5% daily
    drift_per_s: float = 0.0
    z_cap: float = 6.0  # never claim more certainty than 6 sigma

    # ---- execution realism ----------------------------------------------
    latency_s: float = 0.35
    max_book_fraction: float = 0.50  # take at most half of displayed size
    slippage_ticks: float = 1.0

    # ---- risk ------------------------------------------------------------
    bankroll_usd: float = 1_000.0
    kelly_fraction: float = 0.25
    max_position_usd: float = 25.0
    max_total_exposure_usd: float = 150.0
    max_daily_loss_usd: float = 50.0
    max_trades_per_hour: int = 60
    cooldown_after_loss_s: float = 120.0
    min_edge_cents: float = 1.0
    min_remaining_s: float = 20.0
    max_price: float = 0.985
    min_z_lock: float = 2.5

    # ---- ops -------------------------------------------------------------
    db_path: str = "data/pm5m.sqlite3"
    live: bool = False
    dry_run: bool = True
    kill_switch_path: str = "STOP"

    @classmethod
    def from_env(cls) -> "Settings":
        base = cls()
        return replace(
            base,
            series_slug=_env_str("PM5M_SERIES_SLUG", base.series_slug),
            asset=_env_str("PM5M_ASSET", base.asset),
            db_path=_env_str("PM5M_DB", base.db_path),
            bankroll_usd=_env_float("PM5M_BANKROLL_USD", base.bankroll_usd),
            kelly_fraction=_env_float("PM5M_KELLY_FRACTION", base.kelly_fraction),
            max_position_usd=_env_float("PM5M_MAX_POSITION_USD", base.max_position_usd),
            max_total_exposure_usd=_env_float(
                "PM5M_MAX_TOTAL_EXPOSURE_USD", base.max_total_exposure_usd
            ),
            max_daily_loss_usd=_env_float(
                "PM5M_MAX_DAILY_LOSS_USD", base.max_daily_loss_usd
            ),
            min_edge_cents=_env_float("PM5M_MIN_EDGE_CENTS", base.min_edge_cents),
            min_z_lock=_env_float("PM5M_MIN_Z_LOCK", base.min_z_lock),
            max_price=_env_float("PM5M_MAX_PRICE", base.max_price),
            latency_s=_env_float("PM5M_LATENCY_S", base.latency_s),
            taker_fee_rate=_env_float("PM5M_TAKER_FEE_RATE", base.taker_fee_rate),
            live=_env_bool("PM5M_LIVE", base.live),
            dry_run=_env_bool("PM5M_DRY_RUN", base.dry_run),
            kill_switch_path=_env_str("PM5M_KILL_SWITCH", base.kill_switch_path),
        )
