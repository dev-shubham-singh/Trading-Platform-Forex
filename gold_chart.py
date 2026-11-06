"""
MT5 chart server: any symbol, custom intervals, tick charts, Renko and Kagi. Opens in your browser.

READS PRICES ONLY. THIS SCRIPT PLACES NO ORDERS.

  - Symbols: everything your MT5 terminal offers (Market Watch symbols listed first)
  - Time intervals: any length, e.g. 5s, 20s, 1m, 7m, 2h, 1d
      * under a minute (or not a whole number of minutes) -> built from tick history
      * whole minutes / hours / days -> built from MT5 bar history (much longer history)
  - Tick intervals: e.g. 100t = one candle every 100 ticks
  - Renko (brick size) and Kagi (reversal amount), built from ticks
  - Prices are bid prices; times are broker server time.

Requirements: Windows, MT5 open and logged in,  pip install MetaTrader5 pandas numpy
Run:          python gold_chart.py      (close this window to stop)
"""

import json
import sys
import threading
import time
import webbrowser
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import numpy as np
import pandas as pd

try:
    import MetaTrader5 as mt5
except ImportError:
    print("MetaTrader5 library not found. Run:  pip install MetaTrader5 pandas numpy")
    input("Press Enter to exit")
    sys.exit(1)

# ---------------- settings ----------------
TICK_HISTORY_DAYS = 3    # days of ticks loaded for second / tick / Renko / Kagi charts
BAR_HISTORY = 20000      # bars shown for minute-and-up intervals
MAX_BASE_BARS = 99000    # most MT5 bars requested at once (MT5 'Max bars in chart' may cap this)
PORT = 8765
POLL_SEC = 0.25          # how often new ticks are pulled from MT5
MAX_BARS_SENT = 60000    # newest bars sent to the browser for tick-built charts
# ------------------------------------------

HERE = Path(__file__).resolve().parent
MT5_LOCK = threading.Lock()   # MT5 library calls are made one at a time


def fail(msg):
    print(msg)
    input("Press Enter to exit")
    sys.exit(1)


def utc_dt(ms):
    # MT5 timestamps are broker-server time; we pass that same clock back.
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)


def connect():
    if not mt5.initialize():
        fail(f"Could not connect to MetaTrader 5: {mt5.last_error()}\n"
             "Make sure MT5 is open and logged in, then run this again.")
    acc = mt5.account_info()
    if acc:
        print(f"Connected to MT5: account {acc.login} on {acc.server}")


def default_symbol():
    with MT5_LOCK:
        names = sorted((s.name for s in (mt5.symbols_get("*XAUUSD*") or [])), key=len)
    for n in names:
        with MT5_LOCK:
            if mt5.symbol_select(n, True):
                return n
    with MT5_LOCK:
        vis = [s.name for s in (mt5.symbols_get() or []) if s.visible]
    return vis[0] if vis else None


def symbol_list():
    with MT5_LOCK:
        syms = mt5.symbols_get() or []
    out = [{"name": s.name, "desc": getattr(s, "description", "") or "", "path": getattr(s, "path", "") or "",
            "mw": bool(s.visible)} for s in syms]
    out.sort(key=lambda x: (not x["mw"], x["name"]))
    return out


def ensure_symbol(sym):
    with MT5_LOCK:
        ok = mt5.symbol_select(sym, True)
        info = mt5.symbol_info(sym) if ok else None
    if not info:
        raise ValueError(f"Symbol {sym} is not available in MT5")
    return info


# ---------------- ticks ----------------
def fetch_ticks(sym, from_ms, to_ms=None):
    with MT5_LOCK:
        if to_ms is None:
            raw = mt5.copy_ticks_from(sym, utc_dt(from_ms), 500000, mt5.COPY_TICKS_INFO)
        else:
            raw = mt5.copy_ticks_range(sym, utc_dt(from_ms), utc_dt(to_ms), mt5.COPY_TICKS_INFO)
    if raw is None or len(raw) == 0:
        return np.empty(0, np.int64), np.empty(0)
    t = np.asarray(raw["time_msc"], dtype=np.int64)
    b = np.asarray(raw["bid"], dtype=float)
    keep = b > 0
    return t[keep], b[keep]


