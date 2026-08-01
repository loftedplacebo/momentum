"""Walk-forward parameter search for research; it never places orders."""

from __future__ import annotations

from dataclasses import replace
from itertools import product
from pathlib import Path
import json
import random

import numpy as np
import pandas as pd

from .backtest import MomentumBacktester
from .config import BacktestConfig
from .reporting import performance_summary
from .signals import build_signal_frame


def impactful_parameter_grid(base: BacktestConfig, max_scenarios: int = 144) -> list[BacktestConfig]:
    """A deliberately bounded grid: broad enough for research, small enough to audit."""
    values = product(
        (2, 3, 5),              # positions per side
        (6, 10),                # rank exit breadth
        ((0.02, None), (0.03, None), (None, 1.5), (None, 2.0)),  # percent or ATR stop
        (0.04, 0.06, 0.08),     # trailing exit
        (12, 20, 36),           # breakout confirmation window
        (0.70,),                # correlation cap (held fixed in this search)
        ((0, 0), (6, 12), (6, 24)),  # stop-cluster brake (threshold, cooldown)
        (0, 24, 48, 72),        # time stop
        (0.0, 0.75, 1.25),      # minimum absolute cross-sectional score
    )
    configs = [replace(base, long_count=side_count, short_count=side_count,
                       max_positions=side_count * 2, rank_exit_threshold=rank_exit,
                       stop_loss_pct=stop_pct or base.stop_loss_pct, stop_loss_atr_multiple=atr_multiple, trailing_profit_pct=trailing,
                       breakout_lookback_hours=breakout, max_pairwise_correlation=corr,
                       stop_cluster_threshold=cluster_threshold,
                       stop_cluster_cooldown_hours=cooldown_hours, time_stop_hours=time_stop,
                       min_abs_score=score_threshold)
               for side_count, rank_exit, stop_mode, trailing, breakout, corr, brake, time_stop, score_threshold in values
               for stop_pct, atr_multiple in (stop_mode,)
               for cluster_threshold, cooldown_hours in (brake,)]
    if max_scenarios >= len(configs):
        return configs
    # Preserve a fair disabled-vs-enabled comparison in small research runs.
    rng = random.Random(7)
    modes = [(0, 0), (6, 12), (6, 24)]
    chosen = [rng.choice([c for c in configs if (c.stop_cluster_threshold, c.stop_cluster_cooldown_hours) == mode])
              for mode in modes[:max_scenarios]]
    remaining = [c for c in configs if c not in chosen]
    chosen.extend(rng.sample(remaining, max_scenarios - len(chosen)))
    return chosen


def targeted_risk_grid(base: BacktestConfig, max_scenarios: int = 72) -> list[BacktestConfig]:
    """Test sizing and exits while leaving a previously-selected entry model unchanged."""
    configs = [replace(
        base,
        fixed_position_notional=None,
        max_gross_leverage=gross_leverage,
        max_position_equity_fraction=position_fraction,
        stop_loss_pct=stop_loss,
        stop_loss_atr_multiple=None,
        trailing_activation_pct=activation,
        trailing_profit_pct=trailing_distance,
        time_stop_hours=time_stop,
    ) for gross_leverage, position_fraction, stop_loss, activation, trailing_distance, time_stop in product(
        (0.75, 1.0, 1.25, 1.5),
        (0.10, 0.15, 0.20, 0.25),
        (0.02, 0.03),
        (0.02, 0.04, 0.06),
        (0.02, 0.03, 0.04),
        (24, 48, 72),
    )]
    if max_scenarios >= len(configs):
        return configs
    # Include a few sensible anchors, then sample the remaining combinations reproducibly.
    anchors = [c for c in configs if (c.max_gross_leverage, c.max_position_equity_fraction,
                                       c.stop_loss_pct, c.trailing_activation_pct,
                                       c.trailing_profit_pct, c.time_stop_hours) in {
        (1.0, 0.15, 0.02, 0.04, 0.03, 48),
        (0.75, 0.10, 0.02, 0.02, 0.02, 48),
        (1.25, 0.20, 0.03, 0.04, 0.03, 48),
    }]
    rng = random.Random(36)
    remaining = [c for c in configs if c not in anchors]
    return anchors + rng.sample(remaining, max_scenarios - len(anchors))


