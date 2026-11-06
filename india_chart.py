"""
Indian market chart server (Fyers API): the same chart, indicators and tools as the gold chart.

READS PRICES ONLY. THIS SCRIPT PLACES NO ORDERS.

  - Instruments: everything in the Fyers symbol master - NSE / BSE stocks and indices, NSE / BSE futures and
    options, MCX commodities, NSE currency. Search by name in the symbol box.
  - Minute-and-up intervals (1m, 5m, 15m, 1h, 1d, custom minutes): history from Fyers, kept live from the feed.
  - Second intervals, tick charts, Renko and Kagi: built from live ticks. Every tick of every symbol on your
    watchlist (fyers_watchlist.txt) and every symbol you open is saved in the fyers_ticks folder, so this history
    grows each day the app runs during market hours. (With FYERS Prime, 5s-45s history also comes from Fyers.)
  - Times are India time (IST). Hourly / 4h candles, pivots and candle high/low start at the session open
    (9:15 for NSE / BSE, 9:00 for MCX and currency).

Setup: put your Fyers app ID and secret in fyers_config.json (see the file). Each morning the first start opens
the Fyers login page; the login is valid until 6:30 AM the next day.
Run:   start_india_chart.bat   (close the window to stop)
"""

import csv
import hashlib
import io
import json
import re
import secrets
import sys
import threading
import time
import urllib.request
import webbrowser
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

import numpy as np
import pandas as pd

try:
    from fyers_apiv3 import fyersModel
    from fyers_apiv3.FyersWebsocket import data_ws
except ImportError:
    print("Fyers library not found. Run:  pip install fyers-apiv3 pandas numpy")
    input("Press Enter to exit")
    sys.exit(1)

# ---------------- settings ----------------
PORT = 8766
TICK_DAYS = 5              # days of recorded ticks loaded for second / tick / Renko / Kagi charts
MAX_SUBS = 100             # Fyers Standard plan: 100 live symbols (Prime: 5000)
WATCH_MAX = 60             # most symbols kept on the recording watchlist
HIST_REFRESH_SEC = 180     # re-download recent history of open minute charts (fills any feed gaps)
MAX_BARS_SENT = 60000
# ------------------------------------------

HERE = Path(__file__).resolve().parent
CONFIG = HERE / "fyers_config.json"
TOKEN_FILE = HERE / "fyers_token.json"
WATCH_FILE = HERE / "fyers_watchlist.txt"
TICK_DIR = HERE / "fyers_ticks"
SYM_DIR = HERE / "fyers_symbols"
LOG_DIR = HERE / "fyers_logs"
IST = 19800                                     # seconds; all chart times are shown in India time
SYM_FILES = {"NSE_CM": "NSE stocks & indices", "NSE_FO": "NSE F&O", "BSE_CM": "BSE stocks & indices",
             "BSE_FO": "BSE F&O", "MCX_COM": "MCX commodities", "NSE_CD": "NSE currency", "NSE_COM": "NSE commodities"}
SESSION_OPEN = {"MCX_COM": 9 * 3600, "NSE_CD": 9 * 3600, "NSE_COM": 9 * 3600}     # others 9:15
DEFAULT_WATCH = ["NSE:NIFTY50-INDEX", "NSE:NIFTYBANK-INDEX"]
SEC_RES = [45, 30, 15, 10, 5]                   # Fyers seconds resolutions (Prime plan)
MIN_RES = {1: "1", 2: "2", 3: "3", 5: "5", 10: "10", 15: "15", 20: "20", 30: "30", 60: "60", 120: "120", 240: "240"}


def fail(msg):
    print(msg)
    input("Press Enter to exit")
    sys.exit(1)


def now_ist():
    return datetime.now(timezone.utc) + timedelta(seconds=IST)


def safe_name(sym):
    return re.sub(r"[^A-Za-z0-9_.-]", "_", sym)


# ---------------- config and login ----------------
def load_config():
    if not CONFIG.exists():
        CONFIG.write_text(json.dumps({
            "client_id": "PUT-YOUR-APP-ID-HERE-100",
            "secret_key": "PUT-YOUR-SECRET-HERE",
            "redirect_uri": f"http://127.0.0.1:{PORT}/fyers"}, indent=2))
    try:
        cfg = json.loads(CONFIG.read_text())
    except Exception as e:
        fail(f"fyers_config.json could not be read ({e}). Fix it and run again.")
    if "PUT-YOUR" in cfg.get("client_id", "PUT-YOUR") or "PUT-YOUR" in cfg.get("secret_key", "PUT-YOUR"):
        fail(f"Open {CONFIG.name} in Notepad and fill in your Fyers App ID and Secret ID\n"
             f"(from the Fyers API dashboard), save it, and run this again.\n"
             f"The redirect URL in that file must be the same as the one set on your Fyers app.")
    return cfg


