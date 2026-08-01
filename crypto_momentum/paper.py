from __future__ import annotations

"""Paper-trading scaffold: signal generation and durable state, no exchange orders."""

import json
from pathlib import Path
import pandas as pd

from .backtest import MomentumBacktester
from .config import BacktestConfig
from .signals import build_signal_frame, ranks_at


class PaperTrader:
    def __init__(self, config: BacktestConfig, state_file: Path):
        self.config = config
        self.state_file = state_file

    def desired_positions(self, prices: dict[str, pd.DataFrame], funding_rates=None, open_interest=None) -> list[dict]:
        signals = build_signal_frame(prices, self.config, funding_rates, open_interest)
        timestamp = signals["timestamp"].max()
        ranked = ranks_at(signals, timestamp)
        entry_filter = ranked.entry_ready if self.config.require_breakout_entry else True
        score_filter = ranked.score.abs() >= self.config.min_abs_score
        longs = ranked[(ranked.direction == 1) & (ranked.long_rank <= self.config.long_count) & entry_filter & score_filter]
        shorts = ranked[(ranked.direction == -1) & (ranked.short_rank <= self.config.short_count) & entry_filter & score_filter]
        result = [{"symbol": x.symbol, "side": "long", "score": x.score} for x in longs.itertuples()]
        result += [{"symbol": x.symbol, "side": "short", "score": x.score} for x in shorts.itertuples()]
        return result

    def save_snapshot(self, positions: list[dict]) -> None:
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self.state_file.write_text(json.dumps({"desired_positions": positions}, indent=2, default=str))
