"""ATAS receiver: order flow from the ATAS app on the owner's computer, joined into the one account.

ATAS has no cloud API. Its code runs only inside the ATAS desktop app, so a small indicator
(tools/atas/JarvisFeed.cs) runs there and posts one summary per finished bar to POST /api/atas/feed:

  {"symbol": "ES", "timeframe": "M1",
   "bar": {"time": "...", "open", "high", "low", "close", "volume", "delta", "cum_delta",
           "buy_volume", "sell_volume", "poc", "max_delta", "min_delta"},
   "big_trades": [{"price", "size", "side"}],          optional
   "account": {"balance", "pnl", "open_pnl", "positions": [{"symbol", "side", "size", "pnl"}]}}   optional

The token is ATAS_TOKEN (at least 32 characters) set on Render and pasted into the indicator's settings.
Without it set, the feed is off. The receiver only stores what it is sent; it never places orders.
"""

from __future__ import annotations

import math
import threading
import time

MIN_TOKEN = 32
FRESH_SECONDS = 180  # a feed older than this shows as stale
KEEP_BARS = 120
MAX_SYMBOLS = 30
BAR_KEYS = ("open", "high", "low", "close", "volume", "delta", "cum_delta", "buy_volume", "sell_volume", "poc",
            "max_delta", "min_delta")


class FeedError(ValueError):
    pass


def enabled(env) -> bool:
    return len(env.get("ATAS_TOKEN", "")) >= MIN_TOKEN


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
                    "help": None if enabled(env) else "Add ATAS_TOKEN (32+ random characters) on Render, then paste the same "
                                                    "token into the JarvisFeed indicator in ATAS."}