def run_walk_forward_search(prices: dict[str, pd.DataFrame], funding_rates: dict[str, pd.Series],
                            open_interest: dict[str, pd.Series], base: BacktestConfig,
                            max_scenarios: int = 144, train_fraction: float = 0.8,
                            progress_path: Path | None = None,
                            configs: list[BacktestConfig] | None = None,
                            random_validation_windows: int = 0,
                            validation_seed: int = 36) -> tuple[pd.DataFrame, dict]:
    """Select on the first period only; report the selected configuration on untouched data."""
    common_times = sorted(set.intersection(*(set(frame.index) for frame in prices.values())))
    if len(common_times) < 1_000:
        raise ValueError("Need at least 1,000 common hourly bars for a walk-forward search.")
    split = common_times[int(len(common_times) * train_fraction)]
    configs = configs or impactful_parameter_grid(base, max_scenarios)
    validation_windows: list[tuple[pd.Timestamp, pd.Timestamp]] = []
    if random_validation_windows:
        # Contiguous blocks preserve the serial dependence of markets; the final 20% stays unseen.
        train_bars = int(len(common_times) * train_fraction)
        block_bars = max(1_000, int(len(common_times) * 0.15))
        candidates = list(range(int(len(common_times) * 0.10), train_bars - block_bars))
        rng = random.Random(validation_seed)
        rng.shuffle(candidates)
        starts: list[int] = []
        for candidate in candidates:
            if all(abs(candidate - previous) >= block_bars for previous in starts):
                starts.append(candidate)
                if len(starts) == random_validation_windows:
                    break
        if len(starts) < random_validation_windows:
            raise ValueError("Not enough history for the requested independent validation windows.")
        validation_windows = [(common_times[start], common_times[start + block_bars]) for start in sorted(starts)]
    else:
        validation_windows = [(common_times[int(len(common_times) * start_fraction)],
                               common_times[int(len(common_times) * end_fraction)])
                              for start_fraction, end_fraction in ((0.4, 0.6), (0.6, 0.8))]
    signal_cache: dict[int, pd.DataFrame] = {}
    records: list[dict] = []
    for scenario_id, config in enumerate(configs):
        signals = signal_cache.setdefault(config.breakout_lookback_hours,
            build_signal_frame(prices, config, funding_rates, open_interest))
        # Validation windows select parameters; the final 20% remains untouched.
        validation_summaries = []
        for start, end in validation_windows:
            validation_prices = {symbol: frame.loc[start:end] for symbol, frame in prices.items()}
            validation_signals = signals[(signals.timestamp >= start) & (signals.timestamp <= end)]
            result = MomentumBacktester(config).run(validation_prices, validation_signals, funding_rates)
            validation_summaries.append(performance_summary(result, config)[0])
        summary = {key: float(np.nanmean([item[key] for item in validation_summaries])) for key in validation_summaries[0]}
        turnover_multiple = summary["turnover"] / config.initial_equity
        # Reward steady compounding while penalising deep/prolonged drawdowns and churn.
        objective = (
            summary["sharpe_ratio"]
            - 2 * abs(summary["max_drawdown"])
            - summary["ulcer_index"]
            - 0.5 * abs(summary["max_weekly_loss"])
            - 0.0001 * turnover_multiple
            + 0.25 * summary["equity_trend_r2"]
            + 0.10 * summary["profitable_week_fraction"]
        )
        records.append({
            "scenario_id": scenario_id, "objective": objective, **summary,
            "long_short_count": config.long_count, "rank_exit_threshold": config.rank_exit_threshold,
            "stop_loss_pct": config.stop_loss_pct, "trailing_profit_pct": config.trailing_profit_pct,
            "breakout_lookback_hours": config.breakout_lookback_hours,
            "max_pairwise_correlation": config.max_pairwise_correlation,
            "stop_cluster_threshold": config.stop_cluster_threshold,
            "stop_cluster_cooldown_hours": config.stop_cluster_cooldown_hours,
            "fixed_position_notional": config.fixed_position_notional,
            "stop_loss_atr_multiple": config.stop_loss_atr_multiple,
            "time_stop_hours": config.time_stop_hours,
            "min_abs_score": config.min_abs_score,
            "max_gross_leverage": config.max_gross_leverage,
            "max_position_equity_fraction": config.max_position_equity_fraction,
            "trailing_activation_pct": config.trailing_activation_pct,
        })
        if progress_path:
            progress_path.parent.mkdir(parents=True, exist_ok=True)
            current = pd.DataFrame(records).sort_values("objective", ascending=False).iloc[0]
            progress_path.write_text(json.dumps({
                "status": "running", "completed": scenario_id + 1, "total": len(configs),
                "latest": records[-1], "leader": current.to_dict(),
            }, indent=2, default=float))
    table = pd.DataFrame(records).sort_values("objective", ascending=False).reset_index(drop=True)
    winner_row = table.iloc[0]
    winner = configs[int(winner_row.scenario_id)]
    signals = signal_cache[winner.breakout_lookback_hours]
    test_prices = {symbol: frame.loc[split:] for symbol, frame in prices.items()}
    test_signals = signals[signals.timestamp >= split]
    test_result = MomentumBacktester(winner).run(test_prices, test_signals, funding_rates)
    test_summary, _ = performance_summary(test_result, winner)
    selection = {
        "train_end": str(split), "scenarios_tested": len(configs),
        "validation_windows": [[str(start), str(end)] for start, end in validation_windows],
        "selection_metric": "Sharpe - 2*abs(max_drawdown) - ulcer - 0.5*abs(max_weekly_loss) - 0.0001*turnover_multiple + 0.25*equity_R2 + 0.10*profitable_week_fraction",
        "winner": {key: winner_row[key] for key in table.columns if key != "scenario_id"},
        "out_of_sample": test_summary,
    }
    if progress_path:
        progress_path.write_text(json.dumps({"status": "complete", "completed": len(configs),
                                              "total": len(configs), "leader": table.iloc[0].to_dict(),
                                              "out_of_sample": test_summary}, indent=2, default=float))
    return table, selection


def save_search(output_dir: Path, table: pd.DataFrame, selection: dict) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    table.to_csv(output_dir / "training_scenarios.csv", index=False)
    (output_dir / "selection.json").write_text(json.dumps(selection, indent=2, default=float))
