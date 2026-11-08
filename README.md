# Heikin Ashi trading chart (MT5 / Exness + Fyers)

A TradingView-style chart that runs on your own PC, built for scalping XAU/USD on 15-second Heikin Ashi candles,
with custom strategy indicators and backtests. It reads prices, and places an order **only** when you press Confirm
in the Buy / Sell panel of the gold/forex chart (set `ALLOW_TRADING = False` in `gold_chart.py` to turn that off).

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
- **Volume candles** (tick box next to the chart type): Heikin Ashi or normal candles whose width shows volume;
  **Line** chart type (traditional close line)
- **Sideways / chop filter**: tick any timeframes; each is judged on its own (closed) candles by EMA 9 / EMA 15 vs
  SMMA 50 gap in ATR (default), Choppiness Index, ADX, or a 2-of-3 vote. A strip per timeframe (green up, red down,
  grey sideways), grey shading when sideways, a warning in the Buy / Sell panel and an optional trade block.
- **Heikin Ashi switch boxes**: every colour flip (green after red / red after green) on any timeframes at once
  (5s–4h + custom), boxed over the chart candles inside it, last N switches per timeframe
- Gold/forex chart only: MACD pane with doji dots, tick volume, multi-timeframe **doji boxes** (1m–4h, Heikin Ashi or normal)
- Gold/forex chart only, order flow from MT5 ticks (buy/sell by the tick rule, like TradingView on CFD symbols):
  session / visible-range **volume profile** (POC, value area, VAH/VAL), **footprint** (sells x buys per price, imbalances,
  bar delta), **cumulative volume delta** / per-bar delta pane

## Buy / Sell with risk-based lot size (gold/forex chart)
- **Buy** / **Sell** (top bar) opens a plan; nothing is sent until **Confirm**, and only with "Live orders on" ticked.
- Risk per trade: % of balance or a fixed amount (account currency).
- **Auto lot**: the stop loss starts just beyond the last N candles' low (buy) / high (sell); drag the red SL line to
  move it. Lot = risk / loss per lot at that SL, rounded down to the lot step.
- **Fixed lot**: the SL is where your lot loses exactly your risk, so it moves with price. The **SL candle** (orange box)
  is the most recent candle whose real low (buy) / high (sell) reached that level.
- Optional take profit at N x risk. Open positions on the symbol show as dotted lines with live P/L.
- **Commission** is part of the risk: round-trip commission per lot (detected from your last closed trade on the symbol,
  or typed in the panel) is added to the loss at SL, so lot size / fixed-lot SL keep the total loss within your risk,
  and the take profit is placed so the profit *after* commission is N x the total loss. The server's risk check includes it.
- The server re-checks lot limits, SL side and risk on fresh prices (refuses if the loss at SL grew over 1.25x),
  accepts orders only from the chart page itself, and logs every order to `orders.log`.
- MT5 must have **Algo Trading** switched on (toolbar button), or MT5 rejects the order.

### Trades panel (bottom, toggle with "Trades" in the footer)
- **Positions**: every open position (all symbols) with live price and P/L, and a Close button (one click closes).
  "Close all" closes everything (click twice). Positions also show on the chart with live P/L and an x (one click closes).
- Top line: balance, live equity, open P/L, today's P/L and account growth since your deposits.
- **Drag SL / TP** of an open trade on the chart: the line shows the money (and % of balance) you would lose or lock in
  at that level while you drag; releasing sends it to MT5. No TP or SL yet? Drag one out of the trade's entry line.
  Esc cancels a drag; a level on the wrong side of the price is not sent.
- **History & growth**: closed trades with profit, deposits, win rate and a balance curve ending at live equity.
- Requotes are retried automatically on a fresh price (allowed slippage = 1x the spread); the same Confirm can never
  send two orders.

