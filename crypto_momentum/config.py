from __future__ import annotations

from dataclasses import dataclass, field
from typing import FrozenSet


@dataclass(frozen=True)
class BacktestConfig:
    initial_equity: float = 100_000.0
    max_gross_leverage: float = 2.0
    # Optional fixed USDT notional per entry; capped by the gross-leverage rule.
    fixed_position_notional: float | None = None
    max_position_equity_fraction: float | None = None
    # Optional loss-at-stop budget per position.  It controls size, not the signal.
    position_risk_fraction: float | None = None
    max_positions: int = 10
    long_count: int = 5
    short_count: int = 5
    rank_exit_threshold: int = 10
    stop_loss_pct: float = 0.03
    stop_loss_atr_multiple: float | None = None
    atr_hours: int = 24
    trailing_profit_pct: float = 0.05
    trailing_activation_pct: float | None = None
    time_stop_hours: int = 0
    taker_fee_rate: float = 0.0004
    slippage_bps: float = 3.0
    funding_rate_default: float = 0.0
    # 400 days ensures a 12-month test has adequate pre-signal history.
    min_listing_age_days: int = 400
    min_quote_volume: float = 5_000_000.0
    max_pairwise_correlation: float = 0.80
    correlation_lookback_hours: int = 168
    momentum_hours: tuple[int, int, int] = (24, 72, 168)
    fast_ma_hours: int = 24
    slow_ma_hours: int = 120
    rsi_hours: int = 14
    volume_lookback_hours: int = 24
    breakout_lookback_hours: int = 20
    require_breakout_entry: bool = True
    entry_confirmation_bars: int = 1
    momentum_weight: float = 0.60
    ma_weight: float = 0.15
    rsi_weight: float = 0.10
    volume_weight: float = 0.10
    funding_weight: float = 0.05
    open_interest_weight: float = 0.10
    min_abs_score: float = 0.0
    # Portfolio-level whipsaw brake. Zero disables it.
    stop_cluster_window_hours: int = 24
    stop_cluster_threshold: int = 0
    stop_cluster_cooldown_hours: int = 0
    # Prevent immediate re-entry in the same market after its stop is hit.
    post_stop_cooldown_bars: int = 0
    # Reduce, rather than disable, entries when a market's recent ATR is elevated.
    high_volatility_atr_pct: float | None = None
    high_volatility_size_multiplier: float = 1.0
    stablecoin_bases: FrozenSet[str] = field(default_factory=lambda: frozenset({
        "USDT", "USDC", "BUSD", "TUSD", "FDUSD", "DAI", "USDP", "USDD", "FRAX", "PYUSD",
    }))
