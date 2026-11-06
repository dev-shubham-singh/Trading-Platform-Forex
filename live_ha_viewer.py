"""
Live XAU/USD viewer: MT5 ticks -> 15-second candles -> Heikin Ashi.

READS PRICES ONLY. THIS SCRIPT PLACES NO ORDERS.

Requirements:
  - Windows, with MetaTrader 5 open and logged in
  - pip install MetaTrader5 pandas matplotlib

Run:
  python live_ha_viewer.py
Close the chart window to stop.
"""

import sys
import time
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation

try:
    import MetaTrader5 as mt5
except ImportError:
    print("MetaTrader5 library not found. Run:  pip install MetaTrader5 pandas matplotlib")
    input("Press Enter to exit")
    sys.exit(1)

# ---------------- settings ----------------
SYMBOL = None           # e.g. "XAUUSDm" to force a symbol; None = auto-detect
CANDLE_SEC = 15         # candle size in seconds
HISTORY_MIN = 10        # minutes of ticks to keep
SHOW_CANDLES = 40       # candles shown on screen
TICK_WINDOW_SEC = 60    # seconds of raw ticks in the top panel
REFRESH_MS = 500        # how often the screen updates
UP_COLOR, DOWN_COLOR = "#26a69a", "#ef5350"
# ------------------------------------------

BUCKET_MS = CANDLE_SEC * 1000


def connect():
    if not mt5.initialize():
        print("Could not connect to MetaTrader 5:", mt5.last_error())
        print("Make sure MT5 is open and logged in, then run this again.")
        input("Press Enter to exit")
        sys.exit(1)
    acc = mt5.account_info()
    if acc:
        print(f"Connected to MT5: account {acc.login} on {acc.server}")


def find_symbol():
    if SYMBOL:
        names = [SYMBOL]
    else:
        syms = mt5.symbols_get("*XAUUSD*") or []
        names = sorted((s.name for s in syms), key=len)  # shortest name first
    for name in names:
        if not mt5.symbol_select(name, True):
            continue
        for _ in range(20):  # wait up to ~5 s for a first tick
            tick = mt5.symbol_info_tick(name)
            if tick and tick.time_msc:
                return name
            time.sleep(0.25)
    print("Couldn't find a live XAUUSD symbol.")
    print("Gold symbols found:", names or "none")
    print("Open Market Watch in MT5, add gold, or set SYMBOL at the top of this file.")
    input("Press Enter to exit")
    sys.exit(1)


def fetch_ticks_since(symbol, from_ms):
    # MT5 tick times are broker-server time; we pass that same clock back.
    start = datetime.fromtimestamp(from_ms / 1000, tz=timezone.utc)
    raw = mt5.copy_ticks_from(symbol, start, 200000, mt5.COPY_TICKS_INFO)
    if raw is None or len(raw) == 0:
        return pd.DataFrame(columns=["time_msc", "bid", "ask"])
    df = pd.DataFrame(raw)[["time_msc", "bid", "ask"]]
    return df[df["bid"] > 0]


