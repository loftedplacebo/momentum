# Crypto Momentum: Backtest and Paper Trading

Start with [STRATEGY.md](STRATEGY.md) for the selected model and the differences
between the `1hr` and `15m` projects. See [DEPLOYMENT.md](DEPLOYMENT.md) for
GitHub, local setup and VPS paper-trading operations.
For the exchange-specific KuCoin variant, see [KUCOIN.md](KUCOIN.md).

A modular long/short momentum system for Binance USDT-margined perpetual futures.  The default implementation is deliberately **price-only**: it builds hourly cross-sectional momentum rankings and simulates execution without future data.  Funding, open interest, and order-book features have clear extension points but are not enabled by default.

## What it does

- Selects the liquid USDT perpetual universe using Binance 24-hour quote volume, excluding stablecoins and very new markets.
- Scores 24-hour, 3-day, and 7-day momentum using return and hourly trend consistency.
- Confirms trend quality with moving-average alignment, RSI, relative volume, and funding; it can also consume archived open-interest history.
- Buys the five highest qualifying scores and shorts the five lowest; all three horizons must agree in direction.
- Applies 2x maximum gross leverage, equal notional allocation, 3% stops, 5% trailing exits, rank exits, and correlation caps.
- Includes configurable taker fees, slippage, and actual Binance funding-rate history downloaded beside each candle file.
- Produces the requested aggregate and monthly metrics, plus trade and equity-curve CSVs.

## Quick start

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

# Download 12 months of 1-hour klines for the eligible universe (can take time/API requests)
python -m crypto_momentum.cli download --months 12 --data-dir data

# Run the backtest from cached data
python -m crypto_momentum.cli backtest --data-dir data --output-dir results

# Walk-forward search over a bounded grid of high-impact risk/entry variables
python -m crypto_momentum.cli optimize --data-dir data --output-dir optimization --max-scenarios 144 --fixed-trade-notional 5000

# Run an hourly paper-trading poller (uses the latest completed candles)
python -m crypto_momentum.cli paper --state-file state/paper.json

# Check Binance USD(S)-M Futures testnet connectivity and calculate a $5,000 test order.
# This is dry-run and does not place an order.
python -m crypto_momentum.cli testnet-check --symbol BTCUSDT --position-notional 5000

# One stateful paper-portfolio cycle using the current liquid Futures testnet
# universe (up to 100 markets). This starts a $5,000 simulated account, so the
# selected 15% sizing produces initial $750 positions. It never submits orders.
python -m crypto_momentum.cli testnet-paper --starting-equity 5000

# Optional: validates an order with Binance's non-executing test-order endpoint.
# Set testnet-only credentials in the environment first; never put them in a file.
$env:BINANCE_TESTNET_API_KEY='...'
$env:BINANCE_TESTNET_API_SECRET='...'
python -m crypto_momentum.cli testnet-check --symbol BTCUSDT --position-notional 5000 --validate-order
```

The default universe is resolved at download time, then written to `data/universe.json` for reproducibility.  Backtests only use that saved universe and the cached candles.  To select a historical universe exactly as of each historical date, use a licensed historical market-metadata source and implement a `UniverseProvider` (survivorship bias cannot be fully removed from Binance's current ticker endpoint).

The default entry timing rule requires a directionally aligned 20-hour price breakout.  Set `require_breakout_entry=False` in `BacktestConfig` to compare the previous continuous-entry approach.  To use OI, provide `data/SYMBOL.open_interest.csv` files with `timestamp,open_interest`; public Binance OI history is not long enough for a full-year study.

`optimize` uses two rolling validation windows (40–60% and 60–80% of the sample) for parameter selection and evaluates the selected configuration only on the final 20%. It writes all training scenarios to `training_scenarios.csv` and the untouched out-of-sample result to `selection.json`. Alongside return and Sharpe, it scores Ulcer Index, drawdown duration, daily volatility, worst weekly loss, profitable-week share, and equity-trend R². The grid tests percentage and ATR stops, time stops, score thresholds, and portfolio stop-cluster cooldowns. `--fixed-trade-notional 5000` prevents compounding position size; it remains capped by the configured gross leverage. Do not select a strategy by its full-sample return.

## Key assumptions and limitations

- Signals are calculated at an hour's close and orders fill at the next hour's open. This avoids look-ahead bias.
- Stops/trailing levels are evaluated against next-bar OHLC. If both could trigger in the same bar, the adverse stop is used conservatively.
- The included live universe snapshot introduces survivorship bias for historical testing; it is disclosed rather than hidden.
- Binance public endpoints are subject to geographic/network availability and rate limits. Use a data vendor for institutional-quality historical coverage.
- This is research software, not investment advice. Paper trade before any live deployment.
- `testnet-check` uses the Binance USD(S)-M Futures **testnet** only. It is dry-run by default; even its optional validation call uses Binance's non-executing test-order route. The API adapter contains a separate testnet-only submission method for the later VPS phase, but the CLI intentionally does not expose live or testnet order submission yet.
- For VPS deployment, `strategy_v1.json` contains the selected model parameters. The live paper portfolio refreshes the current liquid, non-stable USDT perpetual universe daily (up to 100 contracts); historical data, research outputs, logs, and local state are intentionally excluded from Git.

## Structure

`crypto_momentum/config.py` contains all tunable parameters. Signal, risk, data, simulation, reporting, and paper execution modules are intentionally separated so features can be swapped independently.
