from __future__ import annotations

import numpy as np
import pandas as pd

from .config import BacktestConfig
from .backtest import BacktestResult


def performance_summary(result: BacktestResult, config: BacktestConfig) -> tuple[dict, pd.DataFrame]:
    equity = result.equity["equity"]
    returns = equity.pct_change().dropna()
    days = max((equity.index[-1] - equity.index[0]).total_seconds() / 86_400, 1)
    total_return = equity.iloc[-1] / config.initial_equity - 1
    annualized = (1 + total_return) ** (365 / days) - 1
    sharpe = np.sqrt(24 * 365) * returns.mean() / returns.std(ddof=0) if returns.std(ddof=0) else np.nan
    drawdown = equity / equity.cummax() - 1
    ulcer_index = np.sqrt(np.mean(np.square(drawdown)))
    daily_equity = equity.resample("1D").last().dropna()
    daily_returns = daily_equity.pct_change().dropna()
    weekly_returns = equity.resample("W-SUN").last().pct_change().dropna()
    if len(daily_equity) >= 2:
        x = np.arange(len(daily_equity))
        slope, intercept = np.polyfit(x, daily_equity.to_numpy(), 1)
        fitted = slope * x + intercept
        variance = np.square(daily_equity - daily_equity.mean()).sum()
        equity_trend_r2 = 1 - np.square(daily_equity - fitted).sum() / variance if variance else np.nan
    else:
        equity_trend_r2 = np.nan
    below_peak = drawdown < 0
    drawdown_groups = (below_peak != below_peak.shift()).cumsum()
    max_drawdown_duration_days = 0.0
    for _, group in below_peak.groupby(drawdown_groups):
        if group.iloc[0]:
            max_drawdown_duration_days = max(max_drawdown_duration_days, (group.index[-1] - group.index[0]).total_seconds() / 86_400)
    trades = result.trades
    winners = trades[trades.net_pnl > 0] if not trades.empty else trades
    losers = trades[trades.net_pnl < 0] if not trades.empty else trades
    summary = {
        "total_return": total_return, "annualized_return": annualized, "sharpe_ratio": sharpe,
        "max_drawdown": drawdown.min(), "win_rate": len(winners) / len(trades) if len(trades) else np.nan,
        "profit_factor": winners.net_pnl.sum() / abs(losers.net_pnl.sum()) if len(losers) and losers.net_pnl.sum() else np.nan,
        "turnover": trades.notional.sum() * 2 if not trades.empty else 0.0,
        "fees": trades.fees.sum() if not trades.empty else 0.0, "trades": len(trades),
        "ulcer_index": ulcer_index, "daily_return_volatility": daily_returns.std(ddof=0) if len(daily_returns) else np.nan,
        "max_weekly_loss": weekly_returns.min() if len(weekly_returns) else np.nan,
        "profitable_week_fraction": (weekly_returns > 0).mean() if len(weekly_returns) else np.nan,
        "equity_trend_r2": equity_trend_r2, "max_drawdown_duration_days": max_drawdown_duration_days,
    }
    monthly_equity = equity.resample("ME").last()
    monthly = monthly_equity.pct_change().to_frame("return")
    if not monthly.empty:
        monthly.iloc[0, monthly.columns.get_loc("return")] = monthly_equity.iloc[0] / config.initial_equity - 1
    monthly["equity"] = monthly_equity
    return summary, monthly
