"""ATAS receiver: order flow from the ATAS app on the owner's computer, joined into the one account.

ATAS has no cloud API. Its code runs only inside the ATAS desktop app, so a small indicator
(tools/atas/JarvisFeed.cs) runs there and posts one summary per finished bar to POST /api/atas/feed:

  {"symbol": "ES", "timeframe": "M1",
   "bar": {"time": "...", "open", "high", "low", "close", "volume", "delta", "cum_delta",
           "buy_volume", "sell_volume", "poc", "max_delta", "min_delta"},
   "big_trades": [{"price", "size", "side"}],          optional
   "account": {"balance", "pnl", "open_pnl", "positions": [{"symbol", "side", "size", "pnl"}]}}   optional

The feed token is ATAS_TOKEN (at least 32 characters) when it is set on Render. Without it, the token is derived from
DASHBOARD_PASSWORD (an HMAC, so the password itself never leaves the app), which means nothing has to be set up.
The One account page hands out a one-line installer (Mac Terminal or Windows PowerShell) through a short-lived code:
the installer builds the indicator on the owner's computer and saves the URL and token there, so nothing is typed into
ATAS. The receiver only stores what it is sent; it never places orders.
"""

from __future__ import annotations

import hashlib
import hmac
import math
import re
import secrets
import threading
import time
from pathlib import Path

MIN_TOKEN = 32
FRESH_SECONDS = 180  # a feed older than this shows as stale
KEEP_BARS = 120
MAX_SYMBOLS = 30
BAR_KEYS = ("open", "high", "low", "close", "volume", "delta", "cum_delta", "buy_volume", "sell_volume", "poc",
            "max_delta", "min_delta")


class FeedError(ValueError):
    pass


CODE_SECONDS = 1800  # an install line works for 30 minutes
INSTALL_DIR = Path(__file__).resolve().parent / "atas_install"
_HOST = re.compile(r"^[A-Za-z0-9.-]{1,253}(:[0-9]{1,5})?$")


def token(env) -> str:
    """ATAS_TOKEN when set (32+ characters), else a token derived from DASHBOARD_PASSWORD, else "" (feed off)."""
    own = env.get("ATAS_TOKEN", "")
    if len(own) >= MIN_TOKEN:
        return own
    pw = env.get("DASHBOARD_PASSWORD", "")
    if pw:
        return hmac.new(pw.encode(), b"jarvisfeed-atas-v1", hashlib.sha256).hexdigest()
    return ""


def enabled(env) -> bool:
    return len(token(env)) >= MIN_TOKEN


def app_url(host: str | None, proto: str | None) -> str | None:
    """The app's own address from the request, checked so nothing odd can land inside an installer script."""
    host = (host or "").strip()
    if not _HOST.fullmatch(host):
        return None
    proto = (proto or "").split(",")[0].strip().lower()
    if proto not in ("http", "https"):
        local = host.split(":")[0] in ("localhost", "127.0.0.1")
        proto = "http" if local else "https"
    return f"{proto}://{host}"


def installer(kind: str, url: str, feed_token: str) -> str:
    """The Mac (sh) or Windows (ps1) installer with the app URL, the token and the indicator source filled in."""
    name = {"sh": "install.sh", "ps1": "install.ps1"}[kind]
    text = (INSTALL_DIR / name).read_text(encoding="utf-8")
    source = (INSTALL_DIR / "JarvisFeed.cs").read_text(encoding="utf-8").rstrip("\n")
    bridge = (INSTALL_DIR / "McpBridgeStrategy.cs").read_text(encoding="utf-8").rstrip("\n")
    # values are checked: url by app_url, token is hex or the owner's own ATAS_TOKEN (no quotes allowed)
    if any(c in feed_token for c in "'\"`$\\\n\r"):
        raise FeedError("ATAS_TOKEN can't contain quotes, $, backslashes or line breaks")
    text = text.replace("__APP_URL__", url).replace("__TOKEN__", feed_token)
    if kind == "ps1":
        text = text.replace("\n", "\r\n")
        source = source.replace("\n", "\r\n")
        bridge = bridge.replace("\n", "\r\n")
    return text.replace("__JARVISFEED_CS__", source).replace("__MCPBRIDGE_CS__", bridge)


class InstallCodes:
    """Short-lived codes so the one-line installer can fetch the token without a password in the line."""

    def __init__(self, clock=time.time, ttl: float = CODE_SECONDS):
        self.clock = clock
        self.ttl = ttl
        self.lock = threading.Lock()
        self.codes: dict[str, float] = {}

    def issue(self) -> str:
        now = self.clock()
        code = secrets.token_urlsafe(18)
        with self.lock:
            self.codes = {c: t for c, t in self.codes.items() if t > now}
            if len(self.codes) >= 20:  # keep the newest few
                for c in sorted(self.codes, key=self.codes.get)[: len(self.codes) - 19]:
                    del self.codes[c]
            self.codes[code] = now + self.ttl
        return code

    def valid(self, code: str | None) -> bool:
        if not code:
            return False
        now = self.clock()
        with self.lock:
            for c, t in self.codes.items():
                if t > now and hmac.compare_digest(c.encode(), code.encode()):
                    return True
        return False


def _num(v, name: str) -> float | None:
    if v is None or v == "":
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        raise FeedError(f"{name} must be a number") from None
    if not math.isfinite(f):
        raise FeedError(f"{name} must be a finite number")
    return f


