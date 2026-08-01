# KuCoin Futures research variant

The KuCoin projects use the same selected Binance momentum rules to test whether
the signal survives on a different perpetual-futures venue.

## Universe and data

- USDT-settled, linear, open KuCoin Futures contracts only.
- Stablecoin bases, contracts younger than the requested history, and markets
  below the configured 24-hour turnover threshold are excluded.
- Public KuCoin Futures candles and public funding history are saved beside each
  other in the local `data/` folder. They are never committed to Git.
- KuCoin symbols differ from Binance symbols: for example, Bitcoin is
  `XBTUSDTM` rather than `BTCUSDT`.

## Commands

Hourly project (`C:\Momentum\kucoin\1hr`):

```powershell
python -m crypto_momentum.cli kucoin-download --months 12 --interval 1h --data-dir data
python -m crypto_momentum.cli backtest-winning --data-dir data --output-dir results_kucoin_1hr --bar-minutes 60
```

Active 15-minute project (`C:\Momentum\kucoin\15m`):

```powershell
python -m crypto_momentum.cli kucoin-download --months 12 --interval 15m --data-dir data
python -m crypto_momentum.cli backtest-winning --data-dir data --output-dir results_kucoin_15m --bar-minutes 15
```

The 15-minute command evaluates the active strategy every 15 minutes and scales
the original horizons, breakout and maximum-hold windows by four. It is not an
hourly strategy with more detailed stops.

## Important comparability limits

Exchange liquidity, listing dates, fee schedules, contract multipliers,
funding rules and candle coverage differ from Binance. Results are a venue
robustness check, not proof that performance will match Binance. KuCoin’s
current contract list also creates survivorship bias until point-in-time
historical market membership is supplied.