class TickStore:
    """All ticks of one symbol since TICK_HISTORY_DAYS ago, growing live. Never trimmed during a session."""

    def __init__(self, sym):
        self.sym, self.lock = sym, threading.Lock()
        self.t, self.b, self.n = np.empty(0, np.int64), np.empty(0), 0
        # order flow by the tick rule: a tick above the previous price is a buy, below is a sell, unchanged keeps
        # the last direction. sg = side per tick (+1 / -1 / 0 before the first move); cb / cs = running counts of
        # buys / sells (cb[k] = buys among the first k ticks), so any time range is two lookups.
        self.sg, self.cb, self.cs, self.lastsign = np.empty(0, np.int8), np.zeros(1, np.int64), np.zeros(1, np.int64), 0
        self.last_used = time.time()
        with MT5_LOCK:
            tick = mt5.symbol_info_tick(sym)
        latest = int(tick.time_msc) if tick and tick.time_msc else int(time.time() * 1000)
        start, day = latest - TICK_HISTORY_DAYS * 86_400_000, 86_400_000
        print(f"Loading {TICK_HISTORY_DAYS} days of {sym} ticks...")
        ts, bs = [], []
        while start < latest:
            end = min(start + day, latest)
            t, b = fetch_ticks(sym, start, end)
            ts.append(t); bs.append(b)
            start = end
        self._append(np.concatenate(ts) if ts else np.empty(0, np.int64), np.concatenate(bs) if bs else np.empty(0))
        print(f"  {self.n:,} ticks for {sym}")

    def _append(self, t, b):
        if len(t) == 0:
            return
        o = np.argsort(t, kind="stable")
        t, b = t[o], b[o]
        if self.n:
            keep = t > self.t[self.n - 1]
            t, b = t[keep], b[keep]
        if len(t) == 0:
            return
        need = self.n + len(t)
        if need > len(self.t):                         # grow capacity geometrically
            cap = max(need, int(len(self.t) * 1.5) + 100000)
            nt, nb, ns = np.empty(cap, np.int64), np.empty(cap), np.empty(cap, np.int8)
            ncb, ncs = np.zeros(cap + 1, np.int64), np.zeros(cap + 1, np.int64)
            nt[:self.n], nb[:self.n], ns[:self.n] = self.t[:self.n], self.b[:self.n], self.sg[:self.n]
            ncb[:self.n + 1], ncs[:self.n + 1] = self.cb[:self.n + 1], self.cs[:self.n + 1]
            self.t, self.b, self.sg, self.cb, self.cs = nt, nb, ns, ncb, ncs
        prev = self.b[self.n - 1] if self.n else b[0]
        d = np.sign(np.diff(np.concatenate(([prev], b)))).astype(np.int8)
        idx = np.where(d != 0, np.arange(len(d)), -1)
        np.maximum.accumulate(idx, out=idx)
        side = np.where(idx >= 0, d[np.maximum(idx, 0)], self.lastsign).astype(np.int8)
        self.lastsign = int(side[-1])
        self.t[self.n:need], self.b[self.n:need], self.sg[self.n:need] = t, b, side
        self.cb[self.n + 1:need + 1] = self.cb[self.n] + np.cumsum(side > 0)
        self.cs[self.n + 1:need + 1] = self.cs[self.n] + np.cumsum(side < 0)
        self.n = need

    def update(self):
        last = int(self.t[self.n - 1]) if self.n else int(time.time() * 1000) - 60000
        t, b = fetch_ticks(self.sym, last)
        with self.lock:
            self._append(t, b)

    def view(self, start=0):
        with self.lock:
            n = self.n
            return self.t[start:n], self.b[start:n], n

    def flow_view(self):
        with self.lock:
            n = self.n
            return self.t[:n], self.b[:n], self.sg[:n], self.cb[:n + 1], self.cs[:n + 1]


STORES, STORES_LOCK = {}, threading.Lock()


def store_for(sym):
    with STORES_LOCK:
        st = STORES.get(sym)
    if st is None:
        st = TickStore(sym)
        with STORES_LOCK:
            st = STORES.setdefault(sym, st)
    st.last_used = time.time()
    return st


