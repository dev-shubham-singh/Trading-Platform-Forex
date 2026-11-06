"""
Backtest on 15-second Heikin Ashi candles, XAU/USD, from MT5 tick history.
Strategies (STRATEGY setting): "momentum" (Stretch momentum - default), "stretch" (Stretch reversal,
direct entry, mitigation target) and "bos" (HA BOS bias).

READS PRICE HISTORY ONLY. THIS SCRIPT PLACES NO ORDERS.

Stretch reversal (same logic as the chart indicator):
  - Stretch: EMA 9 and EMA 15 both >= STRETCH_ATR x ATR(14) away from SMMA 50 (on HA closes),
    at some candle within the last STRETCH_LOOKBACK candles.
  - Signal: retracement BOS - a pullback cycle, trend colour returns, then a HA close beyond the pullback.
  - Target: scanning left from before the pullback, the first red (sell) / green (buy) HA candle whose
            low / high is beyond the signal close - the mitigation point.
Stretch limit + breakout (default):
  - Trigger candle found as in Stretch momentum. Then two orders, whichever fills first cancels the other:
    a limit 20% of the trigger candle's length back from its high (buy) / low (sell), valid 1 candle,
    and a stop order 20% beyond it, valid 2 candles. Neither fills -> setup void.
  - Stop: trigger candle low (buy) / high (sell). Target 1:MOMENTUM_RR. No break-even. Same time / stall exits.
Stretch momentum (uses the Stretch reversal signal, but does not trade it directly):
  - The stretch signal candle is recorded. Within the next ENTRY_WINDOW candles, if price reaches
    ENTRY_EXT (50%) of that candle's length beyond its high (buy) / low (sell), the trade is entered.
  - Stop: the recorded stretch candle's low (buy) / high (sell). Target: 1:MOMENTUM_RR.
  - Break-even: after a move of 30% of the ENTRY candle's length, stop -> entry +/- commission.
  - Exit at market after MAX_HOLD candles, or (STALL_EXIT) when a HA candle closes against the
    trade while price is between entry and target.
HA BOS bias:
  - Signal: Heikin Ashi candle closes beyond two pullback cycles (sell / buy). Target 1:RR.
Stretch reversal and HA BOS:
  - Entry : first real tick after the signal candle closes (+ latency), at market
            (buy at ask, sell at bid) plus slippage against you.
  - Stop  : signal candle high (sell) or low (buy) - Heikin Ashi values, as on the chart.
  - Break-even: once price moves in your favour by 30% of the signal candle's length
            (HA high - HA low), the stop moves to the entry price.
  - One trade at a time; signals that arrive while a trade is open are skipped.

Costs: real bid/ask spread from every tick, slippage on market entries and stop exits,
       optional commission, entry latency after the candle closes.

Run: python backtest.py      -> writes backtest_report.html + backtest_trades.csv and opens the report
"""

import csv
import html
import math
import sys
import time
import webbrowser
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

try:
    import MetaTrader5 as mt5
except ImportError:
    print("MetaTrader5 library not found. Run:  pip install MetaTrader5 pandas numpy")
    input("Press Enter to exit")
    sys.exit(1)

# ======================= settings =======================
SYMBOL = None             # None = auto-detect gold (e.g. XAUUSDm)
DAYS = 5                  # days of tick history to test (weekends have no ticks)
CANDLE_SEC = 15

START_BALANCE = 100.0     # USD
LOT_MODE = "fixed"        # "fixed" = always FIXED_LOT; "risk" = size so the stop risks RISK_PERCENT
FIXED_LOT = 0.01
RISK_PERCENT = 2.0        # used only when LOT_MODE = "risk"

STRATEGY = "limit"        # "limit" = Stretch limit + breakout; "momentum" = Stretch momentum (market entry);
                          # "stretch" = Stretch reversal (direct entry,
                          # mitigation target); "bos" = HA BOS bias (target = 1:RR)

# --- Stretch momentum settings (STRATEGY = "momentum"; the signal uses the Stretch settings below) ---
ENTRY_WINDOW = 5          # candles after the stretch candle in which the entry can happen
ENTRY_EXT = 0.5           # buy level = stretch candle high + 50% of its length (sell: low - 50%)
ENTRY_MODE = "close"      # "close" = a HA candle closes beyond the level -> enter at market after it closes
                          # "touch" = a buy-stop / sell-stop order at the level, filled when price touches it
MOMENTUM_RR = 2.0         # target = 2 x risk
MAX_HOLD = 10             # candles after the entry candle; then exit at market
STALL_EXIT = True         # exit at market if a HA candle closes against the trade while price is
                          # between entry and target ("revolving")
BE_COVERS_COMMISSION = True  # break-even stop = entry +/- commission, so a BE exit costs ~nothing

# --- Stretch limit + breakout settings (STRATEGY = "limit"; trigger candle found as in "momentum") ---
LIMIT_PULLBACK = 0.20     # buy limit at trigger candle high - 20% of its length (sell: low + 20%)
LIMIT_VALID = 1           # candles the limit order stays active
BREAKOUT_EXT = 0.20       # buy stop at trigger candle high + 20% of its length (sell: low - 20%)
BREAKOUT_VALID = 2        # candles the breakout order stays active
USE_LIMIT, USE_BREAKOUT = True, True   # which of the two orders to place (both = first fill cancels the other)
LIMIT_BREAK_EVEN = False  # no break-even in this mode

# --- Stretch reversal settings (same as the chart indicator) ---
FAST_EMA, SLOW_EMA, BASE_SMMA = 9, 15, 50
STRETCH_ATR = 4.0         # both EMAs at least this many ATR(14) away from the SMMA = stretched
STRETCH_LOOKBACK = 40     # the stretch must have happened within this many candles before the signal
PULLBACKS = 1             # pullback cycles the BOS must break (1 or 2)
TP_LOOKBACK = 300         # how far left to search for the mitigation candle
MIN_RR = 0.0              # skip trades whose planned reward/risk is below this (0 = take all)
SWEEP = [2, 3, 4, 5, 6, 8]  # also test these STRETCH_ATR values (comparison table in the report)

# --- HA BOS settings ---
SIGNALS = "flips"         # "flips" = only bias changes (chart default); "all" = every break
BREAK_LEVEL = "wick"      # "wick" or "body"  (same as the chart setting)
TREND_FILTER = True       # same as the chart setting

RR = 2.0                  # HA BOS only: target = RR x risk
BE_FRACTION = 0.30        # move stop to entry after a move of 30% of the signal candle length

LATENCY_MS = 300          # delay after candle close before the order reaches the market
SLIPPAGE = 0.05           # price units ($ per oz) lost on market entry and on stop / break-even exits
COMMISSION_PER_LOT = 7.0  # round-trip USD per 1.00 lot = $0.07 per 0.01 lot (set 0 for Exness Standard)
# ========================================================

HERE = Path(__file__).resolve().parent
EPS = 1e-6                # price tolerance so a tick exactly on a level counts as reaching it


def fail(msg):
    print(msg)
    input("Press Enter to exit")
    sys.exit(1)


def connect():
    if not mt5.initialize():
        fail(f"Could not connect to MetaTrader 5: {mt5.last_error()}\n"
             "Make sure MT5 is open and logged in, then run this again.")


def find_symbol():
    names = [SYMBOL] if SYMBOL else sorted((s.name for s in (mt5.symbols_get("*XAUUSD*") or [])), key=len)
    for name in names:
        if mt5.symbol_select(name, True):
            for _ in range(20):
                tick = mt5.symbol_info_tick(name)
                if tick and tick.time_msc:
                    return name
                time.sleep(0.25)
    fail(f"Couldn't find a live XAUUSD symbol (found: {names or 'none'}).")


def utc_dt(ms):
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)


def load_ticks(symbol):
    latest = mt5.symbol_info_tick(symbol).time_msc
    start, day = latest - DAYS * 86_400_000, 86_400_000
    parts = []
    print(f"Loading {DAYS} days of ticks for {symbol}...")
    while start < latest:
        end = min(start + day, latest)
        raw = mt5.copy_ticks_range(symbol, utc_dt(start), utc_dt(end), mt5.COPY_TICKS_INFO)
        n = 0 if raw is None else len(raw)
        print(f"  {utc_dt(start):%Y-%m-%d}: {n:,} ticks")
        if n:
            parts.append(pd.DataFrame({"t": raw["time_msc"].astype(np.int64),
                                       "bid": raw["bid"].astype(float), "ask": raw["ask"].astype(float)}))
        start = end
    if not parts:
        fail("No ticks returned. Is MT5 logged in and does it have history for this symbol?")
    df = pd.concat(parts).drop_duplicates()
    df = df[(df.bid > 0) & (df.ask > 0)].sort_values("t", kind="stable").reset_index(drop=True)
    return df


