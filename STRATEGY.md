# Strategy guide

## Purpose

This is a long/short Binance USDT perpetual-futures momentum research system.
It ranks a liquid non-stablecoin universe, takes the strongest long and short
trends, and applies explicit risk rules. It is research and paper-trading
software, not a promise of live performance.

## Selected hourly model

| Component | Rule |
| --- | --- |
| Universe | Liquid USDT perpetuals; stablecoins and newly listed contracts excluded |
| Direction | 24-hour, 3-day and 7-day momentum must all agree |
| Ranking | Momentum return/consistency, MA trend, RSI, relative volume and funding; OI is optional |
| Entry | 36-hour breakout in the ranked direction; score at least 1.25 |
| Portfolio | At most two longs and two shorts; 0.70 correlation cap |
| Sizing | Account-relative; 15% maximum per position and 1.5x gross cap |
| Stop | 2% adverse move |
| Winner exit | Trail activates at +6%; exits on a 2% retracement |
| Other exits | Rank falls outside top/bottom 10, or 48-hour maximum hold |
| Costs | Taker fees, slippage and downloaded funding rates |

Signals are calculated only at a completed candle close and enter at the next
candle open. If a candle could touch both a stop and a trail, the adverse stop
is used first.

## `1hr` project

`C:\binance\1hr` is the original research and paper-trading project. One bar is
one hour: momentum lookbacks are 24/72/168 hours, breakout is 36 hours, and
maximum hold is 48 hours. The VPS paper portfolio uses this version.

## `15m` project

`C:\binance\15m` is a separate, deliberately more active strategy. It evaluates
the same momentum concept every 15 minutes, rather than holding hourly signals.
Time-equivalent lookbacks are scaled by four:

| Hourly rule | 15-minute equivalent |
| --- | --- |
| 24h / 3d / 7d momentum | 96 / 288 / 672 bars |
| 36-hour breakout | 144 bars |
| 48-hour maximum hold | 192 bars |
| 24-hour ATR/volume | 96 bars |

This is a distinct active strategy. Its historical result remains preliminary:
the current historical universe is selected from markets available today, so
survivorship bias remains until point-in-time market membership is added.

## Active 15-minute risk grid

`optimize-15m-risk` preserves the score, ranking, exits, leverage cap and
two-long/two-short portfolio. It varies only:

- risk at the initial stop: 0.10%, 0.20% or 0.30% of equity;
- same-symbol post-stop cooldown: none, one hour or four hours;
- breakout confirmation: immediate or two consecutive 15-minute candles;
- volatility throttle: normal size, or half size when 24-hour ATR exceeds 1.2%.

It is a bounded 36-scenario robustness test. Selection uses two validation
blocks; the final 20% of the sample is held out for the reported OOS result.

## Interpreting results

Do not choose a version on headline return alone. Compare Sharpe, maximum
drawdown, worst weekly loss, turnover, fees, profit factor and final OOS results.
Stress test costs and extreme 15-minute candles before any live deployment.
