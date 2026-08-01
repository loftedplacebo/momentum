from __future__ import annotations

import numpy as np
import pandas as pd

from .config import BacktestConfig


def build_signal_frame(prices: dict[str, pd.DataFrame], config: BacktestConfig,
                       funding_rates: dict[str, pd.Series] | None = None,
                       open_interest: dict[str, pd.Series] | None = None) -> pd.DataFrame:
    """Return per-symbol scores indexed by completed candle timestamp."""
    records = []
    for symbol, frame in prices.items():
        close = frame["close"].astype(float)
        returns = close.pct_change()
        result = pd.DataFrame(index=frame.index)
        prior_close = close.shift(1)
        true_range = pd.concat([frame["high"] - frame["low"], (frame["high"] - prior_close).abs(), (frame["low"] - prior_close).abs()], axis=1).max(axis=1)
        result["atr_pct"] = true_range.rolling(config.atr_hours, min_periods=config.atr_hours).mean() / close
        horizon_scores = []
        agreements = []
        for hours in config.momentum_hours:
            ret = close.pct_change(hours)
            # Fraction of hourly returns whose sign agrees with horizon return.
            positive_fraction = returns.gt(0).rolling(hours, min_periods=hours).mean()
            negative_fraction = returns.lt(0).rolling(hours, min_periods=hours).mean()
            consistency = positive_fraction.where(ret > 0, negative_fraction.where(ret < 0, 0.0))
            component = ret * (0.5 + consistency)
            horizon_scores.append(component)
            agreements.append(np.sign(ret))
            result[f"ret_{hours}h"] = ret
        result["momentum"] = pd.concat(horizon_scores, axis=1).mean(axis=1)
        signs = pd.concat(agreements, axis=1)
        result["direction"] = np.where((signs > 0).all(axis=1), 1, np.where((signs < 0).all(axis=1), -1, 0))
        fast_ma = close.ewm(span=config.fast_ma_hours, min_periods=config.fast_ma_hours).mean()
        slow_ma = close.ewm(span=config.slow_ma_hours, min_periods=config.slow_ma_hours).mean()
        result["ma_trend"] = (close / fast_ma - 1) + (fast_ma / slow_ma - 1)
        change = close.diff()
        gain, loss = change.clip(lower=0), -change.clip(upper=0)
        rs = gain.ewm(alpha=1 / config.rsi_hours, min_periods=config.rsi_hours).mean() / loss.ewm(alpha=1 / config.rsi_hours, min_periods=config.rsi_hours).mean()
        result["rsi"] = 100 - 100 / (1 + rs)
        result["volume_change"] = np.log(frame["volume"].astype(float) / frame["volume"].rolling(config.volume_lookback_hours).median())
        previous_high = frame["high"].shift(1).rolling(config.breakout_lookback_hours).max()
        previous_low = frame["low"].shift(1).rolling(config.breakout_lookback_hours).min()
        entry_ready = ((result["direction"] == 1) & (close > previous_high)) | ((result["direction"] == -1) & (close < previous_low))
        result["entry_ready"] = (entry_ready.rolling(config.entry_confirmation_bars,
                                                        min_periods=config.entry_confirmation_bars).sum()
                                 >= config.entry_confirmation_bars)
        if funding_rates and symbol in funding_rates:
            result["funding_rate"] = funding_rates[symbol].reindex(frame.index).ffill().fillna(0.0)
        else:
            result["funding_rate"] = 0.0
        if open_interest and symbol in open_interest:
            oi = open_interest[symbol].reindex(frame.index).ffill()
            result["open_interest_change"] = oi.pct_change(24)
        else:
            result["open_interest_change"] = 0.0
        result["symbol"] = symbol
        records.append(result.reset_index().rename(columns={"index": "timestamp"}))
    signals = pd.concat(records, ignore_index=True).dropna(subset=["momentum", "ma_trend", "rsi", "volume_change", "atr_pct"])
    # Cross-sectional normalisation prevents high-volatility contracts dominating purely by scale.
    for column in ("momentum", "ma_trend", "rsi", "volume_change", "funding_rate", "open_interest_change"):
        mean = signals.groupby("timestamp")[column].transform("mean")
        std = signals.groupby("timestamp")[column].transform("std").replace(0, np.nan)
        signals[f"{column}_z"] = ((signals[column] - mean) / std).fillna(0.0).clip(-3, 3)
    direction = signals["direction"]
    signals["score"] = (
        config.momentum_weight * signals["momentum_z"]
        + config.ma_weight * signals["ma_trend_z"]
        + config.rsi_weight * signals["rsi_z"]
        + config.volume_weight * direction * signals["volume_change_z"]
        - config.funding_weight * direction * signals["funding_rate_z"]
        + config.open_interest_weight * direction * signals["open_interest_change_z"]
    )
    return signals


def ranks_at(signals: pd.DataFrame, timestamp: pd.Timestamp) -> pd.DataFrame:
    frame = signals.loc[signals["timestamp"] == timestamp].copy()
    frame["long_rank"] = frame["score"].rank(ascending=False, method="first")
    frame["short_rank"] = frame["score"].rank(ascending=True, method="first")
    return frame
