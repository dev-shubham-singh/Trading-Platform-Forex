"""Telegram alerts for the chart servers (used by gold_chart.py and india_chart.py).

The chart page decides WHEN to alert and makes the screenshot; this file only talks to Telegram from your own PC:
  - telegram_config.json  {"token": "...", "chat_id": 123456}   (private, never committed)
  - GET  /api/tg          -> status (never returns the token)
  - POST /api/tgsetup     {"token"}      checks the bot token, saves it, looks for your chat
  - POST /api/tgchat      {}             looks for your chat again (after you pressed Start in the bot)
  - POST /api/tgsend      {"text", "image"(base64 png, optional)}
  - POST /api/tgclear     {}             forgets the token
Only the chart page of the same server may use the POST endpoints (same checks as the order endpoints).
"""
import base64
import json
import os
import secrets
import threading
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

API_BASE = os.environ.get("TG_API_BASE", "https://api.telegram.org")   # tests point this at a fake server
MAX_BODY = 8_000_000


class Telegram:
    def __init__(self, here, port):
        self.file = Path(here) / "telegram_config.json"
        self.port = port
        self.token_page = secrets.token_hex(16)      # the chart page gets it from GET /api/tg; other websites cannot read it
        self.lock = threading.Lock()
        self.cfg = {}
        self.bot = ""
        self.last_err = ""
        try:
            self.cfg = json.loads(self.file.read_text(encoding="utf-8"))
        except Exception:
            self.cfg = {}

    # ----- telegram api -----
    def _save(self):
        self.file.write_text(json.dumps(self.cfg), encoding="utf-8")

    def _call(self, method, params=None, files=None, token=None):
        token = token or self.cfg.get("token")
        if not token:
            raise RuntimeError("no bot token saved")
        url = f"{API_BASE}/bot{token}/{method}"
        if files:
            boundary = uuid.uuid4().hex
            parts = []
            for k, v in (params or {}).items():
                parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
            for k, (fn, data, ctype) in files.items():
                parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"; filename="{fn}"\r\n'
                             f'Content-Type: {ctype}\r\n\r\n'.encode() + data + b"\r\n")
            parts.append(f"--{boundary}--\r\n".encode())
            req = urllib.request.Request(url, b"".join(parts), {"Content-Type": f"multipart/form-data; boundary={boundary}"})
        else:
            req = urllib.request.Request(url, json.dumps(params or {}).encode(), {"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                out = json.loads(r.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as e:
            try:
                out = json.loads(e.read().decode("utf-8", "replace"))
            except Exception:
                out = {"ok": False, "description": f"HTTP {e.code}"}
        if not out.get("ok"):
            raise RuntimeError(out.get("description") or "Telegram refused the request")
        return out.get("result")

    # ----- setup -----
    def find_chat(self):
        """Newest private chat that wrote to the bot (you pressed Start / sent 'hi'). Saves its id."""
        ups = self._call("getUpdates", {"limit": 50, "timeout": 0}) or []
        for u in reversed(ups):
            m = u.get("message") or u.get("edited_message") or u.get("channel_post") or {}
            ch = m.get("chat") or {}
            if ch.get("id") is not None:
                self.cfg["chat_id"] = ch["id"]
                self.cfg["chat_name"] = ch.get("first_name") or ch.get("title") or ch.get("username") or ""
                self._save()
                return True
        return False

    def setup(self, token):
        token = (token or "").strip()
        if ":" not in token or len(token) < 20:
            return {"error": "That does not look like a bot token (it looks like 123456789:ABC...). Copy it from @BotFather."}
        me = self._call("getMe", token=token)
        self.cfg = {"token": token}
        self.bot = me.get("username", "")
        self.cfg["bot"] = self.bot
        self._save()
        try:
            self.find_chat()
        except Exception:
            pass
        return self.status()

    def status(self):
        return {"configured": bool(self.cfg.get("token")), "chat": self.cfg.get("chat_id") is not None,
                "bot": self.cfg.get("bot", ""), "name": self.cfg.get("chat_name", ""), "token": self.token_page, "err": self.last_err}

    def send(self, text, png=None):
        if not self.cfg.get("token"):
            return {"error": "Telegram is not set up yet"}
        if self.cfg.get("chat_id") is None:
            return {"error": "No chat yet: open your bot in Telegram, press Start, then press 'Find my chat'"}
        cid = self.cfg["chat_id"]
        text = (text or "")[:3900]
        try:
            with self.lock:
                if png:
                    self._call("sendPhoto", {"chat_id": cid, "caption": text[:1000]}, files={"photo": ("chart.png", png, "image/png")})
                    if len(text) > 1000:
                        self._call("sendMessage", {"chat_id": cid, "text": text})
                else:
                    self._call("sendMessage", {"chat_id": cid, "text": text})
                time.sleep(0.4)                       # Telegram allows about one message a second per chat
            self.last_err = ""
            return {"ok": True}
        except Exception as e:
            self.last_err = str(e)[:200]
            return {"error": self.last_err}

    # ----- http glue -----
    def get(self, path):
        return self.status() if path == "/api/tg" else None

    def post(self, path, headers, raw_body):
        """-> (dict, status_code) for /api/tg* paths, or None when the path is not ours."""
        if not path.startswith("/api/tg") or path == "/api/tg":
            return None
        host, origin = headers.get("Host", ""), headers.get("Origin")
        ok_hosts = (f"127.0.0.1:{self.port}", f"localhost:{self.port}")
        if (host not in ok_hosts or (origin and origin not in tuple("http://" + h for h in ok_hosts))
                or not headers.get("Content-Type", "").startswith("application/json")
                or not secrets.compare_digest(headers.get("X-Chart-Token", ""), self.token_page)):
            return {"error": "refused: not from this chart page"}, 403
        try:
            body = json.loads(raw_body or b"{}")
            if path == "/api/tgsetup":
                return self.setup(body.get("token")), 200
            if path == "/api/tgchat":
                ok = self.find_chat()
                return ({**self.status()} if ok else {**self.status(), "error": "No message found yet. Open your bot in Telegram, press Start (or send 'hi'), then try again."}), 200
            if path == "/api/tgclear":
                self.cfg = {}
                try:
                    self.file.unlink()
                except Exception:
                    pass
                return self.status(), 200
            if path == "/api/tgsend":
                png = base64.b64decode(body["image"]) if body.get("image") else None
                return self.send(str(body.get("text", "")), png), 200
        except Exception as e:
            return {"error": str(e)[:300]}, 200
        return {"error": "unknown"}, 404
