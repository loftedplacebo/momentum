from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np
import pandas as pd

from .config import BacktestConfig
from .signals import ranks_at


@dataclass
class Position:
    symbol: str
    side: Literal[1, -1]
    quantity: float
    entry_price: float
    entry_time: pd.Timestamp
    highest_price: float
    lowest_price: float
    entry_fee: float
    stop_loss_pct: float


@dataclass
class BacktestResult:
    equity: pd.DataFrame
    trades: pd.DataFrame


class MomentumBacktester:
    def __init__(self, config: BacktestConfig):
        self.config = config

    def run(self, prices: dict[str, pd.DataFrame], signals: pd.DataFrame,
            funding_rates: dict[str, pd.Series] | None = None) -> BacktestResult:
        all_times = sorted(set.intersection(*(set(f.index) for f in prices.values())))
        return_arrays = {symbol: frame.loc[all_times, "close"].pct_change().to_numpy()
                         for symbol, frame in prices.items()}
        rank_cache = {}
        for timestamp, frame in signals.groupby("timestamp", sort=False):
            frame = frame.copy()
            frame["long_rank"] = frame["score"].rank(ascending=False, method="first")
            frame["short_rank"] = frame["score"].rank(ascending=True, method="first")
            rank_cache[timestamp] = frame
        cash = self.config.initial_equity
        positions: dict[str, Position] = {}
        trades: list[dict] = []
        rows: list[dict] = []
        stop_times: list[pd.Timestamp] = []
        cooldown_until: pd.Timestamp | None = None
        symbol_cooldowns: dict[str, pd.Timestamp] = {}
        for idx, timestamp in enumerate(all_times[:-1]):
            next_time = all_times[idx + 1]
            rank = rank_cache.get(timestamp)
            if rank is None:
                continue
            # Signal closes at timestamp; all decisions fill at next bar's open.
            exits = self._exits(positions, prices, rank, next_time)
            for symbol, reason, exit_price in exits:
                cash += self._close(positions.pop(symbol), exit_price, next_time, reason, trades)
                if reason == "stop_loss":
                    stop_times.append(next_time)
                    if self.config.post_stop_cooldown_bars:
                        symbol_cooldowns[symbol] = next_time + (next_time - timestamp) * self.config.post_stop_cooldown_bars
            if self.config.stop_cluster_threshold:
                window_start = next_time - pd.Timedelta(hours=self.config.stop_cluster_window_hours)
                stop_times = [time for time in stop_times if time >= window_start]
                if len(stop_times) >= self.config.stop_cluster_threshold:
                    candidate_until = next_time + pd.Timedelta(hours=self.config.stop_cluster_cooldown_hours)
                    cooldown_until = max(cooldown_until, candidate_until) if cooldown_until else candidate_until
            equity_before = self._marked_equity(cash, positions, prices, timestamp)
            targets = [] if cooldown_until and next_time < cooldown_until else self._targets(rank, positions, prices, timestamp)
            for symbol, side in targets:
                if symbol in positions or len(positions) >= self.config.max_positions:
                    continue
                if next_time < symbol_cooldowns.get(symbol, next_time):
                    continue
                if not self._correlation_ok(symbol, positions, return_arrays, idx):
                    continue
                open_price = float(prices[symbol].at[next_time, "open"])
                fill = self._fill(open_price, side, entering=True)
                signal_atr = float(rank.set_index("symbol").at[symbol, "atr_pct"])
                stop_pct = self.config.stop_loss_atr_multiple * signal_atr if self.config.stop_loss_atr_multiple else self.config.stop_loss_pct
                leverage_cap = equity_before * self.config.max_gross_leverage / self.config.max_positions
                fraction_cap = equity_before * self.config.max_position_equity_fraction if self.config.max_position_equity_fraction else leverage_cap
                risk_cap = equity_before * self.config.position_risk_fraction / stop_pct if self.config.position_risk_fraction else float("inf")
                notional = min(self.config.fixed_position_notional, leverage_cap, fraction_cap, risk_cap) if self.config.fixed_position_notional else min(leverage_cap, fraction_cap, risk_cap)
                if self.config.high_volatility_atr_pct and signal_atr >= self.config.high_volatility_atr_pct:
                    notional *= self.config.high_volatility_size_multiplier
                qty = notional / fill
                fee = notional * self.config.taker_fee_rate
                cash -= fee
                positions[symbol] = Position(symbol, side, qty, fill, next_time, fill, fill, fee, stop_pct)
            funding = self._apply_funding(positions, prices, next_time, funding_rates or {})
            cash -= funding
            equity = self._marked_equity(cash, positions, prices, next_time)
            gross = sum(abs(p.quantity * float(prices[s].at[next_time, "close"])) for s, p in positions.items())
            rows.append({"timestamp": next_time, "equity": equity, "cash": cash, "gross_exposure": gross, "funding": funding, "positions": len(positions), "cooldown_active": bool(cooldown_until and next_time < cooldown_until)})
        # Liquidate at final close to make return and trade metrics comparable.
        if all_times:
            final_time = all_times[-1]
            for symbol, position in list(positions.items()):
                cash += self._close(position, float(prices[symbol].at[final_time, "close"]), final_time, "end_of_test", trades)
            rows.append({"timestamp": final_time, "equity": cash, "cash": cash, "gross_exposure": 0.0, "funding": 0.0, "positions": 0})
        return BacktestResult(pd.DataFrame(rows).drop_duplicates("timestamp", keep="last").set_index("timestamp"), pd.DataFrame(trades))

    def _targets(self, rank, positions, prices, timestamp):
        entry_filter = rank.entry_ready if self.config.require_breakout_entry else True
        score_filter = rank.score.abs() >= self.config.min_abs_score
        longs = rank[(rank.direction == 1) & (rank.long_rank <= self.config.long_count) & entry_filter & score_filter].sort_values("long_rank")
        shorts = rank[(rank.direction == -1) & (rank.short_rank <= self.config.short_count) & entry_filter & score_filter].sort_values("short_rank")
        return [(x.symbol, 1) for x in longs.itertuples()] + [(x.symbol, -1) for x in shorts.itertuples()]

    def _exits(self, positions, prices, rank, next_time):
        output = []
        lookup = rank.set_index("symbol")
        for symbol, p in positions.items():
            bar = prices[symbol].loc[next_time]
            p.highest_price = max(p.highest_price, float(bar.high))
            p.lowest_price = min(p.lowest_price, float(bar.low))
            stop = p.entry_price * (1 - p.stop_loss_pct) if p.side == 1 else p.entry_price * (1 + p.stop_loss_pct)
            trail = p.highest_price * (1 - self.config.trailing_profit_pct) if p.side == 1 else p.lowest_price * (1 + self.config.trailing_profit_pct)
            adverse_hit = float(bar.low) <= stop if p.side == 1 else float(bar.high) >= stop
            trail_hit = float(bar.low) <= trail if p.side == 1 else float(bar.high) >= trail
            if adverse_hit:  # conservative if both stop and trail are reachable intrabar
                output.append((symbol, "stop_loss", stop))
            elif trail_hit and ((p.highest_price / p.entry_price - 1 >= (self.config.trailing_activation_pct or self.config.trailing_profit_pct)) if p.side == 1 else (p.entry_price / p.lowest_price - 1 >= (self.config.trailing_activation_pct or self.config.trailing_profit_pct))):
                output.append((symbol, "trailing_exit", trail))
            elif self.config.time_stop_hours and next_time - p.entry_time >= pd.Timedelta(hours=self.config.time_stop_hours):
                output.append((symbol, "time_stop", float(bar.open)))
            elif symbol not in lookup.index or (p.side == 1 and lookup.at[symbol, "long_rank"] > self.config.rank_exit_threshold) or (p.side == -1 and lookup.at[symbol, "short_rank"] > self.config.rank_exit_threshold):
                output.append((symbol, "rank_exit", float(bar.open)))
        return output

    def _close(self, p, raw_price, timestamp, reason, trades):
        fill = self._fill(raw_price, p.side, entering=False)
        pnl = p.side * p.quantity * (fill - p.entry_price)
        fee = abs(p.quantity * fill) * self.config.taker_fee_rate
        net = pnl - fee
        trades.append({"symbol": p.symbol, "side": "long" if p.side == 1 else "short", "entry_time": p.entry_time, "exit_time": timestamp, "entry_price": p.entry_price, "exit_price": fill, "notional": p.quantity * p.entry_price, "gross_pnl": pnl, "fees": p.entry_fee + fee, "net_pnl": net - p.entry_fee, "reason": reason})
        return net

    def _fill(self, price, side, entering):
        slip = self.config.slippage_bps / 10_000
        direction = side if entering else -side
        return price * (1 + direction * slip)

    def _marked_equity(self, cash, positions, prices, timestamp):
        return cash + sum(p.side * p.quantity * (float(prices[s].at[timestamp, "close"]) - p.entry_price) for s, p in positions.items())

    def _apply_funding(self, positions, prices, timestamp, funding_rates):
        # Positive funding means longs pay shorts. Apply only at supplied funding timestamps.
        payment = 0.0
        for symbol, p in positions.items():
            rate = self.config.funding_rate_default / 8
            series = funding_rates.get(symbol)
            if series is not None:
                rate = float(series.get(timestamp, 0.0))
            payment += p.side * p.quantity * float(prices[symbol].at[timestamp, "close"]) * rate
        return payment

    def _correlation_ok(self, symbol, positions, return_arrays, time_index):
        if not positions:
            return True
        start = max(0, time_index - self.config.correlation_lookback_hours + 1)
        candidate = return_arrays[symbol][start:time_index + 1]
        for existing in positions:
            other = return_arrays[existing][start:time_index + 1]
            valid = np.isfinite(candidate) & np.isfinite(other)
            corr = np.corrcoef(candidate[valid], other[valid])[0, 1] if valid.sum() >= 3 else np.nan
            if np.isfinite(corr) and abs(corr) > self.config.max_pairwise_correlation:
                return False
        return True