def _symbol(s) -> str:
    s = "".join(c for c in str(s or "").upper() if c.isalnum() or c in "._-/")[:24]
    if not s:
        raise FeedError("symbol required")
    return s


class AtasFeed:
    def __init__(self, clock=time.time):
        self.clock = clock
        self.lock = threading.Lock()
        self.books: dict[str, dict] = {}
        self.account: dict | None = None
        self.last = 0.0
        self.posts = 0
        self.hello: dict | None = None  # the installer said hi from the owner's computer

    def say_hello(self, body) -> dict:
        system = str((body or {}).get("installer") or "") if isinstance(body, dict) else ""
        with self.lock:
            self.hello = {"system": system if system in ("mac", "windows") else "computer", "at": self.clock()}
            return dict(self.hello)

    def ingest(self, body: dict) -> dict:
        if not isinstance(body, dict):
            raise FeedError("send a JSON object")
        sym = _symbol(body.get("symbol"))
        bar_in = body.get("bar") or {}
        if not isinstance(bar_in, dict):
            raise FeedError("bar must be an object")
        bar = {k: _num(bar_in.get(k), k) for k in BAR_KEYS}
        if bar["close"] is None:
            raise FeedError("bar.close required")
        bar["time"] = str(bar_in.get("time") or "")[:32]
        big = []
        for t in (body.get("big_trades") or [])[:50]:
            if isinstance(t, dict):
                side = "buy" if str(t.get("side", "")).lower() in ("buy", "ask", "b") else "sell"
                big.append({"price": _num(t.get("price"), "price"), "size": _num(t.get("size"), "size"), "side": side})
        now = self.clock()
        with self.lock:
            if sym not in self.books and len(self.books) >= MAX_SYMBOLS:
                raise FeedError(f"at most {MAX_SYMBOLS} symbols")
            b = self.books.setdefault(sym, {"symbol": sym, "bars": [], "big_trades": []})
            b["timeframe"] = str(body.get("timeframe") or "")[:8]
            if b["bars"] and b["bars"][-1]["time"] and b["bars"][-1]["time"] == bar["time"]:
                b["bars"][-1] = bar  # the same bar sent again: replace it
            else:
                b["bars"] = (b["bars"] + [bar])[-KEEP_BARS:]
            b["big_trades"] = (b["big_trades"] + big)[-50:]
            b["updated"] = now
            acc = body.get("account")
            if isinstance(acc, dict):
                self.account = self._account(acc, now)
            self.last = now
            self.posts += 1
        return {"symbol": sym, "bars": len(b["bars"]), "account": self.account is not None}

    @staticmethod
    def _account(acc: dict, now: float) -> dict:
        rows = []
        for p in (acc.get("positions") or [])[:50]:
            if not isinstance(p, dict):
                continue
            size = _num(p.get("size"), "size") or 0.0
            rows.append({"symbol": _symbol(p.get("symbol")),
                         "side": "buy" if (str(p.get("side", "")).lower() in ("buy", "long") or size > 0) else "sell",
                         "size": abs(size), "pnl": _num(p.get("pnl"), "pnl") or 0.0,
                         "price": _num(p.get("price"), "price")})
        return {"balance": _num(acc.get("balance"), "balance"), "pnl": _num(acc.get("pnl"), "pnl"),
                "open_pnl": _num(acc.get("open_pnl"), "open_pnl"), "positions": rows, "updated": now,
                "name": str(acc.get("name") or "ATAS")[:40]}

    def view(self, symbol: str) -> dict | None:
        """Order-flow lean for one symbol from the last bars: +1 buyers in control, -1 sellers."""
        with self.lock:
            b = self.books.get(symbol.upper())
            if not b or not b["bars"]:
                return None
            bars = b["bars"][-5:]
            vol = sum(x["volume"] or 0 for x in bars)
            delta = sum(x["delta"] or 0 for x in bars)
            lean = max(-1.0, min(1.0, delta / vol * 4)) if vol else 0.0
            return {"symbol": b["symbol"], "lean": round(lean, 3), "delta": delta, "volume": vol,
                    "close": bars[-1]["close"], "poc": bars[-1]["poc"], "fresh": self.clock() - b["updated"] < FRESH_SECONDS}

    def status(self, env) -> dict:
        now = self.clock()
        with self.lock:
            books = [{"symbol": b["symbol"], "timeframe": b.get("timeframe"), "last": b["bars"][-1] if b["bars"] else None,
                      "bars": b["bars"][-30:], "big_trades": b["big_trades"][-10:][::-1],
                      "age_s": round(now - b["updated"]), "fresh": now - b["updated"] < FRESH_SECONDS}
                     for b in sorted(self.books.values(), key=lambda b: -b["updated"])]
            state = ("off" if not enabled(env) else "waiting" if not self.last
                     else "live" if now - self.last < FRESH_SECONDS else "stale")
            return {"state": state, "enabled": enabled(env), "posts": self.posts,
                    "age_s": round(now - self.last) if self.last else None, "books": books,
                    "account": dict(self.account) if self.account else None,
                    "installed": ({"system": self.hello["system"], "age_s": round(now - self.hello["at"])}
                                  if self.hello else None),
                    "help": None if enabled(env) else "The feed needs a token: set DASHBOARD_PASSWORD or ATAS_TOKEN "
                                                    "(32+ characters) on Render."}