class Auth:
    def __init__(self, cfg):
        self.cfg, self.token, self.client, self.state = cfg, None, None, None
        self.lock = threading.Lock()

    @staticmethod
    def last_reset():
        """Fyers tokens expire every day at 6:30 AM IST: the most recent 6:30 AM, as a UTC timestamp."""
        n = now_ist()
        r = n.replace(hour=6, minute=30, second=0, microsecond=0)
        if n < r:
            r -= timedelta(days=1)
        return (r - timedelta(seconds=IST)).timestamp()

    def try_saved(self):
        try:
            d = json.loads(TOKEN_FILE.read_text())
            if d.get("client_id") != self.cfg["client_id"] or d.get("created", 0) < self.last_reset():
                return False
            return self.use(d["token"], check=True)
        except Exception:
            return False

    def use(self, token, check=False):
        LOG_DIR.mkdir(exist_ok=True)
        client = fyersModel.FyersModel(client_id=self.cfg["client_id"], is_async=False, token=token,
                                       log_path=str(LOG_DIR))
        if check:
            try:
                r = client.get_profile()
                if not isinstance(r, dict) or r.get("s") != "ok":
                    return False
            except Exception:
                return False
        self.token, self.client = token, client
        return True

    def session(self):
        return fyersModel.SessionModel(client_id=self.cfg["client_id"], secret_key=self.cfg["secret_key"],
                                       redirect_uri=self.cfg["redirect_uri"], response_type="code",
                                       grant_type="authorization_code", state=self.state)

    def login_url(self):
        self.state = secrets.token_hex(8)
        return self.session().generate_authcode()

    def finish(self, auth_code):
        s = self.session()
        s.set_token(auth_code)
        r = s.generate_token()
        if not isinstance(r, dict) or not r.get("access_token"):
            raise ValueError(f"Fyers did not return a token: {r}")
        token = r["access_token"]
        if not self.use(token):
            raise ValueError("Token could not be used")
        TOKEN_FILE.write_text(json.dumps({"client_id": self.cfg["client_id"], "token": token, "created": time.time()}))
        return token

    @property
    def ok(self):
        return self.client is not None


AUTH = None
API_LOCK, API_LAST = threading.Lock(), [0.0]


def api_history(data):
    """Fyers data calls are limited (Standard: 5 per second, 50 per minute) - space them out."""
    with API_LOCK:
        wait = 0.35 - (time.time() - API_LAST[0])
        if wait > 0:
            time.sleep(wait)
        try:
            r = AUTH.client.history(data=data)
        finally:
            API_LAST[0] = time.time()
    return r if isinstance(r, dict) else {"s": "error", "message": str(r)}


# ---------------- symbol master ----------------
SYMS, SYM_INDEX = {}, []          # name -> info ; list of (search text, info)


def download_masters():
    SYM_DIR.mkdir(exist_ok=True)
    today = now_ist().date()
    for f in SYM_FILES:
        p = SYM_DIR / f"{f}.csv"
        fresh = p.exists() and p.stat().st_size > 0 and \
            (datetime.fromtimestamp(p.stat().st_mtime, timezone.utc) + timedelta(seconds=IST)).date() == today
        if fresh:
            continue
        try:
            print(f"Downloading symbol list {f}...")
            with urllib.request.urlopen(f"https://public.fyers.in/sym_details/{f}.csv", timeout=60) as r:
                data = r.read()
            if data:
                p.write_bytes(data)
        except Exception as e:
            print(f"  could not download {f} ({e}); using the old copy if there is one")