# ---------------- candles + Heikin Ashi (bid prices, same as the chart) ----------------
def build_ha(ticks):
    ms = CANDLE_SEC * 1000
    bucket = (ticks["t"].to_numpy() // ms) * ms
    g = pd.DataFrame({"b": bucket, "p": ticks["bid"].to_numpy()}).groupby("b", sort=True)["p"]
    c = pd.DataFrame({"open": g.first(), "high": g.max(), "low": g.min(), "close": g.last()})
    o, h, l, cl = (c[k].to_numpy() for k in ("open", "high", "low", "close"))
    hc = (o + h + l + cl) / 4
    ho = np.empty(len(c)); ho[0] = (o[0] + cl[0]) / 2
    for i in range(1, len(c)):
        ho[i] = (ho[i - 1] + hc[i - 1]) / 2
    return pd.DataFrame({"t": c.index.to_numpy(), "open": ho, "high": np.maximum.reduce([h, ho, hc]),
                         "low": np.minimum.reduce([l, ho, hc]), "close": hc, "rclose": cl})


# ---------------- HA BOS signals (line-for-line port of the chart's bosCalc) ----------------
def bos_signals(ha, n, level="wick", trend=True):
    O, H, L, C = (ha[k].to_numpy() for k in ("open", "high", "low", "close"))
    sig = []
    st = {"sell": {"cyc": [], "cur": None, "skip": False}, "buy": {"cyc": [], "cur": None, "skip": False}}

    def finish(T, side):
        c = T["cur"]; T["cur"] = None
        if side == "sell":
            c["lvl"] = c["blo"] if level == "body" else c["lo"]
        else:
            c["lvl"] = c["bhi"] if level == "body" else c["hi"]
        T["cyc"].append(c)
        if len(T["cyc"]) > 2:
            T["cyc"].pop(0)

    def track(T, pullback, i, side):
        if pullback:
            if T["skip"]:
                return
            if T["cur"] is None:
                T["cur"] = {"s": i, "hi": -math.inf, "lo": math.inf, "bhi": -math.inf, "blo": math.inf}
            c = T["cur"]
            c["hi"] = max(c["hi"], H[i]); c["lo"] = min(c["lo"], L[i])
            c["bhi"] = max(c["bhi"], O[i], C[i]); c["blo"] = min(c["blo"], O[i], C[i])
        else:
            T["skip"] = False
            if T["cur"] is not None:
                finish(T, side)

    for i in range(n):
        green = C[i] >= O[i]
        S, B = st["sell"], st["buy"]
        if len(S["cyc"]) == 2 and (not trend or S["cyc"][1]["lvl"] > S["cyc"][0]["lvl"]):
            lvl = min(S["cyc"][0]["lvl"], S["cyc"][1]["lvl"])
            if C[i] < lvl:
                sig.append({"idx": i, "side": "sell", "level": lvl})
                S["cyc"] = []; S["cur"] = None; S["skip"] = True
        if len(B["cyc"]) == 2 and (not trend or B["cyc"][1]["lvl"] < B["cyc"][0]["lvl"]):
            lvl = max(B["cyc"][0]["lvl"], B["cyc"][1]["lvl"])
            if C[i] > lvl:
                sig.append({"idx": i, "side": "buy", "level": lvl})
                B["cyc"] = []; B["cur"] = None; B["skip"] = True
        track(S, not green, i, "sell")
        track(B, green, i, "buy")
    prev = None
    for s in sig:
        s["flip"] = s["side"] != prev
        prev = s["side"]
    return sig


# ---------------- Stretch reversal (line-for-line port of the chart's stretchCalc) ----------------
def _seeded(x, n, step):
    o = [math.nan] * len(x)
    s, prev = 0.0, math.nan
    for i, v in enumerate(x):
        if i < n:
            s += v
            if i == n - 1:
                prev = s / n
                o[i] = prev
        else:
            prev = step(prev, v)
            o[i] = prev
    return o


def ema_list(x, n):
    k = 2 / (n + 1)
    return _seeded(x, n, lambda p, v: v * k + p * (1 - k))


def smma_list(x, n):
    return _seeded(x, n, lambda p, v: (p * (n - 1) + v) / n)


def stretch_signals(ha, n, k=None, level="wick", need=1, trend=True, look=None):
    k = STRETCH_ATR if k is None else k
    look = STRETCH_LOOKBACK if look is None else look
    O, H, L, C = (ha[c].tolist() for c in ("open", "high", "low", "close"))
    R = ha["rclose"].tolist()
    m = len(C)
    e1, e2, sm = ema_list(C, FAST_EMA), ema_list(C, SLOW_EMA), smma_list(C, BASE_SMMA)
    tr = [H[i] - L[i] if i == 0 else max(H[i] - L[i], abs(H[i] - C[i - 1]), abs(L[i] - C[i - 1])) for i in range(m)]
    atr = smma_list(tr, 14)
    up, dn = [math.nan] * m, [math.nan] * m
    for i in range(m):
        if math.isnan(e1[i]) or math.isnan(e2[i]) or math.isnan(sm[i]) or not (atr[i] > 0):
            continue
        up[i] = (min(e1[i], e2[i]) - sm[i]) / atr[i]
        dn[i] = (sm[i] - max(e1[i], e2[i])) / atr[i]
    need = 2 if need == 2 else 1
    st = {"sell": {"cyc": [], "cur": None, "skip": False}, "buy": {"cyc": [], "cur": None, "skip": False}}

    def finish(T, side):
        c = T["cur"]; T["cur"] = None
        if side == "sell":
            c["lvl"] = c["blo"] if level == "body" else c["lo"]
        else:
            c["lvl"] = c["bhi"] if level == "body" else c["hi"]
        T["cyc"].append(c)
        if len(T["cyc"]) > need:
            T["cyc"].pop(0)

    def track(T, pullback, i, side):
        if pullback:
            if T["skip"]:
                return
            if T["cur"] is None:
                T["cur"] = {"s": i, "hi": -math.inf, "lo": math.inf, "bhi": -math.inf, "blo": math.inf}
            c = T["cur"]
            c["hi"] = max(c["hi"], H[i]); c["lo"] = min(c["lo"], L[i])
            c["bhi"] = max(c["bhi"], O[i], C[i]); c["blo"] = min(c["blo"], O[i], C[i])
        else:
            T["skip"] = False
            if T["cur"] is not None:
                finish(T, side)

    def find_tp(side, frm, close):
        j = frm
        while j >= 0 and j >= frm - TP_LOOKBACK:
            if side == "sell" and C[j] < O[j] and L[j] < close:
                return L[j], j
            if side == "buy" and C[j] >= O[j] and H[j] > close:
                return H[j], j
            j -= 1
        return None, None

    sig, last_up, last_dn = [], -math.inf, -math.inf
    for i in range(n):
        green = C[i] >= O[i]
        if up[i] >= k:
            last_up = i
        if dn[i] >= k:
            last_dn = i
        for side in ("sell", "buy"):
            T = st[side]
            if len(T["cyc"]) < need:
                continue
            if need == 2 and trend and not (T["cyc"][1]["lvl"] > T["cyc"][0]["lvl"] if side == "sell"
                                             else T["cyc"][1]["lvl"] < T["cyc"][0]["lvl"]):
                continue
            lvls = [c["lvl"] for c in T["cyc"]]
            lvl = min(lvls) if side == "sell" else max(lvls)
            if (C[i] < lvl) if side == "sell" else (C[i] > lvl):
                last = last_up if side == "sell" else last_dn
                if i - last <= look:
                    tp, tp_idx = find_tp(side, min(c["s"] for c in T["cyc"]) - 1, R[i])
                    sig.append({"idx": i, "side": side, "level": lvl, "tp": tp, "tp_idx": tp_idx,
                                "gap": up[last] if side == "sell" else dn[last]})
                T["cyc"] = []; T["cur"] = None; T["skip"] = True
        track(st["sell"], not green, i, "sell")
        track(st["buy"], green, i, "buy")
    return sig


# ---------------- tick-by-tick trade simulation ----------------
def first_true(fn, start, n):
    """Index of the first tick >= start where fn(slice) is True, or None. Searches in growing chunks."""
    i, step = start, 4096
    while i < n:
        j = min(n, i + step)
        m = fn(i, j)
        if m.any():
            return i + int(m.argmax())
        i, step = j, step * 2
    return None


def retarget(ha, buy, frm, entry):
    """Keep scanning left for the first green (buy) / red (sell) HA candle beyond the actual fill price."""
    O, H, L, C = ha["open"].to_numpy(), ha["high"].to_numpy(), ha["low"].to_numpy(), ha["close"].to_numpy()
    j = frm
    while j >= 0 and j >= frm - TP_LOOKBACK:
        if buy and C[j] >= O[j] and H[j] > entry:
            return float(H[j])
        if not buy and C[j] < O[j] and L[j] < entry:
            return float(L[j])
        j -= 1
    return None


def simulate(ticks, ha, sig, contract, vmin, vstep, only_flips=False):
    T = ticks["t"].to_numpy(); BID = ticks["bid"].to_numpy(); ASK = ticks["ask"].to_numpy()
    N = len(T)
    ms = CANDLE_SEC * 1000
    trades = []
    skipped = {"in_trade": 0, "stop_already_hit": 0, "no_ticks": 0, "balance": 0,
               "no_target": 0, "target_already_hit": 0, "low_rr": 0}
    balance, busy_until = START_BALANCE, -1

    for s in sig:
        if only_flips and not s.get("flip", True):
            continue
        i = s["idx"]
        close_ms = int(ha["t"].iat[i]) + ms
        if close_ms < busy_until:
            skipped["in_trade"] += 1
            continue
        if balance <= 0:
            skipped["balance"] += 1
            continue
        k = int(np.searchsorted(T, close_ms + LATENCY_MS))
        if k >= N:
            skipped["no_ticks"] += 1
            continue
        hi, lo = float(ha["high"].iat[i]), float(ha["low"].iat[i])
        length = hi - lo
        buy = s["side"] == "buy"
        spread = ASK[k] - BID[k]
        if buy:
            entry, sl = ASK[k] + SLIPPAGE, lo
            risk = entry - sl
        else:
            entry, sl = BID[k] - SLIPPAGE, hi
            risk = sl - entry
        if risk <= 0:
            skipped["stop_already_hit"] += 1
            continue
        if "tp" in s:                              # Stretch: target = mitigation candle
            if s["tp"] is None:
                skipped["no_target"] += 1
                continue
            tp = s["tp"]
            if (buy and tp <= entry) or (not buy and tp >= entry):
                tp = retarget(ha, buy, s["tp_idx"] - 1, entry)   # first mitigation candle beyond the fill
                if tp is None:
                    skipped["target_already_hit"] += 1
                    continue
        else:
            tp = entry + RR * risk if buy else entry - RR * risk
        plan_rr = abs(tp - entry) / risk
        if plan_rr < MIN_RR:
            skipped["low_rr"] += 1
            continue
        be_trig = entry + BE_FRACTION * length if buy else entry - BE_FRACTION * length

        if LOT_MODE == "risk":
            lots = math.floor(balance * RISK_PERCENT / 100 / (risk * contract) / vstep) * vstep
            lots = max(vmin, round(lots, 2))
        else:
            lots = FIXED_LOT

        # a long closes at the bid, a short at the ask
        px = BID if buy else ASK
        if buy:
            f_sl = lambda a, b: px[a:b] <= sl
            f_tp = lambda a, b: px[a:b] >= tp
            f_be = lambda a, b: px[a:b] >= be_trig
            f_bx = lambda a, b: px[a:b] <= entry
        else:
            f_sl = lambda a, b: px[a:b] >= sl
            f_tp = lambda a, b: px[a:b] <= tp
            f_be = lambda a, b: px[a:b] <= be_trig
            f_bx = lambda a, b: px[a:b] >= entry
        j = k + 1
        i_sl, i_tp, i_be = first_true(f_sl, j, N), first_true(f_tp, j, N), first_true(f_be, j, N)
        INF = N + 1
        a_sl, a_tp, a_be = (x if x is not None else INF for x in (i_sl, i_tp, i_be))
        be_at = None
        if a_be < a_sl and a_be < a_tp:            # break-even reached first (a TP on the same tick wins)
            be_at = a_be
            i_bx = first_true(f_bx, a_be + 1, N)
            a_bx = i_bx if i_bx is not None else INF
            if a_tp <= a_bx:
                ex_i, result = a_tp, "TP"
            else:
                ex_i, result = a_bx, "BE"
        else:
            if a_sl <= a_tp:                       # same tick: assume the stop (conservative)
                ex_i, result = a_sl, "SL"
            else:
                ex_i, result = a_tp, "TP"
        if ex_i >= N:                              # still open at the end of the data
            ex_i, result = N - 1, "OPEN"
            exit_px = px[ex_i]
        elif result == "TP":
            exit_px = tp                           # limit order: filled at the target price
        else:
            exit_px = px[ex_i] - SLIPPAGE if buy else px[ex_i] + SLIPPAGE   # stop = market order

        move = (exit_px - entry) if buy else (entry - exit_px)
        pnl = move * contract * lots - COMMISSION_PER_LOT * lots
        balance += pnl
        busy_until = int(T[ex_i])
        trades.append({
            "signal_time": close_ms, "entry_time": int(T[k]), "exit_time": int(T[ex_i]),
            "side": s["side"].upper(), "lots": lots, "entry": entry, "stop": sl, "target": tp,
            "candle_len": length, "be_trigger": be_trig, "be_hit": be_at is not None,
            "exit": exit_px, "result": result, "r": move / risk, "pnl": pnl, "balance": balance,
            "risk_usd": risk * contract * lots, "spread": spread, "plan_rr": plan_rr,
        })
    return trades, skipped


# ---------------- shared tick-by-tick trade management ----------------
def manage_trade(T, BID, ASK, HT, HO, HC, last_closed, k, buy, entry, sl, tp, j, t_end, stall,
                 use_be, be_from=0, be_need=0.0, be_stop=0.0):
    """Walk ticks after the fill (index k). Exits: stop, target, break-even (optional), time, stall.
    j = index of the entry candle; stall checks start with the first candle that closes after it."""
    N, ms = len(T), CANDLE_SEC * 1000
    px = BID if buy else ASK                       # a long closes at the bid, a short at the ask
    stop, be_on, c, reason, ex = sl, False, j + 1, None, None
    t = k + 1
    while t < N:
        tt, q = T[t], px[t]
        if tt >= t_end:
            reason, ex = "TIME", t
            break
        while c <= last_closed and int(HT[c]) + ms <= tt:          # candles that have closed
            if stall and c > j:
                against = HC[c] < HO[c] if buy else HC[c] >= HO[c]
                between = (entry < q < tp) if buy else (tp < q < entry)
                if against and between:
                    reason = "STALL"
                    break
            c += 1
        if reason:
            ex = t
            break
        if (q <= stop + EPS) if buy else (q >= stop - EPS):
            reason, ex = ("BE" if be_on else "SL"), t
            break
        if (q >= tp - EPS) if buy else (q <= tp + EPS):
            reason, ex = "TP", t
            break
        if use_be and not be_on and tt >= be_from and ((q >= be_need - EPS) if buy else (q <= be_need + EPS)):
            be_on, stop = True, be_stop
        t += 1
    if reason is None:
        reason, ex = "OPEN", N - 1
        exit_px = px[ex]
    elif reason == "TP":
        exit_px = tp
    else:
        exit_px = px[ex] - SLIPPAGE if buy else px[ex] + SLIPPAGE
    # open-trade excursions, marked at the price you could close at (bid for a long, ask for a short)
    seg = px[k:ex + 1]
    worst = min(seg.min(), exit_px) if buy else max(seg.max(), exit_px)
    best = seg.max() if buy else seg.min()
    mae = max(0.0, (entry - worst) if buy else (worst - entry))         # deepest point against you
    mfe = max(0.0, (best - entry) if buy else (entry - best))           # furthest point in your favour
    if reason == "TP":                                                 # the target order fills at the target
        mfe = min(mfe, abs(tp - entry))
    i_w = k + int((seg.argmin() if buy else seg.argmax()))
    return reason, ex, be_on, exit_px, mae, mfe, int(T[i_w])


def excursions(mae, mfe, risk, contract, lots, t_worst, t_entry):
    return {"mae_usd": mae * contract * lots, "mae_r": mae / risk, "mfe_usd": mfe * contract * lots,
            "mfe_r": mfe / risk, "mae_after_s": (t_worst - t_entry) / 1000}


def find_trigger(HC, lvl, buy, i, last_closed):
    """First candle within ENTRY_WINDOW after the stretch candle that closes beyond the level."""
    return next((jj for jj in range(i + 1, min(i + ENTRY_WINDOW, last_closed) + 1)
                 if (HC[jj] >= lvl if buy else HC[jj] <= lvl)), None)


# ---------------- Stretch limit + breakout ----------------
def simulate_limit(ticks, ha, sig, contract, use_limit=None, use_breakout=None, stall=None):
    use_limit = USE_LIMIT if use_limit is None else use_limit
    use_breakout = USE_BREAKOUT if use_breakout is None else use_breakout
    stall = STALL_EXIT if stall is None else stall
    T = ticks["t"].to_numpy(); BID = ticks["bid"].to_numpy(); ASK = ticks["ask"].to_numpy()
    N, ms = len(T), CANDLE_SEC * 1000
    HT = ha["t"].to_numpy()
    HO, HH, HL, HC = (ha[c].to_numpy() for c in ("open", "high", "low", "close"))
    last_closed = len(HT) - 2
    lots = FIXED_LOT
    trades, balance, busy_until = [], START_BALANCE, -1
    skipped = {"in_trade": 0, "not_triggered": 0, "void": 0, "no_ticks": 0}
    for s in sig:
        i, buy = s["idx"], s["side"] == "buy"
        length = HH[i] - HL[i]
        lvl = HH[i] + ENTRY_EXT * length if buy else HL[i] - ENTRY_EXT * length
        sig_close = int(HT[i]) + ms
        if sig_close < busy_until:
            skipped["in_trade"] += 1
            continue
        a = find_trigger(HC, lvl, buy, i, last_closed)
        if a is None:
            skipped["not_triggered"] += 1
            continue
        alen = HH[a] - HL[a]
        ref = HH[a] if buy else HL[a]              # trigger candle high (buy) / low (sell)
        lim = ref - LIMIT_PULLBACK * alen if buy else ref + LIMIT_PULLBACK * alen
        brk = ref + BREAKOUT_EXT * alen if buy else ref - BREAKOUT_EXT * alen
        sl = HL[a] if buy else HH[a]
        start = int(HT[a]) + ms + LATENCY_MS       # orders reach the broker after the trigger candle closes
        a0 = int(np.searchsorted(T, start))
        k_lim = k_brk = None
        if use_limit:
            a1 = int(np.searchsorted(T, int(HT[a]) + ms + LIMIT_VALID * ms))
            seg = ASK[a0:a1] if buy else BID[a0:a1]     # a buy limit fills when the ask comes down to it
            hit = seg <= lim + EPS if buy else seg >= lim - EPS
            if hit.any():
                k_lim = a0 + int(hit.argmax())
        if use_breakout:
            a2 = int(np.searchsorted(T, int(HT[a]) + ms + BREAKOUT_VALID * ms))
            seg = ASK[a0:a2] if buy else BID[a0:a2]     # a buy stop triggers when the ask reaches it
            hit = seg >= brk - EPS if buy else seg <= brk + EPS
            if hit.any():
                k_brk = a0 + int(hit.argmax())
        if k_lim is None and k_brk is None:
            skipped["void"] += 1
            continue
        if k_brk is None or (k_lim is not None and k_lim <= k_brk):
            k, fill = k_lim, "LIMIT"               # limit: filled at the limit price or better, no slippage
            entry = min(lim, ASK[k]) if buy else max(lim, BID[k])
        else:
            k, fill = k_brk, "BREAKOUT"            # stop order becomes a market order: slippage applies
            entry = ASK[k] + SLIPPAGE if buy else BID[k] - SLIPPAGE
        if (buy and entry <= sl) or (not buy and entry >= sl):
            skipped["void"] += 1
            continue
        risk = abs(entry - sl)
        tp = entry + MOMENTUM_RR * risk if buy else entry - MOMENTUM_RR * risk
        j = int(np.searchsorted(HT, (T[k] // ms) * ms))      # candle in which the fill happened
        t_end = int(HT[j]) + ms + MAX_HOLD * ms if j < len(HT) else int(T[k]) + MAX_HOLD * ms
        reason, ex, be_on, exit_px, mae, mfe, t_worst = manage_trade(T, BID, ASK, HT, HO, HC, last_closed, k, buy, entry, sl, tp,
                                                  j, t_end, stall, LIMIT_BREAK_EVEN)
        move = (exit_px - entry) if buy else (entry - exit_px)
        pnl = move * contract * lots - COMMISSION_PER_LOT * lots
        balance += pnl
        busy_until = int(T[ex])
        trades.append({
            "signal_time": sig_close, "entry_time": int(T[k]), "exit_time": int(T[ex]),
            "side": "BUY" if buy else "SELL", "fill": fill, "lots": lots, "entry_level": lim if fill == "LIMIT" else brk,
            "entry": entry, "stop": sl, "target": tp, "stretch_len": length, "candle_len": alen,
            "be_trigger": math.nan, "be_hit": False, "exit": exit_px, "result": reason, "r": move / risk,
            "pnl": pnl, "balance": balance, "risk_usd": risk * contract * lots, "spread": ASK[k] - BID[k],
            "plan_rr": MOMENTUM_RR, "held_candles": max(0, int((T[ex] - (int(HT[min(j, len(HT) - 1)]) + ms)) // ms)),
            **excursions(mae, mfe, risk, contract, lots, t_worst, int(T[k])),
            "entry_candle_t": int(HT[min(j, len(HT) - 1)]), "signal_candle_t": int(HT[i]), "trigger_candle_t": int(HT[a]),
            "limit": lim, "breakout": brk,
        })
    return trades, skipped


# ---------------- Stretch momentum: entry on extension, tick-by-tick management ----------------
def simulate_momentum(ticks, ha, sig, contract, entry_mode=None, stall=None):
    entry_mode = ENTRY_MODE if entry_mode is None else entry_mode
    stall = STALL_EXIT if stall is None else stall
    T = ticks["t"].to_numpy(); BID = ticks["bid"].to_numpy(); ASK = ticks["ask"].to_numpy()
    N, ms = len(T), CANDLE_SEC * 1000
    HT = ha["t"].to_numpy()
    HO, HH, HL, HC = (ha[c].to_numpy() for c in ("open", "high", "low", "close"))
    last_closed = len(HT) - 2                      # the newest candle may still be forming
    be_off = COMMISSION_PER_LOT / contract if BE_COVERS_COMMISSION else 0.0
    lots = FIXED_LOT
    trades, balance, busy_until = [], START_BALANCE, -1
    skipped = {"in_trade": 0, "not_triggered": 0, "stop_already_hit": 0, "no_ticks": 0}
    for s in sig:
        i, buy = s["idx"], s["side"] == "buy"
        length = HH[i] - HL[i]
        lvl = HH[i] + ENTRY_EXT * length if buy else HL[i] - ENTRY_EXT * length
        sl = HL[i] if buy else HH[i]
        sig_close = int(HT[i]) + ms
        if sig_close < busy_until:
            skipped["in_trade"] += 1
            continue
        if entry_mode == "close":                  # a HA candle closes beyond the level
            j = find_trigger(HC, lvl, buy, i, last_closed)
            if j is None:
                skipped["not_triggered"] += 1
                continue
            k = int(np.searchsorted(T, int(HT[j]) + ms + LATENCY_MS))
            if k >= N:
                skipped["no_ticks"] += 1
                continue
            entry = ASK[k] + SLIPPAGE if buy else BID[k] - SLIPPAGE
            be_from = T[k]
        else:                                      # stop order resting at the level
            a = int(np.searchsorted(T, sig_close))
            b = int(np.searchsorted(T, sig_close + ENTRY_WINDOW * ms))
            seg = ASK[a:b] if buy else BID[a:b]
            hit = seg >= lvl if buy else seg <= lvl
            if not hit.any():
                skipped["not_triggered"] += 1
                continue
            k = a + int(hit.argmax())
            entry = ASK[k] + SLIPPAGE if buy else BID[k] - SLIPPAGE
            j = int(np.searchsorted(HT, (T[k] // ms) * ms))
            be_from = int(HT[j]) + ms              # entry candle length is known once it closes
        if (buy and entry <= sl) or (not buy and entry >= sl):
            skipped["stop_already_hit"] += 1
            continue
        risk = abs(entry - sl)
        tp = entry + MOMENTUM_RR * risk if buy else entry - MOMENTUM_RR * risk
        x = HH[j] - HL[j]                          # entry candle length
        be_trig = entry + BE_FRACTION * x if buy else entry - BE_FRACTION * x
        be_stop = entry + be_off if buy else entry - be_off
        be_need = max(be_trig, be_stop) if buy else min(be_trig, be_stop)
        t_end = int(HT[j]) + ms + MAX_HOLD * ms
        reason, ex, be_on, exit_px, mae, mfe, t_worst = manage_trade(T, BID, ASK, HT, HO, HC, last_closed, k, buy, entry, sl, tp,
                                                  j, t_end, stall, True, be_from, be_need, be_stop)
        move = (exit_px - entry) if buy else (entry - exit_px)
        pnl = move * contract * lots - COMMISSION_PER_LOT * lots
        balance += pnl
        busy_until = int(T[ex])
        trades.append({
            "signal_time": sig_close, "entry_time": int(T[k]), "exit_time": int(T[ex]),
            "side": "BUY" if buy else "SELL", "lots": lots, "entry_level": lvl, "entry": entry, "stop": sl,
            "target": tp, "stretch_len": length, "candle_len": x, "be_trigger": be_need, "be_hit": be_on,
            "exit": exit_px, "result": reason, "r": move / risk, "pnl": pnl, "balance": balance,
            "risk_usd": risk * contract * lots, "spread": ASK[k] - BID[k], "plan_rr": MOMENTUM_RR,
            "held_candles": max(0, int((T[ex] - (int(HT[j]) + ms)) // ms)),
            **excursions(mae, mfe, risk, contract, lots, t_worst, int(T[k])),
            "entry_candle_t": int(HT[j]), "signal_candle_t": int(HT[i]),
        })
    return trades, skipped


def stat_row(label, sg, tr, current=False, **extra):
    x = stats(tr)
    return {"label": label, "current": current, "signals": len(sg), "trades": len(tr),
            "win": x.get("win_rate", 0), "exits": exits_text(x) if x else "-",
            "pf": ("∞" if math.isinf(x["pf"]) else f"{x['pf']:.2f}") if x else "-",
            "net": x.get("net", 0.0), "dd": x.get("max_dd", 0.0), **extra}


def exits_text(st):
    parts = [f"{st['tp']} TP", f"{st['sl']} SL", f"{st['be']} BE"]
    if st.get("time"):
        parts.append(f"{st['time']} time")
    if st.get("stall"):
        parts.append(f"{st['stall']} stall")
    if st.get("open"):
        parts.append(f"{st['open']} open")
    return " / ".join(parts)


def table_html(title, rows, first_col, note):
    if not rows:
        return ""
    HL = ' style="background:#2a2e39"'
    body = "".join(
        f"<tr{HL if r['current'] else ''}><td>{html.escape(r['label'])}{' (current)' if r['current'] else ''}</td>"
        f"<td>{r['signals']}</td><td>{r['trades']}</td><td>{r['win']:.1f}%</td><td>{r['exits']}</td><td>{r['pf']}</td>"
        f"<td style='color:{'#26a69a' if r['net'] >= 0 else '#ef5350'}'>{'+' if r['net'] >= 0 else '-'}${abs(r['net']):.2f}</td>"
        f"<td>${r['dd']:.2f}</td></tr>" for r in rows)
    return (f"<h2>{title}</h2><div class='box'><table><thead><tr><th>{first_col}</th><th>Signals</th><th>Trades</th>"
            "<th>Win rate</th><th>Exits</th><th>Profit factor</th><th>Net</th><th>Max DD</th></tr></thead>"
            f"<tbody>{body}</tbody></table><p style='color:#787b86;font-size:12px;margin:8px 4px 0'>{note}</p></div>")


# ---------------- statistics + report ----------------
def stats(trades):
    if not trades:
        return {}
    pnl = np.array([t["pnl"] for t in trades])
    eq = START_BALANCE + np.cumsum(pnl)
    peak = np.maximum.accumulate(np.concatenate([[START_BALANCE], eq]))[1:]
    dd = peak - eq
    i_dd = int(dd.argmax())
    res = [t["result"] for t in trades]
    wins, losses = pnl[pnl > 0], pnl[pnl < 0]
    streak = cur = 0
    for p in pnl:
        cur = cur + 1 if p < 0 else 0
        streak = max(streak, cur)
    return {
        "trades": len(trades), "tp": res.count("TP"), "sl": res.count("SL"), "be": res.count("BE"),
        "open": res.count("OPEN"), "time": res.count("TIME"), "stall": res.count("STALL"),
        "win_rate": 100 * len(wins) / len(trades),
        "net": float(pnl.sum()), "final": float(eq[-1]), "ret": 100 * float(pnl.sum()) / START_BALANCE,
        "pf": float(wins.sum() / -losses.sum()) if len(losses) else float("inf"),
        "avg_r": float(np.mean([t["r"] for t in trades])),
        "max_dd": float(dd.max()), "max_dd_pct": 100 * float(dd.max() / peak[i_dd]) if peak[i_dd] else 0,
        "streak": streak, "avg_spread": float(np.mean([t["spread"] for t in trades])),
        "avg_risk": float(np.mean([t["risk_usd"] for t in trades])),
        "be_rate": 100 * sum(t["be_hit"] for t in trades) / len(trades),
        "plan_rr": float(np.mean([t["plan_rr"] for t in trades])),
        "avg_hold": float(np.mean([t.get("held_candles", 0) for t in trades])),
    }


def fmt_t(ms):
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%m-%d %H:%M:%S")


def equity_svg(trades, w=900, h=240):
    vals = [START_BALANCE] + [t["balance"] for t in trades]
    lo, hi = min(vals), max(vals)
    if hi - lo < 1e-9:
        hi, lo = hi + 1, lo - 1
    pad = (hi - lo) * 0.08
    lo, hi = lo - pad, hi + pad
    X = lambda i: 50 + (w - 60) * i / max(1, len(vals) - 1)
    Y = lambda v: 10 + (h - 30) * (hi - v) / (hi - lo)
    pts = " ".join(f"{X(i):.1f},{Y(v):.1f}" for i, v in enumerate(vals))
    grid = "".join(
        f'<line x1="50" x2="{w - 10}" y1="{Y(v):.1f}" y2="{Y(v):.1f}" stroke="#2a2e39"/>'
        f'<text x="44" y="{Y(v) + 4:.1f}" text-anchor="end" fill="#787b86" font-size="11">${v:.0f}</text>'
        for v in np.linspace(lo + pad, hi - pad, 5))
    base = f'<line x1="50" x2="{w - 10}" y1="{Y(START_BALANCE):.1f}" y2="{Y(START_BALANCE):.1f}" stroke="#787b86" stroke-dasharray="4 3"/>'
    col = "#26a69a" if vals[-1] >= START_BALANCE else "#ef5350"
    return (f'<svg viewBox="0 0 {w} {h}" width="100%" role="img" aria-label="Equity curve">{grid}{base}'
            f'<polyline fill="none" stroke="{col}" stroke-width="2" points="{pts}"/></svg>')


def write_report(symbol, ticks, ha, sig, trades, skipped, st, contract, sweep_rows=None, extra_html=""):
    first, last = fmt_t(int(ticks["t"].iat[0])), fmt_t(int(ticks["t"].iat[-1]))
    limit = STRATEGY == "limit"
    momentum = STRATEGY in ("momentum", "limit")
    stretch = STRATEGY in ("stretch", "momentum", "limit")
    n_sig = len(sig) if stretch else len([s for s in sig if SIGNALS == "all" or s["flip"]])
    name = ("Stretch limit + breakout" if limit else "Stretch momentum" if momentum
            else "Stretch reversal" if stretch else "HA BOS")

    def card(label, value, color=None):
        c = f' style="color:{color}"' if color else ""
        return f'<div class="card"><div class="l">{label}</div><div class="v"{c}>{value}</div></div>'

    if st:
        up, dn = "#26a69a", "#ef5350"
        cards = "".join([
            card("Final balance", f"${st['final']:.2f}", up if st["net"] >= 0 else dn),
            card("Net profit", f"{'+' if st['net'] >= 0 else '-'}${abs(st['net']):.2f} ({st['ret']:+.1f}%)", up if st["net"] >= 0 else dn),
            card("Trades", f"{st['trades']}"),
            card("Win rate", f"{st['win_rate']:.1f}%"),
            card("Exits", exits_text(st)),
            card("Profit factor", "∞" if math.isinf(st["pf"]) else f"{st['pf']:.2f}"),
            card("Avg result", f"{st['avg_r']:+.2f} R"),
            card("Max drawdown", f"${st['max_dd']:.2f} ({st['max_dd_pct']:.1f}%)"),
            card("Longest losing streak", f"{st['streak']}"),
            card("Reached break-even", f"{st['be_rate']:.0f}% of trades"),
            card("Avg risk per trade", f"${st['avg_risk']:.2f}"),
            *([card("Fills", f"{sum(t.get('fill') == 'LIMIT' for t in trades)} limit / "
                             f"{sum(t.get('fill') == 'BREAKOUT' for t in trades)} breakout")] if limit else []),
            *([card("Avg hold", f"{st['avg_hold']:.1f} candles")] if momentum else
              [card("Avg planned reward", f"{st['plan_rr']:.2f} R")]),
            card("Avg spread at entry", f"${st['avg_spread']:.3f}"),
        ])
    else:
        cards = '<p>No trades were taken in this period.</p>'

    exc = bool(trades) and "mae_usd" in trades[0]
    peak, acct_dd = START_BALANCE, []
    for t in trades:
        peak = max(peak, t["balance"])
        acct_dd.append((peak - t["balance"], 100 * (peak - t["balance"]) / peak))
    rows = "".join(
        f"<tr><td>{n}</td><td>{fmt_t(t['entry_time'])}</td>"
        f"<td style='color:{'#26a69a' if t['side'] == 'BUY' else '#ef5350'}'>{t['side']}"
        f"{' ' + t['fill'].lower() if t.get('fill') else ''}</td>"
        f"<td>{t['lots']:.2f}</td><td>{t['entry']:.3f}</td><td>{t['stop']:.3f}</td><td>{t['target']:.3f}</td>"
        f"<td>{t['candle_len']:.3f}</td><td>{'-' if math.isnan(t['be_trigger']) else format(t['be_trigger'], '.3f')}{' ✓' if t['be_hit'] else ''}</td>"
        f"<td>{t['exit']:.3f}</td><td class='{t['result'].lower()}'>{t['result']}</td>"
        f"<td>{t['r']:+.2f}</td><td style='color:{'#26a69a' if t['pnl'] >= 0 else '#ef5350'}'>{t['pnl']:+.2f}</td>"
        f"<td>{t['balance']:.2f}</td><td>{(t['exit_time'] - t['entry_time']) / 1000:.0f}s</td>"
        + (f"<td>{fmt_t(t['signal_time'])}</td><td>{t['stretch_len']:.3f}</td><td>{t['entry_level']:.3f}</td>" if momentum else "")
        + "</tr>"
        for n, t in enumerate(trades, 1))
    dd_html = ""
    if exc:
        wins = [t for t in trades if t["pnl"] > 0]
        losses = [t for t in trades if t["pnl"] <= 0]
        names = ["0 - 0.25 R", "0.25 - 0.5 R", "0.5 - 0.75 R", "0.75 - 1 R", "1 R or more (stop)"]
        def bucket(r):
            return names[0] if r < 0.25 else names[1] if r < 0.5 else names[2] if r < 0.75 else names[3] if r < 1 else names[4]
        cnt = {b: [0, 0] for b in names}
        for t in trades:
            cnt[bucket(t["mae_r"])][0 if t["pnl"] > 0 else 1] += 1
        avg = lambda xs: (sum(xs) / len(xs)) if xs else 0.0
        sumcards = "".join([
            card("Avg open drawdown", f"${avg([t['mae_usd'] for t in trades]):.2f} ({avg([t['mae_r'] for t in trades]):.2f} R)"),
            card("Worst open drawdown", f"${max(t['mae_usd'] for t in trades):.2f} ({max(t['mae_r'] for t in trades):.2f} R)"),
            card("Winners: avg / max heat", f"{avg([t['mae_r'] for t in wins]):.2f} / {max([t['mae_r'] for t in wins], default=0):.2f} R"),
            card("Losers: avg best profit", f"{avg([t['mfe_r'] for t in losses]):.2f} R"),
            card("Losers that were +1R first", f"{sum(t['mfe_r'] >= 1 for t in losses)} of {len(losses)}"),
            card("Account max drawdown", f"${max(d for d, _ in acct_dd):.2f} ({max(p for _, p in acct_dd):.1f}%)"),
        ])
        brows = "".join(f"<tr><td>{b}</td><td>{cnt[b][0]}</td><td>{cnt[b][1]}</td></tr>" for b in names)
        trows = "".join(
            f"<tr><td>{n}</td><td>{fmt_t(t['entry_time'])}</td>"
            f"<td style='color:{'#26a69a' if t['side'] == 'BUY' else '#ef5350'}'>{t['side']}{' ' + t['fill'].lower() if t.get('fill') else ''}</td>"
            f"<td class='{t['result'].lower()}'>{t['result']}</td><td>${t['risk_usd']:.2f}</td>"
            f"<td style='color:#ef5350'>-${t['mae_usd']:.2f}</td><td>{t['mae_r']:.2f} R</td><td>{t['mae_after_s']:.0f}s</td>"
            f"<td style='color:#26a69a'>+${t['mfe_usd']:.2f}</td><td>{t['mfe_r']:.2f} R</td>"
            f"<td style='color:{'#26a69a' if t['pnl'] >= 0 else '#ef5350'}'>{t['pnl']:+.2f}</td>"
            f"<td>{t['balance']:.2f}</td><td>${acct_dd[n - 1][0]:.2f} ({acct_dd[n - 1][1]:.1f}%)</td></tr>"
            for n, t in enumerate(trades, 1))
        dd_html = (
            "<h2>Drawdown per trade</h2><div class='cards'>" + sumcards + "</div>"
            "<p style='color:#787b86;font-size:12px'>Open drawdown = the furthest the trade went against you while open, "
            "marked at the price you could have closed at (bid for buys, ask for sells), so it includes the spread. "
            "Best profit = the furthest it went in your favour. Account drawdown = how far the balance was below its "
            "highest point after that trade.</p>"
            "<div class='box' style='margin-bottom:12px'><table><thead><tr><th>Deepest point against you</th><th>Winners</th>"
            f"<th>Losers / flat</th></tr></thead><tbody>{brows}</tbody></table></div>"
            "<div class='box scroll'><table><thead><tr><th>#</th><th>Entry time</th><th>Side</th><th>Result</th><th>Risk</th>"
            "<th>Max open loss</th><th>in R</th><th>after</th><th>Max open profit</th><th>in R</th><th>P&amp;L $</th>"
            f"<th>Balance</th><th>Account DD</th></tr></thead><tbody>{trows}</tbody></table></div>")

    sizing = (f"fixed {FIXED_LOT:.2f} lot" if LOT_MODE == "fixed"
              else f"{RISK_PERCENT:g}% of balance per trade (min lot {vmin_g:.2f})")
    notes = [
        f"Data: {symbol}, {len(ticks):,} ticks from {first} to {last} (broker server time), "
        f"{len(ha):,} candles of {CANDLE_SEC}s on Heikin Ashi (bid).",
        (f"Signals: Stretch reversal - EMA {FAST_EMA} and EMA {SLOW_EMA} both >= {STRETCH_ATR:g} ATR(14) from "
         f"SMMA {BASE_SMMA} (HA closes) within {STRETCH_LOOKBACK} candles, then a close beyond {PULLBACKS} pullback "
         f"cycle(s) ({BREAK_LEVEL}): {n_sig} signals, {len(trades)} traded." if stretch else
         f"Signals: HA BOS, {'bias changes only' if SIGNALS == 'flips' else 'every break'}, break of {BREAK_LEVEL}, "
         f"trend filter {'on' if TREND_FILTER else 'off'}: {n_sig} signals, {len(trades)} traded."),
        "Skipped: " + (", ".join(f"{v} {SKIP_TEXT[k]}" for k, v in skipped.items() if v) or "none") + ".",
        f"Entry: first tick {LATENCY_MS} ms after the signal candle closed; buy at ask, sell at bid, "
        f"plus ${SLIPPAGE:.2f} slippage.",
        f"Stop: signal candle's Heikin Ashi high/low. Break-even after a favourable move of "
        f"{BE_FRACTION:.0%} of the signal candle length (HA high - HA low); stop moves to entry. " + (
            f"Target: the first red (sell) / green (buy) HA candle to the left of the pullback whose "
            f"low / high is beyond the real close; if the fill is already past it, the next one further left "
            f"(searching up to {TP_LOOKBACK} candles back)"
            + (f"; trades with planned reward below {MIN_RR:g} R skipped." if MIN_RR > 0 else ".")
            if stretch else f"Target: 1:{RR:g}."),
        f"Exits: target filled at the exact price (limit); stop and break-even exits at the triggering tick "
        f"minus ${SLIPPAGE:.2f} slippage. If stop and target hit on the same tick, the stop is assumed.",
        f"Size: {sizing}; contract size {contract:g} oz per lot; commission ${COMMISSION_PER_LOT:g} per lot round trip.",
        "Past results on a few days of data don't predict future results. Heikin Ashi values are averages, "
        "so stops placed at HA highs/lows differ from where real candles would put them.",
    ]
    if limit:
        orders = []
        if USE_LIMIT:
            orders.append(f"a limit {LIMIT_PULLBACK:.0%} of the trigger candle's length back from its high (buy) / low (sell), "
                          f"active for {LIMIT_VALID} candle(s), filled at the limit price or better")
        if USE_BREAKOUT:
            orders.append(f"a stop order {BREAKOUT_EXT:.0%} of its length beyond its high (buy) / low (sell), active for "
                          f"{BREAKOUT_VALID} candle(s), filled at market plus ${SLIPPAGE:.2f} slippage")
        notes[3:7] = [
            f"Stretch candle recorded, not traded. Trigger candle = the first of the next {ENTRY_WINDOW} candles whose HA close "
            f"is beyond the stretch candle's HA high + {ENTRY_EXT:.0%} of its length (buy) / low - {ENTRY_EXT:.0%} (sell).",
            f"When the trigger candle closes (+{LATENCY_MS} ms), orders are placed: " + "; and ".join(orders)
            + ". Whichever fills first cancels the other; if neither fills in time the setup is void. "
            "A buy fills on the ask, a sell on the bid.",
            f"Stop: the trigger candle's HA low (buy) / high (sell). Target: 1:{MOMENTUM_RR:g} from the actual fill. "
            + ("No break-even." if not LIMIT_BREAK_EVEN else ""),
            f"Exits: target filled at the exact price; stop exits at the triggering tick minus ${SLIPPAGE:.2f}. Time exit at "
            f"market {MAX_HOLD} candles after the fill candle. "
            + ("Stall exit at market when a HA candle closes against the trade while price is between entry and target. "
               if STALL_EXIT else "Stall exit off. ")
            + "If stop and target hit on the same tick, the stop is assumed.",
        ]
    elif momentum:
        trig = ("a HA candle closes beyond it; market entry at the first tick "
                f"{LATENCY_MS} ms after that candle closes" if ENTRY_MODE == "close" else
                "price touches it (stop order resting at the level, no latency)")
        notes[3:7] = [
            f"Stretch candle recorded, not traded. Entry level = stretch candle HA high + {ENTRY_EXT:.0%} of its length "
            f"(buy) / HA low - {ENTRY_EXT:.0%} (sell). Entered only if, within the next {ENTRY_WINDOW} candles, {trig}. "
            f"Buy at ask, sell at bid, plus ${SLIPPAGE:.2f} slippage.",
            f"Stop: the recorded stretch candle's HA low (buy) / high (sell). Target: 1:{MOMENTUM_RR:g} from the actual fill.",
            f"Break-even: when price moves {BE_FRACTION:.0%} of the ENTRY candle's HA length in your favour, the stop moves to "
            f"entry {'+ commission ($' + format(COMMISSION_PER_LOT / contract, '.2f') + ')' if BE_COVERS_COMMISSION else ''}; "
            "if that is further than 30%, it waits until price is past the break-even stop.",
            f"Exits: target filled at the exact price; stop / break-even exits at the triggering tick minus ${SLIPPAGE:.2f}. "
            f"Time exit at market {MAX_HOLD} candles after the entry candle. "
            + ("Stall exit at market when a HA candle closes against the trade while price is between entry and target. "
               if STALL_EXIT else "Stall exit off. ")
            + "If stop and target hit on the same tick, the stop is assumed.",
        ]
    page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>{name} Backtest</title>
<style>
body{{margin:0;background:#131722;color:#d1d4dc;font:14px -apple-system,"Segoe UI",Roboto,Arial,sans-serif}}
main{{max-width:1180px;margin:0 auto;padding:20px 16px 40px}}
h1{{font-size:20px;font-weight:600;margin:0 0 4px}} h2{{font-size:15px;font-weight:600;margin:28px 0 10px}}
.sub{{color:#787b86;margin-bottom:18px}}
.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:10px}}
.card{{background:#1e222d;border-radius:8px;padding:12px 14px}} .l{{color:#787b86;font-size:12px}}
.v{{font-size:20px;font-weight:600;margin-top:4px}}
.box{{background:#1e222d;border-radius:8px;padding:12px}}
table{{width:100%;border-collapse:collapse;font-size:12px;font-variant-numeric:tabular-nums}}
th,td{{padding:6px 8px;text-align:right;border-bottom:1px solid #2a2e39;white-space:nowrap}}
th{{color:#787b86;font-weight:500;position:sticky;top:0;background:#1e222d}}
td:nth-child(2),th:nth-child(2),td:nth-child(3),th:nth-child(3){{text-align:left}}
.tp{{color:#26a69a}} .sl{{color:#ef5350}} .be{{color:#f7c948}} .open{{color:#787b86}} .time,.stall{{color:#90caf9}}
.scroll{{overflow:auto;max-height:560px}} ul{{color:#a3a6af;line-height:1.7;padding-left:18px}}
</style></head><body><main>
<h1>{name} backtest &middot; {html.escape(symbol)} &middot; {CANDLE_SEC}s Heikin Ashi</h1>
<div class="sub">{first} &rarr; {last} &middot; starting balance ${START_BALANCE:.0f} &middot; generated {datetime.now():%Y-%m-%d %H:%M}</div>
<div class="cards">{cards}</div>
<h2>Equity</h2><div class="box">{equity_svg(trades)}</div>
{dd_html}
{extra_html}{sweep_html(sweep_rows)}
<h2>Assumptions</h2><ul>{''.join(f'<li>{html.escape(x)}</li>' for x in notes)}</ul>
<h2>Trades</h2><div class="box scroll"><table><thead><tr><th>#</th><th>Entry time</th><th>Side</th><th>Lots</th>
<th>Entry</th><th>Stop</th><th>Target</th><th>{"Trigger candle" if limit else "Entry candle" if momentum else "Signal candle"}</th><th>BE trigger</th><th>Exit</th><th>Result</th>
<th>R</th><th>P&amp;L $</th><th>Balance</th><th>Held</th>{"<th>Stretch candle</th><th>Stretch len</th><th>Entry level</th>" if momentum else ""}</tr></thead><tbody>{rows}</tbody></table></div>
</main></body></html>"""
    out = HERE / "backtest_report.html"
    out.write_text(page, encoding="utf-8")
    with open(HERE / "backtest_trades.csv", "w", newline="", encoding="utf-8") as f:
        if trades:
            w = csv.DictWriter(f, fieldnames=list(trades[0].keys()))
            w.writeheader()
            for t in trades:
                row = dict(t)
                for k in ("signal_time", "entry_time", "exit_time"):
                    row[k] = fmt_t(row[k])
                w.writerow(row)
    return out


vmin_g = 0.01
SKIP_TEXT = {"in_trade": "while a trade was open", "stop_already_hit": "price already past the stop at entry",
             "no_ticks": "at the end of the data", "balance": "account empty",
             "no_target": "no mitigation candle found", "target_already_hit": "no mitigation candle beyond the fill price",
             "low_rr": "planned reward below MIN_RR",
             "not_triggered": "stretch candles where no candle closed beyond the 50% level in time",
             "void": "setups void (neither order filled in time)"}


def sweep_html(rows):
    if not rows:
        return ""
    HL = ' style="background:#2a2e39"'
    body = "".join(
        f"<tr{HL if r['current'] else ''}><td>{r['k']:g} ATR{' (current)' if r['current'] else ''}</td>"
        f"<td>{r['signals']}</td><td>{r['trades']}</td><td>{r['win']:.1f}%</td><td>{r['tp']}/{r['sl']}/{r['be']}</td>"
        f"<td>{r['pf']}</td><td style='color:{'#26a69a' if r['net'] >= 0 else '#ef5350'}'>{'+' if r['net'] >= 0 else '-'}${abs(r['net']):.2f}</td>"
        f"<td>${r['dd']:.2f}</td></tr>" for r in rows)
    return ("<h2>Stretch threshold comparison</h2><div class='box'><table><thead><tr><th>Gap &ge;</th><th>Signals</th>"
            "<th>Trades</th><th>Win rate</th><th>TP/SL/BE</th><th>Profit factor</th><th>Net</th><th>Max DD</th></tr></thead>"
            f"<tbody>{body}</tbody></table><p style='color:#787b86;font-size:12px;margin:8px 4px 0'>Same data and costs, only the "
            "stretch threshold changes. Picking the best row on a few days of data overfits; prefer a threshold that "
            "works across several rows and several weeks.</p></div>")


def main():
    global vmin_g
    connect()
    symbol = find_symbol()
    info = mt5.symbol_info(symbol)
    contract = float(getattr(info, "trade_contract_size", 100) or 100)
    vmin = float(getattr(info, "volume_min", 0.01) or 0.01)
    vstep = float(getattr(info, "volume_step", 0.01) or 0.01)
    vmin_g = vmin
    ticks = load_ticks(symbol)
    mt5.shutdown()
    print(f"{len(ticks):,} ticks loaded. Building {CANDLE_SEC}s Heikin Ashi candles...")
    ha = build_ha(ticks)
    sweep_rows, extra = None, ""
    if STRATEGY == "limit":
        n = len(ha) - 1
        sig = stretch_signals(ha, n, STRETCH_ATR, BREAK_LEVEL, PULLBACKS, TREND_FILTER)
        print(f"{len(ha):,} candles, {len(sig)} stretch candles recorded. Simulating...")
        trades, skipped = simulate_limit(ticks, ha, sig, contract)
        rows = []
        for label, kw in [("limit + breakout", {}), ("limit only", {"use_limit": True, "use_breakout": False}),
                          ("breakout only", {"use_limit": False, "use_breakout": True}),
                          ("limit + breakout, stall exit " + ("off" if STALL_EXIT else "on"), {"stall": not STALL_EXIT})]:
            tr, _ = simulate_limit(ticks, ha, sig, contract, **kw)
            rows.append(stat_row(label, sig, tr, current=(label == "limit + breakout")))
        tr, _ = simulate_momentum(ticks, ha, sig, contract, "close", STALL_EXIT)
        rows.append(stat_row("previous: market entry after trigger, SL stretch candle, BE 30%", sig, tr))
        extra = table_html("Rule variants", rows, "Variant",
                           "Same stretch candles, data and costs. The last row is the previous momentum version for comparison.")
        rows = []
        for k in sorted(set(SWEEP) | {STRETCH_ATR}):
            sg = stretch_signals(ha, n, k, BREAK_LEVEL, PULLBACKS, TREND_FILTER)
            tr, _ = simulate_limit(ticks, ha, sg, contract)
            rows.append(stat_row(f"{k:g} ATR", sg, tr, current=(k == STRETCH_ATR)))
        extra += table_html("Stretch threshold comparison", rows, "Gap &ge;",
                            "Same rules, only the stretch threshold changes. Picking the best row on a few days of data "
                            "overfits; prefer settings that hold up across several rows and several weeks.")
    elif STRATEGY == "momentum":
        n = len(ha) - 1
        sig = stretch_signals(ha, n, STRETCH_ATR, BREAK_LEVEL, PULLBACKS, TREND_FILTER)
        print(f"{len(ha):,} candles, {len(sig)} stretch candles recorded. Simulating...")
        trades, skipped = simulate_momentum(ticks, ha, sig, contract)
        rows = []
        for mode in ("close", "touch"):
            for stall in (True, False):
                tr, _ = simulate_momentum(ticks, ha, sig, contract, mode, stall)
                rows.append(stat_row(f"entry on {mode}, stall exit {'on' if stall else 'off'}", sig, tr,
                                     current=(mode == ENTRY_MODE and stall == STALL_EXIT)))
        extra = table_html("Rule variants", rows, "Variant",
                           "Same stretch candles, data and costs; only the entry trigger and the stall exit change.")
        rows = []
        for k in sorted(set(SWEEP) | {STRETCH_ATR}):
            sg = stretch_signals(ha, n, k, BREAK_LEVEL, PULLBACKS, TREND_FILTER)
            tr, _ = simulate_momentum(ticks, ha, sg, contract)
            rows.append(stat_row(f"{k:g} ATR", sg, tr, current=(k == STRETCH_ATR)))
        extra += table_html("Stretch threshold comparison", rows, "Gap &ge;",
                            "Same rules, only the stretch threshold changes. Picking the best row on a few days of data "
                            "overfits; prefer settings that hold up across several rows and several weeks.")
    elif STRATEGY == "stretch":
        sig = stretch_signals(ha, len(ha) - 1, STRETCH_ATR, BREAK_LEVEL, PULLBACKS, TREND_FILTER)
        print(f"{len(ha):,} candles, {len(sig)} stretch reversal signals.")
        trades, skipped = simulate(ticks, ha, sig, contract, vmin, vstep)
        sweep_rows = []
        for k in sorted(set(SWEEP) | {STRETCH_ATR}):
            sg = stretch_signals(ha, len(ha) - 1, k, BREAK_LEVEL, PULLBACKS, TREND_FILTER)
            tr, _ = simulate(ticks, ha, sg, contract, vmin, vstep)
            x = stats(tr)
            sweep_rows.append({"k": k, "current": k == STRETCH_ATR, "signals": len(sg), "trades": len(tr),
                               "win": x.get("win_rate", 0), "tp": x.get("tp", 0), "sl": x.get("sl", 0), "be": x.get("be", 0),
                               "pf": ("∞" if math.isinf(x["pf"]) else f"{x['pf']:.2f}") if x else "-",
                               "net": x.get("net", 0.0), "dd": x.get("max_dd", 0.0)})
    else:
        sig = bos_signals(ha, len(ha) - 1, BREAK_LEVEL, TREND_FILTER)
        print(f"{len(ha):,} candles, {len(sig)} breaks of structure ({sum(s['flip'] for s in sig)} bias changes).")
        trades, skipped = simulate(ticks, ha, sig, contract, vmin, vstep, only_flips=SIGNALS == "flips")
    st = stats(trades)
    out = write_report(symbol, ticks, ha, sig, trades, skipped, st, contract, sweep_rows, extra)
    if st:
        print(f"\n{st['trades']} trades | {exits_text(st)} | win rate {st['win_rate']:.1f}%")
        print(f"Balance ${START_BALANCE:.2f} -> ${st['final']:.2f}  ({st['ret']:+.1f}%), max drawdown ${st['max_dd']:.2f}")
    else:
        print("\nNo trades in this period.")
    print(f"\nReport: {out}")
    webbrowser.open(out.as_uri())


if __name__ == "__main__":
    main()
