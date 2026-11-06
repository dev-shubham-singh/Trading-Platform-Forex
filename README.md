# Heikin Ashi trading chart (MT5 / Exness + Fyers)

A TradingView-style chart that runs on your own PC, built for scalping XAU/USD on 15-second Heikin Ashi candles,
with custom strategy indicators and backtests. It **reads prices only and places no orders**.

## Two charts

| | Gold / forex | Indian market |
|---|---|---|
| Start | `start_gold_chart.bat` | `start_india_chart.bat` |
| Data | MetaTrader 5 (Exness), must be open and logged in | Fyers API (free plan), daily login |
| Server | `gold_chart.py` → http://127.0.0.1:8765 | `india_chart.py` → http://127.0.0.1:8766 |
| Symbols | everything in your MT5 terminal | all Fyers instruments (NSE/BSE stocks & F&O, MCX, currency), searched by name |

Both use the same page, `chart.html`. Restart the `.bat` after updating `chart.html` (the server reads it at start).

## Chart features
- Candle types: Heikin Ashi, normal candles, Renko, Kagi
- Intervals: seconds, minutes, hours, days, tick charts (e.g. `100t`), any custom value
- Zoom/pan, invert scale, drawing tools (trend line, horizontal line, box) with colours
- Time zone menu (bottom right, like TradingView): India time by default, plus UTC, broker time and major world cities; live clock
- Dark / light mode
- **New window + Sync**: open a second chart window (e.g. 5m) for another screen; drawings, crosshair, symbol and theme stay in sync (toggle)
- Indicators: EMA, SMMA, SMA, Bollinger Bands, pivot points (any period, R3/S3, history),
  candle high/low liquidity sweeps, previous day high/low/close, VWAP with deviation bands, ATR (own pane)
- Strategy indicators: Heikin Ashi BOS bias (two pullback cycles), EMA/SMMA stretch reversal / momentum / limit-breakout
- **Heikin Ashi switch boxes**: every colour flip (green after red / red after green) on any timeframes at once
  (5s–4h + custom), boxed over the chart candles inside it, last N switches per timeframe
- Gold/forex chart only: MACD pane with doji dots, tick volume, multi-timeframe **doji boxes** (1m–4h, Heikin Ashi or normal)
- Gold/forex chart only, order flow from MT5 ticks (buy/sell by the tick rule, like TradingView on CFD symbols):
  session / visible-range **volume profile** (POC, value area, VAH/VAL), **footprint** (sells x buys per price, imbalances,
  bar delta), **cumulative volume delta** / per-bar delta pane

## Indian chart (Fyers)
See `FYERS_SETUP.txt`. Copy `fyers_config.example.json` to `fyers_config.json` and put in your App ID and Secret
(this file is git-ignored). Minute charts and up have full history; second / tick / Renko / Kagi charts are built from
ticks the app records while it runs (`fyers_ticks/`).

## Backtest
`run_backtest.bat` runs `backtest.py` on recent MT5 ticks for the strategy set in `STRATEGY` and writes
`backtest_report.html` (per-trade drawdown, variants, threshold sweep) and `backtest_trades.csv`.

## Requirements
Windows, Python 3.10+. The `.bat` files install the libraries (`MetaTrader5`, `fyers-apiv3`, `pandas`, `numpy`).
The servers save lightweight-charts 4.2.0 next to the page on first start (fallback: unpkg), so the chart loads instantly and offline.