# ---------------- bar builders (incremental) ----------------
# Every builder keeps rows [id, open, high, low, close, real_time_seconds, tick_volume] and a 'done' tick position.
class TimeBuilder:
    """Fixed-length time candles built from ticks (any number of seconds)."""

    def __init__(self, sec):
        self.ms, self.rows, self.done = int(sec * 1000), [], 0

    def feed(self, t, b):
        if len(t) == 0:
            return
        bucket = (t // self.ms) * self.ms
        g = pd.DataFrame({"k": bucket, "p": b}).groupby("k", sort=True)["p"]
        agg = zip(g.first().index.to_numpy(), g.first().to_numpy(), g.max().to_numpy(), g.min().to_numpy(),
                  g.last().to_numpy(), g.size().to_numpy())
        for k, o, h, l, c, v in agg:
            sec = int(k) // 1000
            r = self.rows[-1] if self.rows else None
            if r and r[0] == sec:
                r[2] = max(r[2], h); r[3] = min(r[3], l); r[4] = c; r[6] += int(v)
            elif not r or sec > r[0]:
                self.rows.append([sec, o, h, l, c, sec, int(v)])


class TickBuilder:
    """One candle every N ticks."""

    def __init__(self, n):
        self.n, self.rows, self.done = max(1, int(n)), [], 0
        self.cnt = 0                                   # ticks in the newest (forming) candle

    def feed(self, t, b):
        if len(t) == 0:
            return
        g = np.arange(self.done, self.done + len(t)) // self.n       # global candle number
        df = pd.DataFrame({"g": g, "p": b, "t": t}).groupby("g", sort=True)
        agg = zip(df["p"].first().index.to_numpy(), df["p"].first().to_numpy(), df["p"].max().to_numpy(),
                  df["p"].min().to_numpy(), df["p"].last().to_numpy(), df["t"].first().to_numpy(), df["p"].size().to_numpy())
        for k, o, h, l, c, t0, v in agg:
            cid = int(k) + 1
            r = self.rows[-1] if self.rows else None
            if r and r[0] == cid:
                r[2] = max(r[2], h); r[3] = min(r[3], l); r[4] = c; r[6] += int(v)
            else:
                self.rows.append([cid, o, h, l, c, int(t0) / 1000, int(v)])
        self.cnt = (self.done + len(t)) % self.n or self.n


class RenkoBuilder:
    """Classic Renko on bid: a brick per `box` move; a reversal needs two boxes. Bricks have no wicks."""

    def __init__(self, box):
        self.box, self.rows, self.done = float(box), [], 0
        self.anchor, self.dir = None, 0                # anchor = close of the last brick
        self.pend = 0                                  # ticks since the last brick (its volume)

    def feed(self, t, b):
        box = self.box
        rows = self.rows
        for i in range(len(b)):
            p = b[i]
            self.pend += 1
            if self.anchor is None:
                self.anchor = round(p / box) * box
                continue
            a, n0 = self.anchor, len(rows)
            if p >= a + box and self.dir >= 0 or (self.dir < 0 and p >= a + 2 * box):
                start = a if self.dir >= 0 else a + box
                while p >= start + box:
                    rows.append([len(rows) + 1, start, start + box, start, start + box, int(t[i]) / 1000, 0])
                    start += box
                self.anchor, self.dir = start, 1
            elif p <= a - box and self.dir <= 0 or (self.dir > 0 and p <= a - 2 * box):
                start = a if self.dir <= 0 else a - box
                while p <= start - box:
                    rows.append([len(rows) + 1, start, start, start - box, start - box, int(t[i]) / 1000, 0])
                    start -= box
                self.anchor, self.dir = start, -1
            if len(rows) > n0:
                rows[n0][6] = self.pend; self.pend = 0


class KagiBuilder:
    """Kagi on bid: the line follows price until it reverses by `rev`, then a new column starts.
    Each column = [id, start, high, low, end]. The newest column keeps extending live."""

    def __init__(self, rev):
        self.rev, self.rows, self.done = float(rev), [], 0
        self.dir, self.base = 0, None

    def feed(self, t, b):
        rev, rows = self.rev, self.rows
        for i in range(len(b)):
            p = b[i]
            ts = int(t[i]) / 1000
            if self.base is None:
                self.base = p
                continue
            if not rows:
                if abs(p - self.base) >= rev:
                    self.dir = 1 if p > self.base else -1
                    rows.append([1, self.base, max(p, self.base), min(p, self.base), p, ts, 1])
                continue
            r = rows[-1]
            if self.dir > 0:
                if p > r[4]:
                    r[4] = r[2] = p; r[6] += 1
                elif r[4] - p >= rev:
                    rows.append([len(rows) + 1, r[4], r[4], p, p, ts, 1]); self.dir = -1
                else:
                    r[6] += 1
            else:
                if p < r[4]:
                    r[4] = r[3] = p; r[6] += 1
                elif p - r[4] >= rev:
                    rows.append([len(rows) + 1, r[4], p, r[4], p, ts, 1]); self.dir = 1
                else:
                    r[6] += 1


BUILDERS, BUILDERS_LOCK = {}, threading.Lock()


def builder_for(sym, kind, p):
    key = (sym, kind, p)
    with BUILDERS_LOCK:
        bd = BUILDERS.get(key)
        if bd is None:
            bd = {"time": TimeBuilder, "tick": TickBuilder, "renko": RenkoBuilder, "kagi": KagiBuilder}[kind](p)
            bd.lock = threading.Lock()
            BUILDERS[key] = bd
            if len(BUILDERS) > 24:                     # forget the oldest charts
                BUILDERS.pop(next(iter(BUILDERS)))
    st = store_for(sym)
    with bd.lock:
        t, b, n = st.view(bd.done)
        bd.feed(t, b)
        bd.done = n
    return bd


# ---------------- minute+ intervals from MT5 bar history ----------------
def base_tf(sec):
    if sec % 86400 == 0:
        return mt5.TIMEFRAME_D1, 86400
    if sec % 3600 == 0:
        return mt5.TIMEFRAME_H1, 3600
    return mt5.TIMEFRAME_M1, 60


def rate_bars(sym, sec, since=None):
    tf, bsec = base_tf(sec)
    mult = sec // bsec
    count = min(MAX_BASE_BARS, BAR_HISTORY * mult) if since is None else mult * 3 + 2
    r = None
    with MT5_LOCK:
        while count >= 10:
            r = mt5.copy_rates_from_pos(sym, tf, 0, int(count))
            if r is not None and len(r):
                break
            count //= 2
    if r is None or not len(r):
        return []
    vol = r["tick_volume"] if "tick_volume" in (r.dtype.names or ()) else np.zeros(len(r))
    df = pd.DataFrame({"k": (r["time"].astype(np.int64) // sec) * sec, "o": r["open"], "h": r["high"],
                       "l": r["low"], "c": r["close"], "v": vol})
    g = df.groupby("k", sort=True)
    a = pd.DataFrame({"o": g["o"].first(), "h": g["h"].max(), "l": g["l"].min(), "c": g["c"].last(), "v": g["v"].sum()})
    if since is not None:
        a = a[a.index >= int(since)]
    return [[int(k), float(o), float(h), float(l), float(c), int(k), int(v)]
            for k, o, h, l, c, v in zip(a.index, a["o"], a["h"], a["l"], a["c"], a["v"])]


def suggest_box(sym, point):
    """A starting Renko brick / Kagi reversal: about half the average 1-minute range, rounded."""
    with MT5_LOCK:
        r = mt5.copy_rates_from_pos(sym, mt5.TIMEFRAME_M1, 0, 300)
    if r is None or not len(r):
        return point * 100
    rng = float(np.median(r["high"] - r["low"])) / 2 or point * 10
    mag = 10 ** np.floor(np.log10(rng))
    for m in (1, 2, 5, 10):
        if rng <= m * mag:
            return float(round(m * mag, 10))
    return float(rng)


def live_loop():
    while True:
        try:
            with STORES_LOCK:
                active = [s for s in STORES.values() if time.time() - s.last_used < 120]
            for st in active:
                st.update()
        except Exception as e:                         # keep the chart alive through hiccups
            print("Live update problem (retrying):", e)
        time.sleep(POLL_SEC)


# ---------------- broker clock ----------------
# MT5 times are the broker's server clock. Its offset from real UTC (Exness: usually 0) is measured from a fresh
# tick: tick time - real time, rounded to 15 minutes. Remembered in broker_tz.json for weekends / closed markets.
TZ_FILE = HERE / "broker_tz.json"
_tz = {"offset": None, "at": 0.0}


def broker_offset(sym=None):
    if _tz["offset"] is not None and time.time() - _tz["at"] < 600:
        return _tz["offset"]
    found = None
    try:
        with MT5_LOCK:
            names = ([sym] if sym else []) + [s.name for s in (mt5.symbols_get() or []) if getattr(s, "visible", False)][:80]
            for name in names:
                tk = mt5.symbol_info_tick(name)
                if not tk or not tk.time:
                    continue
                diff = tk.time - time.time()
                r = round(diff / 900) * 900
                if abs(diff - r) < 90 and abs(r) <= 14 * 3600:          # a tick from the last minute or so
                    found = int(r)
                    break
    except Exception as e:
        print("Could not read the broker clock:", e)
    if found is not None:
        if found != _tz["offset"]:
            try:
                TZ_FILE.write_text(json.dumps({"offset": found}))
            except Exception:
                pass
        _tz["offset"], _tz["at"] = found, time.time()
        return found
    if _tz["offset"] is None:
        try:
            _tz["offset"] = int(json.loads(TZ_FILE.read_text())["offset"])
        except Exception:
            _tz["offset"] = 0
    _tz["at"] = time.time()
    return _tz["offset"]


def status(sym):
    with MT5_LOCK:
        tk = mt5.symbol_info_tick(sym)
    return {"bid": tk.bid, "ask": tk.ask, "server_ms": tk.time_msc} if tk else {}


# ---------------- order flow (tick rule) ----------------
def nice_step(x, point):
    """Round a price step to 1 / 2 / 2.5 / 5 x 10^k, never below the symbol's point."""
    if not x > 0:
        return point
    mag = 10 ** np.floor(np.log10(x))
    for m in (1, 2, 2.5, 5, 10):
        if x <= m * mag:
            return float(max(point, round(m * mag, 10)))
    return float(max(point, x))


def bar_bounds(sym, kind, p, since=None):
    """(ids, start_ms, end_ms) of the chart's bars: time bars run [start, start + interval); tick / Kagi bars
    run until the next bar starts; a Renko brick holds the ticks after the previous brick up to its own."""
    rows, _ = get_bars(sym, kind, p, None if kind in ("renko",) else since)
    if not rows:
        return np.empty(0, np.int64), np.empty(0, np.int64), np.empty(0, np.int64)
    ids = np.array([r[0] for r in rows], dtype=float)
    rt = np.array([r[5] for r in rows], dtype=float) * 1000
    now = time.time() * 1000 + 86_400_000 * 365
    if kind == "time":
        st = rt.astype(np.int64); en = st + int(round(float(p) * 1000))
    elif kind == "renko":
        st = np.concatenate(([0.0], rt[:-1])).astype(np.int64) + 1; en = rt.astype(np.int64) + 1
        if since is not None:
            keep = ids >= since; ids, st, en = ids[keep], st[keep], en[keep]
    else:
        st = rt.astype(np.int64); en = np.concatenate((rt[1:], [now])).astype(np.int64)
    return ids, st, en


def flow_delta(sym, kind, p, since=None):
    st = store_for(sym)
    ids, a, b = bar_bounds(sym, kind, p, since)
    t, _, _, cb, cs = st.flow_view()
    if len(t) == 0 or len(ids) == 0:
        return {"ids": [], "buy": [], "sell": []}
    i0, i1 = np.searchsorted(t, a, "left"), np.searchsorted(t, b, "left")
    keep = a >= t[0]                                    # bars older than the tick history have no flow data
    buy, sell = (cb[i1] - cb[i0])[keep], (cs[i1] - cs[i0])[keep]
    ids = ids[keep]
    return {"ids": [int(x) if float(x).is_integer() else float(x) for x in ids], "buy": buy.tolist(), "sell": sell.tolist()}


def flow_profile(sym, t0, t1, rows=24, step=None):
    """Volume at price (ticks, split buy / sell) between two real times in seconds."""
    st = store_for(sym)
    t, b, sg, _, _ = st.flow_view()
    i0, i1 = np.searchsorted(t, int(t0 * 1000), "left"), np.searchsorted(t, int(t1 * 1000), "left")
    if i1 <= i0:
        return {"ticks": 0}
    pb, ps = b[i0:i1], sg[i0:i1]
    point = symbol_point(sym)
    lo, hi = float(pb.min()), float(pb.max())
    step = float(step) if step else nice_step((hi - lo) / max(1, int(rows)), point)
    k = np.floor(pb / step + 1e-9).astype(np.int64)
    base = int(k.min()); k -= base
    n = int(k.max()) + 1
    buy = np.bincount(k, weights=(ps > 0), minlength=n).astype(np.int64)
    sell = np.bincount(k, weights=(ps < 0), minlength=n).astype(np.int64)
    return {"ticks": int(i1 - i0), "base": base, "step": step, "buy": buy.tolist(), "sell": sell.tolist(),
            "from": float(t[i0]) / 1000, "to": float(t[i1 - 1]) / 1000, "covered": bool(t0 * 1000 >= t[0])}


def flow_footprint(sym, starts, ends, step):
    """Per bar: buy / sell ticks at each price row of size `step`."""
    st = store_for(sym)
    t, b, sg, cb, cs = st.flow_view()
    out = []
    if len(t) == 0:
        return out
    a = np.searchsorted(t, (np.asarray(starts) * 1000).astype(np.int64), "left")
    z = np.searchsorted(t, (np.asarray(ends) * 1000).astype(np.int64), "left")
    for s0, i0, i1 in zip(starts, a, z):
        if i1 <= i0 or s0 * 1000 < t[0]:
            out.append(None); continue
        k = np.floor(b[i0:i1] / step + 1e-9).astype(np.int64)
        base = int(k.min()); k -= base
        n = int(k.max()) + 1
        sgn = sg[i0:i1]
        out.append([base, np.bincount(k, weights=(sgn > 0), minlength=n).astype(int).tolist(),
                    np.bincount(k, weights=(sgn < 0), minlength=n).astype(int).tolist()])
    return out


_POINTS = {}


def symbol_point(sym):
    if sym not in _POINTS:
        with MT5_LOCK:
            info = mt5.symbol_info(sym)
        _POINTS[sym] = float(info.point) if info and info.point else 0.01
    return _POINTS[sym]


def get_bars(sym, kind, p, since):
    if kind == "time":
        sec = float(p)
        if sec >= 60 and sec % 60 == 0:
            return rate_bars(sym, int(sec), since), {}
        bd = builder_for(sym, "time", sec)
    else:
        bd = builder_for(sym, kind, float(p) if kind in ("renko", "kagi") else int(p))
    rows = bd.rows
    if since is not None:
        lo, hi = 0, len(rows)                          # rows are sorted by id: binary search
        while lo < hi:
            mid = (lo + hi) // 2
            if rows[mid][0] < since:
                lo = mid + 1
            else:
                hi = mid
        out = [list(r) for r in rows[lo:]]
    else:
        out = [list(r) for r in rows[-MAX_BARS_SENT:]]
    extra = {"cnt": bd.cnt} if kind == "tick" else {}
    return out, extra


# ---------------- chart library (kept next to this file so the page loads instantly and offline) ----------------
LIB_FILE = HERE / "lightweight-charts.js"
LIB_URL = "https://unpkg.com/lightweight-charts@4.2.0/dist/lightweight-charts.standalone.production.js"


def ensure_library():
    """Download the chart library once. If it can't be downloaded the page falls back to the internet copy."""
    try:
        if LIB_FILE.exists() and LIB_FILE.stat().st_size > 100_000:
            return
        import urllib.request
        print("Saving the chart library locally (one time)...")
        with urllib.request.urlopen(LIB_URL, timeout=30) as r:
            data = r.read()
        if len(data) > 100_000 and b"createChart" in data:
            LIB_FILE.write_bytes(data)
    except Exception as e:
        print("  could not save it (the page will load it from the internet):", e)


def library_bytes():
    try:
        return LIB_FILE.read_bytes() if LIB_FILE.exists() and LIB_FILE.stat().st_size > 100_000 else None
    except Exception:
        return None


def make_handler(default_sym):
    page = (HERE / "chart.html").read_bytes()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def send(self, body, ctype="application/json", code=200):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def js(self, obj, code=200):
            self.send(json.dumps(obj).encode(), code=code)

        def do_GET(self):
            url = urlparse(self.path)
            q = {k: v[0] for k, v in parse_qs(url.query).items()}
            try:
                if url.path == "/lightweight-charts.js":
                    lib = library_bytes()
                    if lib is None:
                        return self.send_error(404)
                    self.send_response(200)
                    self.send_header("Content-Type", "application/javascript")
                    self.send_header("Cache-Control", "max-age=604800")
                    self.end_headers()
                    self.wfile.write(lib)
                    return
                if url.path in ("/", "/index.html"):
                    return self.send(page, "text/html; charset=utf-8")
                if url.path == "/api/symbols":
                    return self.js({"default": default_sym, "symbols": symbol_list(), "srvOffset": broker_offset(default_sym)})
                sym = q.get("symbol") or default_sym
                if url.path == "/api/meta":
                    info = ensure_symbol(sym)
                    return self.js({"symbol": sym, "digits": info.digits, "point": info.point,
                                    "desc": getattr(info, "description", ""), "box": suggest_box(sym, info.point),
                                    "srvOffset": broker_offset(sym)})
                if url.path == "/api/delta":
                    ensure_symbol(sym)
                    kind = q.get("kind", "time")
                    since = q.get("since")
                    return self.js(flow_delta(sym, kind, float(q.get("p", "15")), float(since) if since else None))
                if url.path == "/api/profile":
                    ensure_symbol(sym)
                    return self.js(flow_profile(sym, float(q["t0"]), float(q["t1"]), int(q.get("rows", 24)),
                                                float(q["step"]) if q.get("step") else None))
                if url.path == "/api/footprint":
                    ensure_symbol(sym)
                    s_ = [float(x) for x in q.get("s", "").split(",") if x]
                    e_ = [float(x) for x in q.get("e", "").split(",") if x]
                    if len(s_) != len(e_) or len(s_) > 600:
                        return self.js({"error": "bad footprint request"}, 400)
                    step = float(q.get("step", "0")) or symbol_point(sym) * 100
                    return self.js({"step": step, "bars": flow_footprint(sym, s_, e_, step)})
                if url.path == "/api/bars":
                    ensure_symbol(sym)
                    kind = q.get("kind", "time")
                    if kind not in ("time", "tick", "renko", "kagi"):
                        return self.js({"error": "unknown chart kind"}, 400)
                    p = float(q.get("p", "15"))
                    if not p > 0:
                        return self.js({"error": "interval must be above zero"}, 400)
                    since = q.get("since")
                    rows, extra = get_bars(sym, kind, p, float(since) if since is not None else None)
                    return self.js({"symbol": sym, "kind": kind, "p": p, "bars": rows, "status": status(sym), **extra})
                self.send_error(404)
            except Exception as e:
                self.js({"error": str(e)}, 500)

    return Handler


def main():
    connect()
    sym = default_symbol()
    if not sym:
        fail("No symbols found in MT5. Add some to Market Watch and try again.")
    print(f"Default symbol: {sym}")
    threading.Thread(target=live_loop, daemon=True).start()
    server = ThreadingHTTPServer(("127.0.0.1", PORT), make_handler(sym))
    url = f"http://127.0.0.1:{PORT}"
    print(f"\nChart running at {url}  (keep this window open, Ctrl+C to stop)")
    ensure_library()
    webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        mt5.shutdown()
        print("Stopped.")


if __name__ == "__main__":
    main()
