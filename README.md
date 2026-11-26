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
- **Templates transfer**: Templates menu > Copy all templates, then on the other chart (gold or India) Paste templates; same-name templates ask before replacing
- EMA / SMMA (and SMA, BB, MACD, ATR) **Timeframe** list now starts at 1s; on a tick chart (e.g. 100t) any time frame works, on a time chart it must be longer than the chart interval
- **Clear** button (next to Indicators): deletes every indicator in one click; pick a template to bring them back
- Indicator **Timeframe shorter than the chart** (e.g. a 1s SMMA/EMA on a 15s chart): the short candles load in the background, the indicator is calculated on them and each chart candle shows the value at its close
- **Scan** button: checks every symbol of Favorites / Market Watch / an India list on any time frame (1s ... 1d) for price below / above an SMMA of your length for at least N candles in a row (Heikin Ashi or normal, close or whole candle); saves each run as a new list named `date time · timeframe` under Symbols > Scans
- **Push count (HH / HL)** indicator + scanner: bias from the last N higher-timeframe candle colours (e.g. 2 green 15m candles), then on the lower chart price dips below an SMMA (own length / timeframe), reclaims it, breaks the top of the dip (HH1) and each later higher high after a higher low (HH2, HH3 ...); mirror for sells (LL1, LL2...). Default mode is Candle swing: the swing is the last red (sell) / green (buy) candle of the chosen timeframe (HA or normal) before the retrace; its break = LL1 / HH1, the next one = 2 ...; a candle touching the SMMA restarts the count; swing lines are drawn (Pivot swing = the older mode). Each push also draws a buy / sell rectangle zone (the pullback between the broken high and the higher low; from-push / to-push / extend are settings); bias candles 0 = no bias filter. Every parameter is editable; Scan > Push count scans a whole list with the same settings (`pushCalc` is shared by the chart and the scanner)
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

## Favorite symbols
Click the star next to the symbol name to add or remove it from your favorites. In the symbol search, the **Favorites** tab
lists them, favorites are also pinned at the top of the full list, and every row has its own star. Saved in the browser,
separately for the gold and the Indian chart.

## Indian chart (Fyers)
See `FYERS_SETUP.txt`. Copy `fyers_config.example.json` to `fyers_config.json` and put in your App ID and Secret
(this file is git-ignored). Minute charts and up have full history; second / tick / Renko / Kagi charts are built from
ticks the app records while it runs (`fyers_ticks/`).

The Fyers chart has the same screen as the gold chart: every indicator (MACD, Volume, Doji boxes, Volume profile,
Footprint, CVD, higher-timeframe candles, multi-timeframe lines...), the legend eye / gear icons, bar replay and the
Trades bar. Differences: Fyers sends no buy / sell flag, so footprint, delta and volume profile use the tick rule
(an up-tick counts as a buy) on the ticks the app has recorded; the Volume bars are Fyers' traded volume on minute
charts and up (tick counts on seconds / tick charts). The Fyers app is data-only and cannot place orders, so Buy / Sell
there trades a **paper account** (starts at 100,000 INR, saved in the browser, "New paper account" resets it): lot
sizing, SL / TP lines, drag to move, commission, history and growth all work, nothing is sent to Fyers.

On the Indian chart the symbol search has extra tabs: **Options (F&O)** (everything that has options, from the Fyers
symbol list), **Nifty 50 / 100 / 500** (the NSE constituent lists, downloaded once and kept for a week in `fyers_symbols/`)
and **Top gainers 7d** (the biggest 7-day gains among F&O stocks and the Nifty 500, worked out from Fyers daily candles;
it takes a few minutes the first time and is refreshed hourly).

## Burke setups at pivots
**Indicators > Burke setups at pivots** marks Stacey Burke style reversal setups, but only when the peak forms at a pivot
level (P, R1, S1, R2, S2...) of the pivot period you choose (default 15m), never in the middle between levels:
- **M / W**: two pushes into the level where the second fails. M = sell, W = buy.
- **3 pushes**: three pushes into the level, then the reversal.
- **First red / first green day**: the first red day after green days is marked as a sell day, the first green day after
  red days as a buy day. Signals that agree with the day get a star; "Only with day bias" hides the rest.