def load_masters():
    tick_re = re.compile(r"^(NSE|BSE|MCX):\S+$")
    for f, label in SYM_FILES.items():
        p = SYM_DIR / f"{f}.csv"
        if not p.exists():
            continue
        col = None
        with open(p, newline="", encoding="utf-8", errors="replace") as fh:
            for row in csv.reader(fh):
                if col is None:                        # find the "Symbol ticker" column (NSE:SBIN-EQ style)
                    col = next((i for i, v in enumerate(row) if tick_re.match(v.strip())), None)
                    if col is None:
                        continue                       # header row
                if len(row) <= col:
                    continue
                name = row[col].strip()
                if not tick_re.match(name):
                    continue
                try:
                    tick = float(row[4]) if len(row) > 4 and row[4] else 0.05
                except ValueError:
                    tick = 0.05
                try:
                    lot = int(float(row[3])) if len(row) > 3 and row[3] else 1
                except ValueError:
                    lot = 1
                try:
                    exp = int(float(row[8])) if len(row) > 8 and row[8] else 0
                except ValueError:
                    exp = 0
                info = {"name": name, "desc": row[1].strip() if len(row) > 1 else "", "seg": f, "path": label,
                        "tick": tick if tick > 0 else 0.05, "lot": lot, "exp": exp}
                SYMS[name] = info
                SYM_INDEX.append(((name + " " + info["desc"]).lower(), info))
    print(f"{len(SYMS):,} instruments loaded")


def search(q, limit=80):
    words = q.lower().split()
    if not words:
        return []
    hits = [info for text, info in SYM_INDEX if all(w in text for w in words)]
    w0 = words[0]

    def rank(i):
        short = i["name"].split(":", 1)[1].lower()
        return (0 if short == w0 or short.startswith(w0 + "-") else 1 if short.startswith(w0) else 2,
                0 if i["seg"].endswith("_CM") else 1, i["exp"] or 0, len(i["name"]))
    hits.sort(key=rank)
    return [{"name": i["name"], "desc": i["desc"], "path": i["path"], "mw": False} for i in hits[:limit]]


def ensure_symbol(sym):
    info = SYMS.get(sym)
    if not info:
        raise ValueError(f"{sym} is not in the Fyers symbol list (expired contract or a typo?)")
    return info


def anchor_of(sym):
    return SESSION_OPEN.get(SYMS[sym]["seg"], 9 * 3600 + 15 * 60) if sym in SYMS else 9 * 3600 + 15 * 60


def digits_of(tick):
    s = f"{tick:.6f}".rstrip("0").rstrip(".")
    return len(s.split(".")[1]) if "." in s else 0


# ---------------- watchlist ----------------
WATCH_LOCK = threading.Lock()


def read_watch():
    if not WATCH_FILE.exists():
        WATCH_FILE.write_text("# Symbols recorded tick by tick while this app runs (one per line).\n"
                              "# Symbols you open on the chart are added automatically. Delete lines to stop recording.\n"
                              + "\n".join(DEFAULT_WATCH) + "\n")
    out = []
    for line in WATCH_FILE.read_text().splitlines():
        s = line.strip()
        if s and not s.startswith("#") and s not in out:
            out.append(s)
    return out


def add_watch(sym):
    with WATCH_LOCK:
        cur = read_watch()
        if sym in cur or len(cur) >= WATCH_MAX:
            return
        with open(WATCH_FILE, "a") as fh:
            fh.write(sym + "\n")


# ---------------- tick recording and storage ----------------
class Recorder:
    """Appends every tick to fyers_ticks/<IST date>/<symbol>.csv as 'utc_ms,price'."""

    def __init__(self):
        self.files, self.lock = {}, threading.Lock()
        threading.Thread(target=self._flusher, daemon=True).start()

    def write(self, sym, ms, price):
        day = datetime.fromtimestamp(ms / 1000 + IST, timezone.utc).strftime("%Y-%m-%d")
        with self.lock:
            key = (sym, day)
            fh = self.files.get(key)
            if fh is None:
                for k in [k for k in self.files if k[0] == sym]:      # a new day: close yesterday's file
                    self.files.pop(k).close()
                d = TICK_DIR / day
                d.mkdir(parents=True, exist_ok=True)
                fh = self.files[key] = open(d / f"{safe_name(sym)}.csv", "a", buffering=1 << 16)
            fh.write(f"{ms},{price}\n")

    def flush(self):
        with self.lock:
            for fh in self.files.values():
                fh.flush()

    def _flusher(self):
        while True:
            time.sleep(1)
            try:
                self.flush()
            except Exception:
                pass


REC = None