## Bar replay (practice)
- **Replay** (top bar) -> click the candle to start from. The chart shows only candles that had closed by the replay
  time, on any timeframe or candle type (switch freely; it stays at the same moment, nothing from the future).
- Play / pause, next candle (or the Right arrow key), speed 1-25 candles per second, pick another start.
- Gold/forex chart: Buy / Sell, SL / TP dragging and the x close all work as **practice trades** at the replay price
  (spread included); SL / TP are checked candle by candle (SL first if both are inside one candle). The trades panel
  shows the practice account (balance you choose, history, win rate, growth curve). Nothing is sent to MT5.
- Exit replay returns to the live chart and your real account.

## Indian chart (Fyers)
See `FYERS_SETUP.txt`. Copy `fyers_config.example.json` to `fyers_config.json` and put in your App ID and Secret
(this file is git-ignored). Minute charts and up have full history; second / tick / Renko / Kagi charts are built from
ticks the app records while it runs (`fyers_ticks/`).

## Backtest
`run_backtest.bat` runs `backtest.py` on recent MT5 ticks for the strategy set in `STRATEGY` and writes
`backtest_report.html` (per-trade drawdown, variants, threshold sweep) and `backtest_trades.csv`.

## Requirements
Windows, Python 3.10+. All libraries are listed in `requirements.txt` (`MetaTrader5`, `fyers-apiv3`, `pandas`, `numpy`);
the `.bat` files also install what they need on first run.

### Setting up on a new PC
1. Install Python 3 from https://www.python.org/downloads/ (tick **Add Python to PATH**).
2. Download this repo: **Code → Download ZIP** on GitHub and unzip it, or `git clone https://github.com/dev-shubham-singh/Trading-Platform-Forex.git`.
3. In that folder, open a command prompt and run `py -m pip install -r requirements.txt`.
4. Gold / forex: install MetaTrader 5, log in to Exness, then run `start_gold_chart.bat`.
5. Indian market: copy `fyers_config.example.json` to `fyers_config.json`, fill in your Fyers App ID and Secret
   (these are not on GitHub, on purpose), then run `start_india_chart.bat` and log in.

Not on GitHub (they stay on the old PC): `fyers_config.json`, your recorded Fyers ticks (`fyers_ticks/`), and the chart's
saved drawings / indicators / settings, which live in the browser. Copy `fyers_ticks/` across if you want the old tick history.
The servers save lightweight-charts 4.2.0 next to the page on first start (fallback: unpkg), so the chart loads instantly and offline.

- **Eye icon on every indicator.** In the legend at the top left of the chart each indicator has an eye. Click it to hide or show that indicator (like TradingView). Hidden ones stay listed, struck through, so you can switch them back on without opening the Indicators panel.

- **Higher timeframe candles** (Indicators > Higher timeframe candles). Draws a higher-timeframe candle as boxes over the chart candles it covers: a light box from its high to its low and a stronger box inside from its open to its close (green up, red down). Choose a fixed length (any of 30s, 5m, 1h, 4h, 1d, 1w, 4w, 30d ...) or "Chart x N", which follows the chart interval (on a 15s chart x2 is 30s, on a 1h chart x4 is 4h). Candles can be Heikin Ashi or normal. "Last" limits how many higher candles are drawn (blank = all loaded history). The newest one is dashed while it is still forming and follows every tick. The legend shows the O/H/L/C of the higher candle under the crosshair.

- **Commission in the trades panel.** A *Commission* box (per 1.00 lot, round trip) sits in the toolbar of the Trades panel and is the same setting as in the Buy/Sell panel; *auto* goes back to the value read from your last closed trade. Open positions show *Profit*, *Commission* and *Net*; the chart tag shows the net; the summary shows *After commission*. History shows *Profit*, *Commission* and *Net* per trade, and the growth line shows the total commission paid (and today's). A trade counts as won only if it is positive after both commissions. Restart `gold_chart.py` after updating, so open positions report the commission MT5 has already charged.