The arrow is drawn on the candle that closes through the neckline (the pullback between the pushes), after the last push
is confirmed, so a signal never moves or disappears afterwards. **Zone** sets how close to the level the peak must be,
**Swing** how big the pushes are. It marks setups; it does not place trades. Uses real candles on any chart type.

## SMMA candle colours
In the SMMA settings (gear) tick **Colour candles** (and untick **Show line** to see only the colours): every candle whose close is above the SMMA turns green (body and wick),
every candle below it turns red; the two colours can be changed. The SMMA has its own **Timeframe**, so a 50-period SMMA of
5m colours the candles of a 15 second chart. Works on Heikin Ashi and normal candle charts (not on line / Kagi or volume candles).

## MACD doji dots: Heikin Ashi / normal / off
The MACD row in the legend has a small **doji dots** button. Click it to cycle Heikin Ashi, normal candles and off. The
Heikin Ashi doji is always worked out from the real candles, so you can see Heikin Ashi dojis on the MACD while the chart
shows normal candles, and normal-candle dojis while the chart shows Heikin Ashi. Same choice is in the MACD settings (gear).

## Keep indicators on every timeframe
The checkbox **Keep indicators on every timeframe** (top bar) decides what happens when you change the timeframe.
**On** (default): your indicators stay, as before. **Off**: every timeframe has its own indicators. A timeframe you have
not used yet starts clean (drawings such as rectangles and trend lines stay), you add what you want there, and when you
go back to 15s you see your 15s indicators and on 15m your 15m ones. Turning it off makes the indicators on screen belong
to the timeframe you are on. Saved separately for the gold and the Indian chart.

## Templates
The **Templates** button (next to Indicators) saves the current indicators with all their settings (pivots, EMA, MACD...),
and optionally the chart type and the timeframe, under a name. Click a saved template to switch the chart to it in one
click, e.g. "1D pivots" (1d + pivots) and "1H trend" (1h + EMAs). **Update** replaces a template with what is on the chart now.
Saved in the browser, separately for the gold and the Indian chart.

Both charts open in **Google Chrome** when you start them (if Chrome is not installed, your normal default browser is used).

## Troubleshooting
| Problem | Fix |
|---|---|
| Gold chart says it cannot connect to MT5 | Open MetaTrader 5 first, log in to the Exness account, and turn on **Tools > Options > Expert Advisors > Allow algorithmic trading**. Then restart `start_gold_chart.bat`. |
| Buy / Sell does nothing on the gold chart | Check the green "live" switch in the Trades bar and that the MT5 terminal shows no "trading disabled" message. |
| India chart shows a Fyers login page | Normal once a day: Fyers logins expire at 6:30 AM India time. Click the login link and sign in. |
| `aiohttp` / "Microsoft Visual C++ 14.0" error on install | You are on a very new Python. The launcher picks Python 3.12 / 3.13 / 3.11 if installed; install one of those from python.org (tick **Add Python to PATH**). |
| New features are missing after an update | Press **Ctrl+F5** in the browser. If the update touched a `.py` file, close the black window and start the `.bat` again. |
| India chart: Nifty 100 / 500 tab says it could not download | NSE blocked the download. Save the CSV from niftyindices.com into `fyers_symbols/` as `idx_ind_nifty500list.csv` (same pattern for the others). |
| Second / tick charts on the India chart are short | Fyers gives no seconds history on the Standard plan; those charts use ticks recorded while the app runs, so keep it open. |
| Port already in use | A previous black window is still open. Close it and start again. |

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