def read_recorded(sym):
    """Recorded ticks of the last TICK_DAYS recorded days, oldest first (UTC ms, price)."""
    if not TICK_DIR.exists():
        return np.empty(0, np.int64), np.empty(0)
    days = sorted(d for d in TICK_DIR.iterdir() if d.is_dir() and (d / f"{safe_name(sym)}.csv").exists())[-TICK_DAYS:]
    ts, bs = [], []
    for d in days:
        try:
            a = pd.read_csv(d / f"{safe_name(sym)}.csv", header=None, names=["t", "p"], on_bad_lines="skip")
            a = a.apply(pd.to_numeric, errors="coerce").dropna()        # a half-written last line is dropped
            ts.append(a["t"].to_numpy(np.int64)); bs.append(a["p"].to_numpy(float))
        except Exception as e:
            print(f"Could not read {d.name}/{sym} ticks: {e}")
    if not ts:
        return np.empty(0, np.int64), np.empty(0)
    return np.concatenate(ts), np.concatenate(bs)


class TickStore:
    """Ticks of one symbol (recorded days + live), times in IST milliseconds."""

    def __init__(self, sym):
        self.sym, self.lock = sym, threading.Lock()
        self.t, self.b, self.n = np.empty(0, np.int64), np.empty(0), 0
        self.last_used = time.time()
        if REC:
            REC.flush()
        t, b = read_recorded(sym)
        self.append_many(t + IST * 1000, b)
        if self.n:
            print(f"  {self.n:,} recorded ticks for {sym}")

    def append_many(self, t, b):
        if len(t) == 0:
            return
        o = np.argsort(t, kind="stable")
        t, b = t[o], b[o]
        with self.lock:
            if self.n:
                keep = t > self.t[self.n - 1]
                t, b = t[keep], b[keep]
            if len(t) == 0:
                return
            need = self.n + len(t)
            if need > len(self.t):
                cap = max(need, int(len(self.t) * 1.5) + 100000)
                nt, nb = np.empty(cap, np.int64), np.empty(cap)
                nt[:self.n], nb[:self.n] = self.t[:self.n], self.b[:self.n]
                self.t, self.b = nt, nb
            self.t[self.n:need], self.b[self.n:need] = t, b
            self.n = need

    def add(self, ms_ist, price):
        with self.lock:
            if self.n and ms_ist <= self.t[self.n - 1]:
                return
            if self.n >= len(self.t):
                cap = int(len(self.t) * 1.5) + 100000
                nt, nb = np.empty(cap, np.int64), np.empty(cap)
                nt[:self.n], nb[:self.n] = self.t[:self.n], self.b[:self.n]
                self.t, self.b = nt, nb
            self.t[self.n], self.b[self.n] = ms_ist, price
            self.n += 1

    def view(self, start=0):
        with self.lock:
            n = self.n
            return self.t[start:n], self.b[start:n], n

    def first_at_or_after(self, ms_ist):
        with self.lock:
            return int(np.searchsorted(self.t[:self.n], ms_ist, side="left"))


STORES, STORES_LOCK = {}, threading.Lock()


def store_for(sym):
    with STORES_LOCK:
        st = STORES.get(sym)
        if st is None:
            st = STORES[sym] = TickStore(sym)
    st.last_used = time.time()
    return st