def build_candles(ticks):
    bucket = (ticks["time_msc"] // BUCKET_MS) * BUCKET_MS
    g = ticks.groupby(bucket)["bid"]
    return pd.DataFrame({"open": g.first(), "high": g.max(),
                         "low": g.min(), "close": g.last()})


def heikin_ashi(c):
    o, h, l, cl = (c[k].to_numpy() for k in ("open", "high", "low", "close"))
    ha_close = (o + h + l + cl) / 4
    ha_open = np.empty(len(c))
    ha_open[0] = (o[0] + cl[0]) / 2
    for i in range(1, len(c)):
        ha_open[i] = (ha_open[i - 1] + ha_close[i - 1]) / 2
    return pd.DataFrame({
        "open": ha_open,
        "high": np.maximum.reduce([h, ha_open, ha_close]),
        "low": np.minimum.reduce([l, ha_open, ha_close]),
        "close": ha_close,
    }, index=c.index)


def draw_candles(ax, df, title):
    ax.clear()
    d = df.tail(SHOW_CANDLES)
    if d.empty:
        return
    x = np.arange(len(d))
    colors = np.where(d["close"] >= d["open"], UP_COLOR, DOWN_COLOR)
    ax.vlines(x, d["low"], d["high"], color=colors, linewidth=1)
    bottom = np.minimum(d["open"], d["close"])
    height = np.maximum((d["close"] - d["open"]).abs(), 0.01)
    ax.bar(x, height, bottom=bottom, color=colors, width=0.6)
    step = max(1, len(d) // 8)
    labels = pd.to_datetime(d.index, unit="ms").strftime("%H:%M:%S")
    ax.set_xticks(x[::step])
    ax.set_xticklabels(labels[::step], fontsize=8)
    ax.set_title(title, fontsize=10, loc="left")
    ax.grid(alpha=0.2)
    ax.yaxis.tick_right()


def main():
    connect()
    symbol = find_symbol()
    print(f"Streaming {symbol}. Close the chart window to stop.")

    latest = mt5.symbol_info_tick(symbol).time_msc
    clock_offset = latest - time.time() * 1000  # server clock vs this PC
    state = {
        "ticks": fetch_ticks_since(symbol, latest - HISTORY_MIN * 60_000),
        "last_ms": latest,
        "warned": False,
    }

    fig, (ax1, ax2, ax3) = plt.subplots(
        3, 1, figsize=(11, 9), gridspec_kw={"height_ratios": [1, 1.4, 1.4]})
    fig.canvas.manager.set_window_title(f"{symbol}: live ticks -> 15s -> Heikin Ashi")

    def update(_):
        new = fetch_ticks_since(symbol, state["last_ms"])
        if new.empty and mt5.last_error()[0] != 1 and not state["warned"]:
            print("MT5 returned no data:", mt5.last_error())
            state["warned"] = True
        ticks = pd.concat([state["ticks"], new]).drop_duplicates()
        if ticks.empty:
            return
        newest = int(ticks["time_msc"].max())
        ticks = ticks[ticks["time_msc"] >= newest - HISTORY_MIN * 60_000]
        state["ticks"], state["last_ms"] = ticks, newest

        t0 = time.perf_counter()
        candles = build_candles(ticks)
        ha = heikin_ashi(candles)
        conv_ms = (time.perf_counter() - t0) * 1000

        # top panel: raw ticks over the last minute
        ax1.clear()
        recent = ticks[ticks["time_msc"] >= newest - TICK_WINDOW_SEC * 1000]
        secs_ago = (recent["time_msc"] - newest) / 1000
        ax1.plot(secs_ago, recent["bid"], marker=".", markersize=3, linewidth=0.8)
        first_edge = ((newest - TICK_WINDOW_SEC * 1000) // BUCKET_MS + 1) * BUCKET_MS
        for edge in range(int(first_edge), newest + 1, BUCKET_MS):
            ax1.axvline((edge - newest) / 1000, linestyle="--", alpha=0.3)
        ax1.set_xlim(-TICK_WINDOW_SEC, 1)
        ax1.set_title(f"1. Raw ticks, last {TICK_WINDOW_SEC}s (dashed = 15s boundaries)",
                      fontsize=10, loc="left")
        ax1.grid(alpha=0.2)
        ax1.yaxis.tick_right()

        draw_candles(ax2, candles, "2. Regular 15-second candles (bid, server time)")
        draw_candles(ax3, ha, "3. Heikin Ashi 15-second candles")

        now_server = time.time() * 1000 + clock_offset
        closes_in = (BUCKET_MS - (now_server % BUCKET_MS)) / 1000
        last = ticks.iloc[-1]
        fig.suptitle(
            f"{symbol}  bid {last['bid']:.2f}  ask {last['ask']:.2f}  "
            f"spread {last['ask'] - last['bid']:.2f}  |  ticks: {len(ticks):,}  |  "
            f"candle closes in ~{closes_in:.1f}s  |  conversion: {conv_ms:.2f} ms",
            fontsize=10)
        fig.tight_layout()

    anim = FuncAnimation(fig, update, interval=REFRESH_MS, cache_frame_data=False)
    try:
        plt.show()
    finally:
        mt5.shutdown()
        print("Disconnected from MT5.")
    return anim


if __name__ == "__main__":
    main()