- **Multi-timeframe indicators.** EMA, SMMA, SMA, Bollinger Bands, MACD and ATR have a *Timeframe* setting (Chart, or 5s ... 1d). The indicator is calculated on candles of that timeframe (Heikin Ashi or normal like the chart) and shown on the chart you are looking at, e.g. a 1m SMMA 50 on the 15s chart. By default every chart candle shows the value of the last *closed* higher candle (no repaint, no look-ahead); tick *Live* to also follow the forming candle. The timeframe must be longer than the chart interval, and it can only use the candles your chart has loaded.
- **Settings on the chart.** Each legend line has an eye (hide / show), a gear (opens that indicator's settings in a small window, applied live, Esc closes it) and a cross (remove). No need to open the Indicators panel.

## Second-chart history limit

Second-based charts (5s, 15s...) build up tens of thousands of candles, which uses a lot of memory and slows the chart. A small dropdown next to the timeframes (only shown on second charts) keeps just the newest part: **Last 8 hours / Last 12 hours (default) / Last 1 day / All loaded**. Pick *All loaded* to get as much as possible. Your choice is remembered. Needs a restart of the `.bat` once (server change) plus Ctrl+F5.

## Limit orders and the Risk / Reward drawing

- **Limit orders**: in the Buy / Sell panel pick *Order type: Limit*, then type the limit price or drag the blue line. Lot size comes from your risk (amount or %) and the distance to the stop, with commission included, exactly like a market order. A buy limit must be below the price, a sell limit above. On the gold chart it places a real pending order in MT5 (needs *Live orders on*); on the Fyers chart and in bar replay it is a practice order that fills when price reaches it. Pending orders show as blue dashed lines with a cancel (x) button.
- **Risk / Reward drawing**: two icons under the box tool (long, short). Click where you want to enter; drag the green line (target), red line (stop), the right edge (width) or the whole box. It shows pips and points, the lot size for your risk, the money lost at the stop and gained at the target (commission included), the % move and R:R, and recalculates while you drag. Default risk is 10 (1000 on the Indian chart); change it, or switch to % of balance, in the panel that opens when it is selected. The same panel has *Place limit order* and *At market*.
- Pips: 1 pip = 10 points (gold 0.10). Points are shown next to it.

- **Pending button** (blue, next to Buy / Sell): pick Buy or Sell and an entry price. The chart chooses the type itself: a buy below the price is a BUY LIMIT, above it a BUY STOP (breakout); a sell above the price is a SELL LIMIT, below it a SELL STOP. Buy / Sell stay market orders. The Risk / Reward drawing's *Place pending order* button does the same.

## Flip SL (automatic stop and target after the fill)

The orange **Flip SL: ON / off** button (next to Sell) turns it on; the arrow beside it opens the settings. When any order fills (market, limit, stop or drawing order) the stop loss jumps to the extreme of the **last candle of the opposite colour**: for a SELL the high of the last green candle, for a BUY the low of the last red candle. Colours come from an SMMA (the one that paints your candles, or one you pick). The take profit is then reset to 1R / 1.2R / 1.5R / 2R / 3R / custom of the new stop distance (or kept / removed). Options: use Heikin Ashi or real candle highs and lows, a buffer in points, a max-loss limit (default 20: the stop is left alone if the new risk is bigger), and "never wider than my original stop". Positions that were already open are never touched. On the gold chart the account is checked every 0.4 s after you send an order so the change happens within about a second; on the practice account / replay it is instant.

## Getting the project on a new computer

`git clone` and run `start_gold_chart.bat` / `start_india_chart.bat`. `lightweight-charts.js` (TradingView Lightweight Charts v4.2.0, Apache-2.0) is included so the charts work offline. Your own `fyers_config.json` / `fyers_token.json` are never stored in the repo: copy `fyers_config.example.json` and fill it in.

## Partial profit + Secure (one button)

The green **Partial + Secure: ON / off** button (next to Flip SL; the arrow opens the settings and shows the maths in R and money). When a trade fills, it watches the price. 1R = the money you risk = entry to stop (after Flip SL has moved the stop, if that is on). At **X R** (default 0.5) it closes **P %** of the lots (default 25 %); at **Y R** (default 0.7) it moves the stop to **Z R above entry** (default 0.1) so the rest of the trade pays at least the commission. All four numbers and the final target (default 1.75 R, used for the maths) can be changed. Example with 25 % at 0.5R, stop to +0.1R at 0.7R, target 1.75R: target hit = 0.25x0.5 + 0.75x1.75 = 1.4375R; falls back after 0.7R = 0.25x0.5 + 0.75x0.1 = +0.2R; falls back after 0.5R but before 0.7R = 0.25x0.5 - 0.75x1 = -0.625R. Works on the live MT5 account (needs the server restart: partial close), practice and replay.

## Hide candles

The **Hide candles / Show candles** button (next to Dark / Light) hides the candles so only indicators, drawings, orders and levels show. The price scale stays the same and your choice is remembered.

## Profit lock

Toolbar button **Profit lock** (arrow = settings). When a trade's profit reaches the *trigger* amount, its stop loss is raised to lock in the *minimum profit*; a stop of yours that is already better is left alone. On a live MT5 account it runs inside `gold_chart.py` (checks 10x a second, works with the chart closed; settings in `profit_lock.json`, not committed). Practice / paper trades are handled by the page. Restart `gold_chart.py` after updating.

## Demo / real account switch

Toolbar button **DEMO 12345 / REAL 12345** (top row, next to Light / Dark). It shows which MT5 account is live; click it to switch. No password is typed or stored: MT5 logs in with the password saved in the terminal (File > Login to Trade Account > Save password). Accounts you log into in MT5 appear automatically; you can also add one by login number and server. Switching to a REAL account needs a second confirming click. The list is kept in `mt5_accounts.json` (not committed). Restart `gold_chart.py` after updating.

## Template groups

Templates can be put in groups (e.g. "15s execution" with its higher and lower template): type a group when saving, or use a template's **Group** button; click a group header to fold it, **Rename** / **Copy group** on the header. **Copy** on a single template (or a group) copies just that for the other chart (gold <-> India): Templates > Paste templates there. Groups travel with the templates.

## Pivot-zone scan

Scan > *Scan for* > **Price near a current pivot level**. Pick the pivot period (15m ... 360d), the type and *More than (%)* / *Less than (%)* (e.g. 0 and 15 = close to a level, 15 and 85 = away from both levels). A symbol is listed when its last close is between those two percents away from ANY current level (P, R1-R8, S1-S8), above or below it. The percent is of the distance between that level and the next level on the same side. Works on every list the scanner already has. Tick **AND also check another pivot period** to add a second condition (for example 1d and 1h): a symbol is listed only when both are true.

## Quick template bar

When you have 2 or more templates, a small bar at the bottom-left of the chart shows the buttons of one group (for example *Bias* and *Exec*). One click loads that template. The group button (yellow) shows the next group, and the arrow loads the next template of the group. The loaded template is highlighted.

## Watchlists from scan results

Symbols > Scans: every result has a round **+** icon (and each scan has **+ all**). It opens a small menu: pick a watchlist or make a new one (e.g. "Tomorrow"). A tick shows the symbol is already in a list; click the list again to take it out. Symbols > **Watchlists** shows your lists (rename, empty, delete, remove single symbols; click a symbol to open it). Lists stay saved in the browser for the next day and can be chosen as the source of a new scan.

## Sessions (gold chart only)

Indicators > **Sessions (high / low)**. Thin lines for the high and low of the Asia, London and New York sessions. Hours are in UTC and editable (default Asia 00:00-09:00, London 07:00-16:00, New York 12:00-21:00; IST = UTC + 5:30). *Show last N sessions* sets how many sessions of each kind are drawn. *Shade %* fills the box between low and high: 0 = only lines (cleanest), 100 = dark. *Lines run*: only during the session, until the next same session, or to the right edge. Names / prices and dotted open lines can be switched off or on. Use a chart of 1h or shorter.

## History window per timeframe

The *Last N hours* box on second charts is remembered per timeframe, so two windows no longer overwrite each other. Defaults: 1s loads 2 hours, 15s loads 8 hours; any other second timeframe keeps the old 12 hours until you change it.

## Telegram alerts (both charts)

Toolbar > **Alerts**. Connect once: in Telegram talk to **@BotFather**, send /newbot, paste the token into the Alerts window and press Connect, then open your bot and press Start. The token is saved on your PC only, in `telegram_config.json` (never committed). Alerts sent: price levels crossed (right-click anywhere on the chart (or the price scale) > *Add alert at ...*, like TradingView; drag the yellow line to move it, right-click near it > *Remove alert*; or use the Alerts window; each one fires once), new push-count breaks (from push number N), and scan results when a scan finishes. Every chart alert carries a screenshot of the chart with a title strip. The chart page has to stay open. Needs `tg_alerts.py` next to the server; restart the matching .bat after updating.