# ---------------- live feed ----------------
class Feed:
    def __init__(self):
        self.ws, self.connected, self.subs = None, False, set()
        self.lock = threading.Lock()
        self.last = {}            # sym -> (ltp, volume, utc_ms of last stored tick)
        self.quote = {}           # sym -> {"bid", "ask", "ltp"}
        self.err = ""

    def start(self):
        LOG_DIR.mkdir(exist_ok=True)
        self.ws = data_ws.FyersDataSocket(
            access_token=f"{AUTH.cfg['client_id']}:{AUTH.token}", log_path=str(LOG_DIR), litemode=False,
            write_to_file=False, reconnect=True, on_connect=self.on_open, on_close=self.on_close,
            on_error=self.on_error, on_message=self.on_msg, reconnect_retry=50)
        self.ws.connect()

    def on_open(self, *a):
        self.connected = True
        with self.lock:
            subs = list(self.subs)
        if subs:
            for i in range(0, len(subs), 50):
                self.ws.subscribe(symbols=subs[i:i + 50], data_type="SymbolUpdate")
        print(f"Live feed connected ({len(subs)} symbols)")

    def on_close(self, *a):
        self.connected = False
        print("Live feed closed:", a[0] if a else "")

    def on_error(self, *a):
        self.err = str(a[0]) if a else "error"
        print("Live feed problem:", self.err)

    def want(self, syms):
        new = []
        with self.lock:
            for s in syms:
                if s not in self.subs and s in SYMS and len(self.subs) < MAX_SUBS:
                    self.subs.add(s); new.append(s)
        if new and self.connected and self.ws:
            try:
                self.ws.subscribe(symbols=new, data_type="SymbolUpdate")
            except Exception as e:
                print("Subscribe problem:", e)
        return all(s in self.subs for s in syms if s in SYMS)

    def on_msg(self, m):
        try:
            if isinstance(m, str):
                m = json.loads(m)
            if not isinstance(m, dict) or m.get("type") not in ("sf", "if"):
                return
            sym, ltp = m.get("symbol"), m.get("ltp")
            if not sym or ltp is None:
                return
            ltp = float(ltp)
            q = self.quote.setdefault(sym, {})
            q["ltp"] = ltp
            q["bid"] = float(m.get("bid_price") or ltp)
            q["ask"] = float(m.get("ask_price") or ltp)
            vol = m.get("vol_traded_today")
            feed_s = int(m.get("exch_feed_time") or m.get("last_traded_time") or time.time())
            prev = self.last.get(sym)
            if prev is None:                               # first message: skip a stale snapshot we already have
                prev = self.last[sym] = (None, None, self.last_recorded_ms(sym))
            if prev[0] == ltp and prev[1] == vol:
                return                                     # only a bid/ask change, no trade
            now_ms = int(time.time() * 1000)
            ms = min(max(now_ms, feed_s * 1000), feed_s * 1000 + 999)
            if ms <= prev[2]:
                if feed_s * 1000 + 999 < prev[2] - 2000:    # an old snapshot / replay
                    self.last[sym] = (ltp, vol, prev[2])
                    return
                ms = prev[2] + 1
            self.last[sym] = (ltp, vol, ms)
            REC.write(sym, ms, ltp)
            with STORES_LOCK:
                st = STORES.get(sym)
            if st:
                st.add(ms + IST * 1000, ltp)
        except Exception as e:
            print("Tick problem:", e)

    @staticmethod
    def last_recorded_ms(sym):
        try:
            days = sorted(d for d in TICK_DIR.iterdir() if (d / f"{safe_name(sym)}.csv").exists())
            if not days:
                return 0
            with open(days[-1] / f"{safe_name(sym)}.csv", "rb") as fh:
                fh.seek(0, 2); size = fh.tell(); fh.seek(max(0, size - 200))
                lines = fh.read().decode(errors="ignore").strip().splitlines()
            return int(lines[-1].split(",")[0]) if lines else 0
        except Exception:
            return 0


FEED = None


