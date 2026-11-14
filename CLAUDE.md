# Project notes for Claude (read this first in a new chat)

Owner: Shubham (India, Asia/Kolkata). Trades XAU/USD on **Exness MT5** (use Exness only; do not repeat the RBI warning) and
Indian markets through **Fyers** (data-only app, no order placement). Repo: `dev-shubham-singh/Trading-Platform-Forex`, branch `main`.
The user's PC folder is `C:\Users\shubh\Documents\gold_viewer` (the same files as this repo).

## Files
- `chart.html`  - ONE shared single-page chart UI (vanilla JS, lightweight-charts v4.2.0, overlay canvas). Used by BOTH charts.
  `SRC` is 'MT5' on the gold chart and 'Fyers' on the India chart; a few things branch on it (`paperOn()`, `practice()`).
- `gold_chart.py`  - MT5 server, port 8765 (needs MetaTrader5 lib + a logged-in terminal). Orders, account, history, tick order flow.
- `india_chart.py` - Fyers server, port 8766. Candles, live feed, recorded ticks, order flow by tick rule, symbol lists
  (`/api/list`: fno, nifty50/100/500, gainers), no order endpoints.
- `backtest.py`, `start_gold_chart.bat`, `start_india_chart.bat`, `FYERS_SETUP.txt`, `README.md` (feature list - keep it updated).
- Never commit: `fyers_config.json`, `fyers_token.json`, `fyers_ticks/`, `fyers_symbols/`, `orders.log`. Never ask the user to paste keys in chat.

## Features already built (do not rebuild)
Indicators with eye / gear / remove icons and multi-timeframe option; higher-timeframe candles (`htf`); MACD, Volume, Doji boxes,
Volume profile, Footprint, CVD; bar replay with practice trading; Trades toolbar (positions, history, growth, commission);
Buy/Sell with risk sizing and SL/TP lines. On the India chart Buy/Sell is a saved **paper account** (`PA`). Templates, per-timeframe indicators, SMMA candle colours, Burke setups at pivots (`burke`). Favorite symbols (star,
Favorites tab). India symbol tabs for F&O / Nifty 50 / 100 / 500 / 7-day gainers.
Not built yet (user may ask): Wyckoff indicator (plan was discussed, needs their go-ahead), automatic strategy backtest with their rules.

## How to work on it
- Edit `chart.html` / the `.py` files in the repo, test, commit, push to `main`.
- No MT5 or Fyers in the cloud. Test the UI with Playwright (Chromium) against stub servers: a fake `MetaTrader5` module for
  `gold_chart.py`, a fake `fyers_apiv3` for `india_chart.py`, and a mock lightweight-charts file (the real library is not
  downloadable in the sandbox). Compare indicator values against pandas calculations. Say plainly what could not be tested for real.
- Deploying to the PC: if the `mcp__remote-devices__` tools are present, copy the changed files to `/mnt/user-data/outputs/`
  and call `device_commit_files` to `C:\\Users\\shubh\\Documents\\gold_viewer\\...`. ALWAYS verify afterwards with
  `device_bash` (`md5sum`, grep for something new) - a "written" reply once did not actually update the files.
  If the PC is not linked, tell the user to `git pull` / download the ZIP from GitHub.
- After a server (`.py`) change the user must close and restart the matching `.bat`; after `chart.html` only, Ctrl+F5.
- Windows launcher: `start_india_chart.bat` prefers Python 3.12/3.13/3.11 and sets `*_NO_EXTENSIONS` so aiohttp installs without a C++ compiler.
- Commit messages end with the Co-Authored-By trailer required by the session's attribution reminder.

## Safety of the gold chart server (keep)
POSTs need the right Host/Origin/JSON content type/`X-Chart-Token`; `RISK_TOLERANCE`; `SENT_IDS` dedupe; orders logged in `orders.log`.
