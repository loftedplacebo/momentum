"""Stateful, no-order paper portfolio driven by completed Futures testnet candles."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .config import BacktestConfig
from .signals import build_signal_frame, ranks_at
from .testnet import BinanceFuturesTestnetClient


class TestnetPaperPortfolio:
    """Persist cash, positions, and trades locally while never sending exchange orders."""

    def __init__(self, config: BacktestConfig, client: BinanceFuturesTestnetClient,
                 state_file: Path, starting_equity: float, max_markets: int = 100) -> None:
        self.config = config
        self.client = client
        self.state_file = state_file
        self.starting_equity = starting_equity
        self.max_markets = max_markets

    def run_once(self) -> dict[str, Any]:
        state = self._load_state()
        now = pd.Timestamp.now(tz="UTC")
        if state.get("universe_date") != str(now.date()):
            state["universe_symbols"] = self.client.live_universe(self.config, self.max_markets)
            state["universe_date"] = str(now.date())
        requested = state.get("universe_symbols", [])
        prices, unavailable = self.client.hourly_price_frames(requested)
        if len(prices) < 10:
            raise RuntimeError(f"Only {len(prices)} live markets had enough completed candles.")
        common_bar = min(frame.index.max() for frame in prices.values())
        prices = {symbol: frame.loc[:common_bar] for symbol, frame in prices.items() if common_bar in frame.index}
        if state.get("last_completed_bar") == str(common_bar):
            return {"status": "unchanged", "completed_bar": str(common_bar), "positions": state["positions"],
                    "equity": state["equity"], "markets_loaded": len(prices)}
        signals = build_signal_frame(prices, self.config)
        ranked = ranks_at(signals, common_bar)
        exits = self._apply_exits(state, prices, ranked, common_bar)
        entries = self._apply_entries(state, prices, ranked, common_bar)
        state["last_completed_bar"] = str(common_bar)
        state["equity"] = self._equity(state, prices, common_bar)
        state["last_scan_at"] = str(now)
        state["markets_loaded"] = len(prices)
        state["unavailable_symbols"] = unavailable
        self._save_state(state)
        return {"status": "processed", "completed_bar": str(common_bar), "markets_requested": len(requested),
                "markets_loaded": len(prices), "unavailable_symbols": unavailable, "equity": state["equity"],
                "cash": state["cash"], "positions": list(state["positions"].values()), "entries": entries, "exits": exits}

    def _load_state(self) -> dict[str, Any]:
        if self.state_file.exists():
            payload = json.loads(self.state_file.read_text())
            if "cash" in payload and "positions" in payload:
                return payload
        return {"version": 1, "mode": "local_paper_only", "starting_equity": self.starting_equity,
                "cash": self.starting_equity, "equity": self.starting_equity, "positions": {}, "trades": []}

    def _save_state(self, state: dict[str, Any]) -> None:
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.state_file.with_suffix(".tmp")
        temporary.write_text(json.dumps(state, indent=2, default=str))
        temporary.replace(self.state_file)

    def _equity(self, state: dict[str, Any], prices: dict[str, pd.DataFrame], timestamp: pd.Timestamp) -> float:
        return float(state["cash"] + sum(position["side"] * position["quantity"] *
                     (float(prices[symbol].at[timestamp, "close"]) - position["entry_price"])
                     for symbol, position in state["positions"].items() if symbol in prices))

    def _fill(self, price: float, side: int, entering: bool) -> float:
        direction = side if entering else -side
        return price * (1 + direction * self.config.slippage_bps / 10_000)

    def _apply_exits(self, state: dict[str, Any], prices: dict[str, pd.DataFrame], rank: pd.DataFrame,
                     timestamp: pd.Timestamp) -> list[dict[str, Any]]:
        lookup = rank.set_index("symbol")
        exits = []
        for symbol, position in list(state["positions"].items()):
            if symbol not in prices:
                continue
            bar = prices[symbol].loc[timestamp]
            position["highest_price"] = max(position["highest_price"], float(bar.high))
            position["lowest_price"] = min(position["lowest_price"], float(bar.low))
            stop = position["entry_price"] * (1 - position["stop_loss_pct"]) if position["side"] == 1 else position["entry_price"] * (1 + position["stop_loss_pct"])
            trail = position["highest_price"] * (1 - self.config.trailing_profit_pct) if position["side"] == 1 else position["lowest_price"] * (1 + self.config.trailing_profit_pct)
            reason = None
            raw_exit = float(bar.close)
            if (position["side"] == 1 and float(bar.low) <= stop) or (position["side"] == -1 and float(bar.high) >= stop):
                reason, raw_exit = "stop_loss", stop
            elif self._trail_active(position) and ((position["side"] == 1 and float(bar.low) <= trail) or (position["side"] == -1 and float(bar.high) >= trail)):
                reason, raw_exit = "trailing_exit", trail
            elif self.config.time_stop_hours and timestamp - pd.Timestamp(position["entry_time"]) >= pd.Timedelta(hours=self.config.time_stop_hours):
                reason = "time_stop"
            elif symbol not in lookup.index or (position["side"] == 1 and lookup.at[symbol, "long_rank"] > self.config.rank_exit_threshold) or (position["side"] == -1 and lookup.at[symbol, "short_rank"] > self.config.rank_exit_threshold):
                reason = "rank_exit"
            if reason:
                fill = self._fill(raw_exit, position["side"], entering=False)
                pnl = position["side"] * position["quantity"] * (fill - position["entry_price"])
                fee = abs(position["quantity"] * fill) * self.config.taker_fee_rate
                state["cash"] += pnl - fee
                trade = {"symbol": symbol, "side": "long" if position["side"] == 1 else "short", "entry_time": position["entry_time"],
                         "exit_time": str(timestamp), "entry_price": position["entry_price"], "exit_price": fill,
                         "net_pnl": pnl - fee - position["entry_fee"], "fees": fee + position["entry_fee"], "reason": reason}
                state["trades"].append(trade)
                exits.append(trade)
                del state["positions"][symbol]
        return exits

    def _trail_active(self, position: dict[str, Any]) -> bool:
        activation = self.config.trailing_activation_pct or self.config.trailing_profit_pct
        return ((position["highest_price"] / position["entry_price"] - 1) >= activation if position["side"] == 1
                else (position["entry_price"] / position["lowest_price"] - 1) >= activation)

    def _apply_entries(self, state: dict[str, Any], prices: dict[str, pd.DataFrame], rank: pd.DataFrame,
                       timestamp: pd.Timestamp) -> list[dict[str, Any]]:
        ready = rank.entry_ready if self.config.require_breakout_entry else True
        strong = rank.score.abs() >= self.config.min_abs_score
        candidates = pd.concat([
            rank[(rank.direction == 1) & (rank.long_rank <= self.config.long_count) & ready & strong].sort_values("long_rank"),
            rank[(rank.direction == -1) & (rank.short_rank <= self.config.short_count) & ready & strong].sort_values("short_rank"),
        ])
        entries = []
        equity = self._equity(state, prices, timestamp)
        per_position = min(equity * self.config.max_gross_leverage / self.config.max_positions,
                           equity * float(self.config.max_position_equity_fraction or 1))
        returns = {symbol: frame.close.pct_change().tail(self.config.correlation_lookback_hours) for symbol, frame in prices.items()}
        for row in candidates.itertuples():
            if row.symbol in state["positions"] or len(state["positions"]) >= self.config.max_positions:
                continue
            if not self._correlation_ok(row.symbol, state["positions"], returns):
                continue
            side = int(row.direction)
            fill = self._fill(float(prices[row.symbol].at[timestamp, "close"]), side, entering=True)
            quantity = per_position / fill
            fee = per_position * self.config.taker_fee_rate
            state["cash"] -= fee
            position = {"symbol": row.symbol, "side": side, "quantity": quantity, "entry_price": fill,
                        "entry_time": str(timestamp), "highest_price": fill, "lowest_price": fill, "entry_fee": fee,
                        "stop_loss_pct": self.config.stop_loss_pct, "score": float(row.score)}
            state["positions"][row.symbol] = position
            entries.append({"symbol": row.symbol, "side": "long" if side == 1 else "short", "notional_usdt": per_position,
                            "quantity": quantity, "entry_price": fill, "score": float(row.score)})
        return entries

    def _correlation_ok(self, symbol: str, positions: dict[str, Any], returns: dict[str, pd.Series]) -> bool:
        for held in positions:
            paired = pd.concat([returns[symbol], returns[held]], axis=1).dropna()
            if len(paired) >= 3 and abs(float(paired.corr().iloc[0, 1])) > self.config.max_pairwise_correlation:
                return False
        return True