# ---------------- bar builders (incremental, times in IST) ----------------
def bucket(t, sec, a, day):
    """Start of the candle holding time t; t, sec, session open a and day length all in the same unit.
    Candles restart at each session open, so intervals that don't divide a day (7m, 50m) start at 9:15 daily."""
    if sec >= day or day % sec == 0:
        return ((t - a) // sec) * sec + a
    d = ((t - a) // day) * day + a
    return d + ((t - d) // sec) * sec


# rows: [id, open, high, low, close, real_time_seconds]; id = start time for time charts, a counter otherwise
class TimeBuilder:
    """Fixed-length time candles from ticks. Candles start at the session open (anchor) for intervals that
    don't divide it, so a 1h candle runs 9:15-10:15 like on exchange charts."""

    def __init__(self, sec, anchor):
        self.ms, self.a, self.rows, self.done = int(sec * 1000), int(anchor) * 1000, [], 0

    def bucket_ms(self, t):
        return bucket(t, self.ms, self.a, 86_400_000)

    def feed(self, t, b):
        if len(t) == 0:
            return
        bucket = self.bucket_ms(t)
        g = pd.DataFrame({"k": bucket, "p": b}).groupby("k", sort=True)["p"]
        agg = zip(g.first().index.to_numpy(), g.first().to_numpy(), g.max().to_numpy(), g.min().to_numpy(), g.last().to_numpy())
        for k, o, h, l, c in agg:
            sec = int(k) // 1000
            r = self.rows[-1] if self.rows else None
            if r and r[0] == sec:
                r[2] = max(r[2], h); r[3] = min(r[3], l); r[4] = c
            elif not r or sec > r[0]:
                self.rows.append([sec, o, h, l, c, sec])


class TickBuilder:
    def __init__(self, n):
        self.n, self.rows, self.done, self.cnt = max(1, int(n)), [], 0, 0

    def feed(self, t, b):
        if len(t) == 0:
            return
        g = np.arange(self.done, self.done + len(t)) // self.n
        df = pd.DataFrame({"g": g, "p": b, "t": t}).groupby("g", sort=True)
        agg = zip(df["p"].first().index.to_numpy(), df["p"].first().to_numpy(), df["p"].max().to_numpy(),
                  df["p"].min().to_numpy(), df["p"].last().to_numpy(), df["t"].first().to_numpy())
        for k, o, h, l, c, t0 in agg:
            cid = int(k) + 1
            r = self.rows[-1] if self.rows else None
            if r and r[0] == cid:
                r[2] = max(r[2], h); r[3] = min(r[3], l); r[4] = c
            else:
                self.rows.append([cid, o, h, l, c, int(t0) / 1000])
        self.cnt = (self.done + len(t)) % self.n or self.n


class RenkoBuilder:
    def __init__(self, box):
        self.box, self.rows, self.done = float(box), [], 0
        self.anchor, self.dir = None, 0

    def feed(self, t, b):
        box, rows = self.box, self.rows
        for i in range(len(b)):
            p = b[i]
            if self.anchor is None:
                self.anchor = round(p / box) * box
                continue
            a = self.anchor
            if p >= a + box and self.dir >= 0 or (self.dir < 0 and p >= a + 2 * box):
                start = a if self.dir >= 0 else a + box
                while p >= start + box:
                    rows.append([len(rows) + 1, start, start + box, start, start + box, int(t[i]) / 1000])
                    start += box
                self.anchor, self.dir = start, 1
            elif p <= a - box and self.dir <= 0 or (self.dir > 0 and p <= a - 2 * box):
                start = a if self.dir <= 0 else a - box
                while p <= start - box:
                    rows.append([len(rows) + 1, start, start, start - box, start - box, int(t[i]) / 1000])
                    start -= box
                self.anchor, self.dir = start, -1


class KagiBuilder:
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
                    rows.append([1, self.base, max(p, self.base), min(p, self.base), p, ts])
                continue
            r = rows[-1]
            if self.dir > 0:
                if p > r[4]:
                    r[4] = r[2] = p
                elif r[4] - p >= rev:
                    rows.append([len(rows) + 1, r[4], r[4], p, p, ts]); self.dir = -1
            else:
                if p < r[4]:
                    r[4] = r[3] = p
                elif p - r[4] >= rev:
                    rows.append([len(rows) + 1, r[4], p, r[4], p, ts]); self.dir = 1


# ---------------- history from Fyers ----------------
SECONDS_OK = [True]          # becomes False if the account has no seconds history (Standard plan)


def base_res(sec):
    """(Fyers resolution, its length in seconds) used to build an interval of `sec` seconds, or None."""
    if sec % 86400 == 0:
        return "D", 86400
    if sec % 60 == 0:
        m = sec // 60
        for r in sorted(MIN_RES, reverse=True):
            if m % r == 0 and (r < 60 or m % 60 == 0 or r == m):
                return MIN_RES[r], r * 60
        return "1", 60
    if SECONDS_OK[0]:
        for r in SEC_RES:
            if sec % r == 0:
                return f"{r}S", r
    return None


def fetch_history(sym, res, rsec, days_back, until=None):
    """Candles [utc_sec, o, h, l, c] for the last `days_back` days, in request-sized chunks."""
    chunk = 366 if res == "D" else 100 if rsec >= 60 else 5
    end = int(until or time.time())
    if res != "D":
        end = (end // rsec) * rsec - 1          # leave out the forming candle
    start_all = end - days_back * 86400
    out, to = [], end
    while to > start_all:
        frm = max(start_all, to - chunk * 86400)
        r = api_history({"symbol": sym, "resolution": res, "date_format": "0",
                         "range_from": str(int(frm)), "range_to": str(int(to)), "cont_flag": "1"})
        if r.get("s") == "ok":
            out = (r.get("candles") or []) + out
        elif r.get("s") == "no_data":
            pass
        else:
            msg = str(r.get("message") or r)
            if rsec < 60:
                SECONDS_OK[0] = False
                print("Seconds history is not available on this Fyers plan; second charts use recorded ticks.")
                return None
            raise ValueError(f"Fyers history error for {sym}: {msg}")
        to = frm - 1
        if rsec < 60 and len(out) == 0:
            break
    seen, rows = set(), []
    for c in out:
        t = int(c[0])
        if t not in seen:
            seen.add(t); rows.append(c)
    rows.sort(key=lambda c: c[0])
    return rows


def history_days(res, rsec):
    if res == "D":
        return 3 * 366
    if rsec < 60:
        return 5
    return {60: 90, 120: 100, 180: 100, 300: 100}.get(rsec, 300 if rsec <= 1800 else 360)


class HistBuilder(TimeBuilder):
    """A time chart that starts from Fyers history and continues from live ticks."""

    def __init__(self, sym, sec, anchor, res, rsec):
        super().__init__(sec, anchor)
        self.sym, self.sec, self.res, self.rsec = sym, sec, res, rsec
        self.refreshed, self.ok = 0, False
        self.rebuild(full=True)

    def seed(self, candles):
        rows = []
        for c in candles:
            t = int(c[0]) + IST
            if self.res == "D":                       # daily candles: the session start of that day
                t = (t // 86400) * 86400 + self.a // 1000
            k = bucket(t * 1000, self.ms, self.a, 86_400_000) // 1000
            o, h, l, cl = float(c[1]), float(c[2]), float(c[3]), float(c[4])
            r = rows[-1] if rows else None
            if r and r[0] == k:
                r[2] = max(r[2], h); r[3] = min(r[3], l); r[4] = cl
            else:
                rows.append([k, o, h, l, cl, k])
        return rows

    def rebuild(self, full=False):
        days = history_days(self.res, self.rsec) if full or not self.rows else 3
        candles = fetch_history(self.sym, self.res, self.rsec, days)
        self.refreshed = time.time()
        if candles is None:
            return
        new = self.seed(candles)
        if not full and self.rows and new:
            keep = [r for r in self.rows if r[0] < new[0][0]]
            new = keep + new
        self.rows = new
        self.ok = True
        st = store_for(self.sym)
        start = (self.rows[-1][0] if self.rows else 0) * 1000
        self.done = st.first_at_or_after(start)


BUILDERS, BUILDERS_LOCK = {}, threading.Lock()


def builder_for(sym, kind, p):
    key = (sym, kind, p)
    with BUILDERS_LOCK:
        bd = BUILDERS.get(key)
    if bd is None:
        if kind == "time":
            sec = int(p) if float(p).is_integer() else p
            br = base_res(sec) if isinstance(sec, int) else None
            bd = None
            if br:
                bd = HistBuilder(sym, sec, anchor_of(sym), *br)
                if not bd.ok:
                    bd = None
            if bd is None:
                bd = TimeBuilder(p, anchor_of(sym))
        else:
            bd = {"tick": TickBuilder, "renko": RenkoBuilder, "kagi": KagiBuilder}[kind](p)
        bd.lock = threading.Lock()
        with BUILDERS_LOCK:
            bd = BUILDERS.setdefault(key, bd)
            if len(BUILDERS) > 24:
                BUILDERS.pop(next(iter(BUILDERS)))
    st = store_for(sym)
    with bd.lock:
        if isinstance(bd, HistBuilder) and time.time() - bd.refreshed > HIST_REFRESH_SEC:
            try:
                bd.rebuild()
            except Exception as e:
                print("History refresh problem:", e)
                bd.refreshed = time.time()
        t, b, n = st.view(bd.done)
        bd.feed(t, b)
        bd.done = n
    return bd


def suggest_box(sym, tick):
    try:
        c = fetch_history(sym, "1", 60, 4)
        if c:
            rng = float(np.median([x[2] - x[3] for x in c[-300:]])) / 2
        else:
            raise ValueError
    except Exception:
        return tick * 20
    rng = rng or tick * 10
    mag = 10 ** np.floor(np.log10(rng))
    for m in (1, 2, 5, 10):
        if rng <= m * mag:
            return float(max(tick, round(m * mag, 10)))
    return float(rng)


def status(sym):
    q = FEED.quote.get(sym) if FEED else None
    srv = int(time.time() * 1000) + IST * 1000
    if not q:
        return {"server_ms": srv}
    return {"bid": q["bid"], "ask": q["ask"], "server_ms": srv}


def get_bars(sym, kind, p, since):
    FEED.want([sym])
    bd = builder_for(sym, kind, p if kind != "tick" else int(p))
    rows = bd.rows
    if since is not None:
        lo, hi = 0, len(rows)
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


# ---------------- web server ----------------
LOGIN_PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>Fyers login</title>
<style>body{background:#131722;color:#d1d4dc;font:15px sans-serif;display:flex;justify-content:center;padding-top:12vh}
.c{max-width:560px;padding:0 16px} a.b{display:inline-block;background:#2962ff;color:#fff;padding:10px 18px;border-radius:6px;
text-decoration:none;font-weight:600} input{width:100%;padding:8px;margin:8px 0;background:#1e222d;color:#d1d4dc;
border:1px solid #363a45;border-radius:4px} button{padding:8px 14px} .m{color:#868993;font-size:13px} .e{color:#ef5350}</style>
</head><body><div class="c"><h2>Log in to Fyers</h2>
<p>Fyers needs a fresh login every day (it expires at 6:30 AM).</p>
<p><a class="b" href="/login">Log in with Fyers</a></p>
<p class="e">%ERR%</p>
<p class="m">If after logging in the browser shows an error page instead of coming back here, copy the whole address
from the address bar (it contains <code>auth_code=</code>), paste it below and press Continue.</p>
<form action="/fyers-paste" method="get"><input name="url" placeholder="Paste the address with auth_code=... here">
<button>Continue</button></form></div></body></html>"""


def make_handler():
    page = (HERE / "chart.html").read_bytes()
    cb_path = urlparse(AUTH.cfg["redirect_uri"]).path or "/"
    state = {"err": ""}

    def finish_login(code):
        AUTH.finish(code)
        FEED.start()
        FEED.want(read_watch())

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

        def redirect(self, to):
            self.send_response(302)
            self.send_header("Location", to)
            self.end_headers()

        def do_GET(self):
            url = urlparse(self.path)
            q = {k: v[0] for k, v in parse_qs(url.query).items()}
            try:
                if url.path == "/login":
                    return self.redirect(AUTH.login_url())
                if url.path in (cb_path, "/fyers-paste") and (url.path != "/" or "auth_code" in q):
                    if url.path == "/fyers-paste":
                        q = {k: v[0] for k, v in parse_qs(urlparse(q.get("url", "")).query).items()}
                    code = q.get("auth_code")
                    if not code:
                        state["err"] = "No auth_code found. Log in again."
                        return self.redirect("/")
                    try:
                        finish_login(code)
                        state["err"] = ""
                    except Exception as e:
                        state["err"] = f"Login failed: {e}"
                    return self.redirect("/")
                if url.path in ("/", "/index.html"):
                    if not AUTH.ok:
                        return self.send(LOGIN_PAGE.replace("%ERR%", state["err"]).encode(), "text/html; charset=utf-8")
                    return self.send(page, "text/html; charset=utf-8")
                if not AUTH.ok:
                    return self.js({"error": "Not logged in to Fyers. Refresh the page and log in."}, 401)
                if url.path == "/api/symbols":
                    watch = read_watch()
                    lst = [{"name": s, "desc": SYMS[s]["desc"], "path": SYMS[s]["path"], "mw": True}
                           for s in watch if s in SYMS]
                    dflt = lst[0]["name"] if lst else "NSE:NIFTY50-INDEX"
                    return self.js({"default": dflt, "symbols": lst, "search": True, "source": "Fyers", "srvOffset": IST,
                                    "watchLabel": "Watchlist (recorded)"})
                if url.path == "/api/search":
                    return self.js({"symbols": search(q.get("q", ""))})
                sym = q.get("symbol") or "NSE:NIFTY50-INDEX"
                if url.path == "/api/meta":
                    info = ensure_symbol(sym)
                    if not FEED.want([sym]):
                        return self.js({"error": f"Live symbol limit ({MAX_SUBS}) reached. Remove symbols from "
                                                 f"{WATCH_FILE.name} and restart."}, 400)
                    add_watch(sym)
                    return self.js({"symbol": sym, "digits": digits_of(info["tick"]), "point": info["tick"],
                                    "desc": info["desc"] + (f"  ·  lot {info['lot']}" if info["lot"] > 1 else ""),
                                    "box": suggest_box(sym, info["tick"]), "anchor": anchor_of(sym), "srvOffset": IST})
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
    global AUTH, FEED, REC
    cfg = load_config()
    AUTH, FEED, REC = Auth(cfg), Feed(), Recorder()
    download_masters()
    load_masters()
    if not SYMS:
        fail("No instruments could be loaded. Check your internet connection and run again.")
    server = ThreadingHTTPServer(("127.0.0.1", PORT), make_handler())
    url = f"http://127.0.0.1:{PORT}"
    if AUTH.try_saved():
        print("Using today's saved Fyers login.")
        FEED.start()
        FEED.want(read_watch())
    else:
        print("Opening the Fyers login page in your browser...")
    print(f"\nChart running at {url}  (keep this window open while the market is open, Ctrl+C to stop)")
    webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        if REC:
            REC.flush()
        print("Stopped.")


if __name__ == "__main__":
    main()
