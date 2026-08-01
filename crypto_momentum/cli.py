from __future__ import annotations

import argparse
import json
from dataclasses import replace
from pathlib import Path

from .backtest import MomentumBacktester
from .config import BacktestConfig
from .data import BinanceKlineDownloader, combine_price_data, default_date_range, load_funding_data, load_open_interest_data, load_price_data
from .paper import PaperTrader
from .optimizer import active_15m_risk_grid, run_walk_forward_search, save_search, targeted_risk_grid
from .reporting import performance_summary
from .signals import build_signal_frame
from .universe import BinanceUniverseProvider, UniverseSnapshot
from .testnet import BinanceFuturesTestnetClient
from .live_paper import TestnetPaperPortfolio
from .kucoin import KuCoinFuturesProvider, KuCoinKlineDownloader


def main() -> None:
    parser = argparse.ArgumentParser(description="Binance perpetual momentum research")
    sub = parser.add_subparsers(dest="command", required=True)
    down = sub.add_parser("download")
    down.add_argument("--months", type=int, default=12)
    down.add_argument("--data-dir", type=Path, default=Path("data"))
    down.add_argument("--combined-file", type=Path, default=None)
    down.add_argument("--interval", choices=("15m", "1h"), default="1h")
    kucoin_down = sub.add_parser("kucoin-download", help="Download public KuCoin USDT perpetual candles and funding.")
    kucoin_down.add_argument("--months", type=int, default=12)
    kucoin_down.add_argument("--data-dir", type=Path, default=Path("data"))
    kucoin_down.add_argument("--interval", choices=("15m", "1h"), default="1h")
    kucoin_down.add_argument("--max-markets", type=int, default=100)
    run = sub.add_parser("backtest")
    run.add_argument("--data-dir", type=Path, default=Path("data"))
    run.add_argument("--output-dir", type=Path, default=Path("results"))
    winning = sub.add_parser("backtest-winning", help="Run the selected strategy at a time-equivalent candle interval.")
    winning.add_argument("--data-dir", type=Path, default=Path("data"))
    winning.add_argument("--output-dir", type=Path, default=Path("results_winning"))
    winning.add_argument("--strategy-selection", type=Path, default=Path("strategy_v1.json"))
    winning.add_argument("--bar-minutes", type=int, choices=(15, 60), default=60)
    active_risk = sub.add_parser("optimize-15m-risk", help="Robustness grid for the active 15-minute winning strategy.")
    active_risk.add_argument("--data-dir", type=Path, default=Path("data_15m"))
    active_risk.add_argument("--output-dir", type=Path, default=Path("optimization_15m_risk"))
    active_risk.add_argument("--strategy-selection", type=Path, default=Path("strategy_v1.json"))
    active_risk.add_argument("--max-scenarios", type=int, default=36)
    paper = sub.add_parser("paper")
    paper.add_argument("--data-dir", type=Path, default=Path("data"))
    paper.add_argument("--state-file", type=Path, default=Path("state/paper.json"))
    optimize = sub.add_parser("optimize")
    optimize.add_argument("--data-dir", type=Path, default=Path("data"))
    optimize.add_argument("--output-dir", type=Path, default=Path("optimization"))
    optimize.add_argument("--max-scenarios", type=int, default=144)
    optimize.add_argument("--fixed-trade-notional", type=float, default=None)
    risk = sub.add_parser("optimize-risk")
    risk.add_argument("--data-dir", type=Path, default=Path("data_36m"))
    risk.add_argument("--output-dir", type=Path, default=Path("optimization_risk_36m"))
    risk.add_argument("--base-selection", type=Path, default=Path("optimization_full_200/selection.json"))
    risk.add_argument("--max-scenarios", type=int, default=72)
    risk.add_argument("--validation-windows", type=int, default=3)
    testnet = sub.add_parser("testnet-check", help="Test the Binance Futures testnet connection and order sizing.")
    testnet.add_argument("--symbol", default="BTCUSDT")
    testnet.add_argument("--position-notional", type=float, default=5_000.0)
    testnet.add_argument("--validate-order", action="store_true", help="Call Binance's non-executing signed test-order endpoint.")
    testnet_paper = sub.add_parser("testnet-paper", help="Run one stateful, no-order paper portfolio cycle using Binance Futures testnet data.")
    testnet_paper.add_argument("--selection", type=Path, default=Path("strategy_v1.json"))
    testnet_paper.add_argument("--state-file", type=Path, default=Path("state/testnet_paper.json"))
    testnet_paper.add_argument("--starting-equity", type=float, default=5_000.0)
    testnet_paper.add_argument("--max-markets", type=int, default=100)
    args = parser.parse_args()
    config = BacktestConfig()
    if args.command == "testnet-check":
        client = BinanceFuturesTestnetClient()
        server_time = client.server_time()
        candles = client.hourly_klines(args.symbol, limit=3)
        mark_price = float(candles.iloc[-1].close)
        rules = client.symbol_rules(args.symbol)
        quantity = client.quantity_for_notional(args.position_notional, mark_price, rules)
        result = {"environment": "Binance USD(S)-M Futures testnet", "server_time": server_time,
                  "symbol": args.symbol, "last_completed_hour_close": mark_price,
                  "position_notional_usdt": args.position_notional, "rounded_quantity": quantity,
                  "order_validation": "not requested"}
        if args.validate_order:
            client.validate_market_order(args.symbol, "BUY", quantity)
            result["order_validation"] = "accepted by Binance test-order endpoint (not executed)"
        print(json.dumps(result, indent=2))
        return
    if args.command == "testnet-paper":
        selected = json.loads(args.selection.read_text())["winner"]
        side_count = int(selected["long_short_count"])
        config = replace(
            config, long_count=side_count, short_count=side_count, max_positions=side_count * 2,
            rank_exit_threshold=int(selected["rank_exit_threshold"]), stop_loss_pct=float(selected["stop_loss_pct"]),
            trailing_profit_pct=float(selected["trailing_profit_pct"]), trailing_activation_pct=float(selected["trailing_activation_pct"]),
            breakout_lookback_hours=int(selected["breakout_lookback_hours"]),
            max_pairwise_correlation=float(selected["max_pairwise_correlation"]), time_stop_hours=int(selected["time_stop_hours"]),
            min_abs_score=float(selected["min_abs_score"]), max_gross_leverage=float(selected["max_gross_leverage"]),
            max_position_equity_fraction=float(selected["max_position_equity_fraction"]),
        )
        client = BinanceFuturesTestnetClient()
        portfolio = TestnetPaperPortfolio(config, client, args.state_file, args.starting_equity, args.max_markets)
        print(json.dumps(portfolio.run_once(), indent=2, default=str))
        return
    universe_path = args.data_dir / "universe.json"
    if args.command == "download":
        download_config = replace(config, min_listing_age_days=args.months * 31 + 30)
        snapshot = BinanceUniverseProvider(download_config).select()
        snapshot.save(universe_path)
        start, end = default_date_range(args.months)
        BinanceKlineDownloader(args.interval).download(snapshot.symbols, start, end, args.data_dir)
        if args.combined_file:
            combine_price_data(args.data_dir, snapshot.symbols, args.combined_file)
        print(f"Saved {len(snapshot.symbols)} symbols to {args.data_dir}")
        return
    if args.command == "kucoin-download":
        download_config = replace(config, min_listing_age_days=args.months * 31 + 30)
        snapshot = KuCoinFuturesProvider(download_config).select(args.max_markets)
        snapshot.save(universe_path)
        start, end = default_date_range(args.months)
        KuCoinKlineDownloader(args.interval).download(snapshot.symbols, start, end, args.data_dir)
        print(f"Saved {len(snapshot.symbols)} KuCoin symbols to {args.data_dir}")
        return
    snapshot = UniverseSnapshot.load(universe_path)
    prices = load_price_data(args.data_dir, snapshot.symbols)
    # A full-year run must not be shortened by a recent listing that slipped into a prior snapshot.
    latest = max(frame.index.max() for frame in prices.values()) if prices else None
    if latest is not None:
        prices = {symbol: frame for symbol, frame in prices.items()
                  if (latest - frame.index.min()).days >= 365}
    if not prices:
        raise SystemExit("No cached price files found. Run the download command first.")
    if args.command == "paper":
        trader = PaperTrader(config, args.state_file)
        desired = trader.desired_positions(prices, load_funding_data(args.data_dir, snapshot.symbols), load_open_interest_data(args.data_dir, snapshot.symbols))
        trader.save_snapshot(desired)
        print(desired)
        return
    funding = load_funding_data(args.data_dir, snapshot.symbols)
    open_interest = load_open_interest_data(args.data_dir, snapshot.symbols)
    if args.command == "backtest-winning":
        selected = json.loads(args.strategy_selection.read_text())["winner"]
        side_count = int(selected["long_short_count"])
        scale = 60 // args.bar_minutes
        config = replace(
            config, long_count=side_count, short_count=side_count, max_positions=side_count * 2,
            rank_exit_threshold=int(selected["rank_exit_threshold"]), stop_loss_pct=float(selected["stop_loss_pct"]),
            trailing_profit_pct=float(selected["trailing_profit_pct"]), trailing_activation_pct=float(selected["trailing_activation_pct"]),
            breakout_lookback_hours=int(selected["breakout_lookback_hours"]) * scale,
            # This setting is calendar time, unlike indicator lookbacks which are bar counts.
            max_pairwise_correlation=float(selected["max_pairwise_correlation"]), time_stop_hours=int(selected["time_stop_hours"]),
            min_abs_score=float(selected["min_abs_score"]), max_gross_leverage=float(selected["max_gross_leverage"]),
            max_position_equity_fraction=float(selected["max_position_equity_fraction"]),
            momentum_hours=tuple(hours * scale for hours in config.momentum_hours),
            fast_ma_hours=config.fast_ma_hours * scale, slow_ma_hours=config.slow_ma_hours * scale,
            rsi_hours=config.rsi_hours * scale, volume_lookback_hours=config.volume_lookback_hours * scale,
            atr_hours=config.atr_hours * scale, correlation_lookback_hours=config.correlation_lookback_hours * scale,
        )
        result = MomentumBacktester(config).run(prices, build_signal_frame(prices, config, funding, open_interest), funding)
        summary, monthly = performance_summary(result, config)
        args.output_dir.mkdir(parents=True, exist_ok=True)
        result.equity.to_csv(args.output_dir / "equity.csv")
        result.trades.to_csv(args.output_dir / "trades.csv", index=False)
        monthly.to_csv(args.output_dir / "monthly.csv")
        (args.output_dir / "summary.json").write_text(json.dumps(summary, indent=2, default=float))
        print(json.dumps(summary, indent=2, default=float))
        return
    if args.command == "optimize-15m-risk":
        selected = json.loads(args.strategy_selection.read_text())["winner"]
        side_count = int(selected["long_short_count"])
        scale = 4
        base_config = replace(
            config, long_count=side_count, short_count=side_count, max_positions=side_count * 2,
            rank_exit_threshold=int(selected["rank_exit_threshold"]), stop_loss_pct=float(selected["stop_loss_pct"]),
            trailing_profit_pct=float(selected["trailing_profit_pct"]), trailing_activation_pct=float(selected["trailing_activation_pct"]),
            breakout_lookback_hours=int(selected["breakout_lookback_hours"]) * scale,
            # Keep the selected 48-hour maximum hold at 48 calendar hours on 15-minute data.
            max_pairwise_correlation=float(selected["max_pairwise_correlation"]), time_stop_hours=int(selected["time_stop_hours"]),
            min_abs_score=float(selected["min_abs_score"]), max_gross_leverage=float(selected["max_gross_leverage"]),
            max_position_equity_fraction=float(selected["max_position_equity_fraction"]),
            momentum_hours=tuple(hours * scale for hours in config.momentum_hours),
            fast_ma_hours=config.fast_ma_hours * scale, slow_ma_hours=config.slow_ma_hours * scale,
            rsi_hours=config.rsi_hours * scale, volume_lookback_hours=config.volume_lookback_hours * scale,
            atr_hours=config.atr_hours * scale, correlation_lookback_hours=config.correlation_lookback_hours * scale,
        )
        configs = active_15m_risk_grid(base_config, args.max_scenarios)
        table, selection = run_walk_forward_search(prices, funding, open_interest, base_config, configs=configs,
                                                    progress_path=args.output_dir / "progress.json")
        selection["research_scope"] = "Fixed active 15-minute momentum model; sizing, post-stop cooldown, entry confirmation and volatility throttle only."
        save_search(args.output_dir, table, selection)
        print(json.dumps(selection, indent=2, default=float))
        return
    if args.command == "optimize":
        config = replace(config, fixed_position_notional=args.fixed_trade_notional)
        table, selection = run_walk_forward_search(prices, funding, open_interest, config, args.max_scenarios,
                                                    progress_path=args.output_dir / "progress.json")
        save_search(args.output_dir, table, selection)
        print(json.dumps(selection, indent=2, default=float))
        return
    if args.command == "optimize-risk":
        selected = json.loads(args.base_selection.read_text())["winner"]
        side_count = int(selected["long_short_count"])
        base_config = replace(
            config, long_count=side_count, short_count=side_count, max_positions=side_count * 2,
            rank_exit_threshold=int(selected["rank_exit_threshold"]),
            stop_loss_pct=float(selected["stop_loss_pct"]),
            stop_loss_atr_multiple=selected.get("stop_loss_atr_multiple"),
            trailing_profit_pct=float(selected["trailing_profit_pct"]),
            breakout_lookback_hours=int(selected["breakout_lookback_hours"]),
            max_pairwise_correlation=float(selected["max_pairwise_correlation"]),
            stop_cluster_threshold=int(selected["stop_cluster_threshold"]),
            stop_cluster_cooldown_hours=int(selected["stop_cluster_cooldown_hours"]),
            time_stop_hours=int(selected["time_stop_hours"]),
            min_abs_score=float(selected["min_abs_score"]),
        )
        configs = targeted_risk_grid(base_config, args.max_scenarios)
        table, selection = run_walk_forward_search(
            prices, funding, open_interest, base_config, args.max_scenarios,
            progress_path=args.output_dir / "progress.json", configs=configs,
            random_validation_windows=args.validation_windows,
        )
        selection["base_selection"] = str(args.base_selection)
        selection["research_scope"] = "Fixed entry model; account-relative sizing and exit-risk controls only."
        save_search(args.output_dir, table, selection)
        print(json.dumps(selection, indent=2, default=float))
        return
    result = MomentumBacktester(config).run(prices, build_signal_frame(prices, config, funding, open_interest), funding)
    summary, monthly = performance_summary(result, config)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    result.equity.to_csv(args.output_dir / "equity.csv")
    result.trades.to_csv(args.output_dir / "trades.csv", index=False)
    monthly.to_csv(args.output_dir / "monthly.csv")
    (args.output_dir / "summary.json").write_text(__import__("json").dumps(summary, indent=2, default=float))
    for key, value in summary.items():
        print(f"{key}: {value}")


if __name__ == "__main__":
    main()
