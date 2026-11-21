"""Market data.

Live sources:
- Crypto: Coinbase Exchange public API (candles, trades, order book). No key needed.
- Everything else: Yahoo Finance chart API (delayed for some markets).
If a live source fails or is blocked, a simulated feed takes over so the pages
never break; every response says which source it came from.

Set MARKETS_FEED=sim to force the simulated feed (tests use this).
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import random
import threading
import time
import urllib.request

from . import catalog

TIMEFRAMES = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "4h": 14400, "1d": 86400}
_COINBASE_GRAN = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "1d": 86400}
_YAHOO = {"1m": ("1m", "1d"), "5m": ("5m", "5d"), "15m": ("15m", "5d"),
          "1h": ("60m", "1mo"), "1d": ("1d", "1y")}
UA = {"User-Agent": "jarvis-trading/1.0"}

_cache: dict = {}
_lock = threading.Lock()


def _cached(key, ttl, fn):
    now = time.time()
    with _lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < ttl:
            return hit[1]
    val = fn()
    with _lock:
        _cache[key] = (now, val)
    return val


def _get_json(url, timeout=6):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def sim_mode() -> bool:
    return os.environ.get("MARKETS_FEED", "").lower() == "sim"


# ---------- simulated feed ----------

def _seed(*parts) -> int:
    """Stable seed (Python's hash() changes per process)."""
    return int(hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()[:8], 16)


def sim_candles(symbol: str, tf: str, n: int = 300, now: float | None = None) -> list[dict]:
    p = catalog.get(symbol)
    step = TIMEFRAMES[tf]
    now = now if now is not None else time.time()
    end = int(now // step) * step
    rng = random.Random(_seed(symbol, tf, end // (step * 50)))
    vol = {"crypto": 0.012, "forex": 0.0015, "commodity": 0.006,
           "index": 0.004, "share": 0.008}[p.kind] * math.sqrt(step / 3600)
    drift = rng.uniform(-0.15, 0.15) * vol
    price = p.base_price * rng.uniform(0.9, 1.1)
    out = []
    for i in range(n):
        t = end - (n - 1 - i) * step
        o = price
        c = o * (1 + drift + rng.gauss(0, vol))
        h = max(o, c) * (1 + abs(rng.gauss(0, vol / 2)))
        l = min(o, c) * (1 - abs(rng.gauss(0, vol / 2)))
        v = abs(rng.gauss(1000, 300)) * (1 + 5 * abs(c - o) / o / max(vol, 1e-9) / 10)
        out.append({"t": t, "o": o, "h": h, "l": l, "c": c, "v": v})
        price = c
    # Anchor every timeframe to the same "current" price so pages agree.
    k = sim_anchor(symbol, now) / out[-1]["c"]
    for c in out:
        for f in ("o", "h", "l", "c"):
            c[f] *= k
    return out


def sim_anchor(symbol: str, now: float) -> float:
    p = catalog.get(symbol)
    now = int(now // 10) * 10  # moves every 10 seconds
    rng = random.Random(_seed(symbol, "anchor"))
    phase, amp = rng.uniform(0, 6.28), rng.uniform(0.02, 0.08)
    return p.base_price * (1 + amp * math.sin(now / 86400 / 7 + phase)
                           + 0.002 * math.sin(now / 600 + phase))


def sim_trades(symbol: str, n: int = 400, now: float | None = None) -> list[dict]:
    now = now if now is not None else time.time()
    last = sim_candles(symbol, "1m", 5, now)[-1]
    rng = random.Random(_seed(symbol, "trades", int(now // 10)))
    price = last["c"]
    tick = price * 0.0001
    out = []
    for i in range(n):
        side = "buy" if rng.random() < 0.52 else "sell"
        price += tick * rng.choice([-1, 0, 0, 1])
        size = abs(rng.lognormvariate(-1, 1.2))
        out.append({"t": now - (n - i) * 0.5, "price": round(price, 8), "size": size, "side": side})
    return out


def sim_book(symbol: str, now: float | None = None) -> dict:
    now = now if now is not None else time.time()
    mid = sim_trades(symbol, 5, now)[-1]["price"]
    rng = random.Random(_seed(symbol, "book", int(now // 5)))
    tick = mid * 0.0001
    bids = [[mid - tick * (i + 1), abs(rng.lognormvariate(0, 1))] for i in range(25)]
    asks = [[mid + tick * (i + 1), abs(rng.lognormvariate(0, 1))] for i in range(25)]
    return {"bids": bids, "asks": asks}


# ---------- live feeds ----------

def _aggregate(candles, step):
    out = {}
    for c in candles:
        k = int(c["t"] // step) * step
        b = out.get(k)
        if b is None:
            out[k] = dict(c, t=k)
        else:
            b["h"] = max(b["h"], c["h"]); b["l"] = min(b["l"], c["l"])
            b["c"] = c["c"]; b["v"] += c["v"]
    return [out[k] for k in sorted(out)]


def _coinbase_candles(feed_id, tf):
    if tf == "4h":
        return _aggregate(_coinbase_candles(feed_id, "1h"), TIMEFRAMES["4h"])
    rows = _get_json(f"https://api.exchange.coinbase.com/products/{feed_id}/candles"
                     f"?granularity={_COINBASE_GRAN[tf]}")
    # Coinbase rows: [time, low, high, open, close, volume], newest first
    return [{"t": r[0], "o": r[3], "h": r[2], "l": r[1], "c": r[4], "v": r[5]}
            for r in reversed(rows)]


def _yahoo_candles(feed_id, tf):
    if tf == "4h":
        return _aggregate(_yahoo_candles(feed_id, "1h"), TIMEFRAMES["4h"])
    interval, rng = _YAHOO[tf]
    j = _get_json(f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.request.quote(feed_id)}"
                  f"?interval={interval}&range={rng}")
    res = j["chart"]["result"][0]
    q = res["indicators"]["quote"][0]
    out = []
    for i, t in enumerate(res.get("timestamp") or []):
        o, h, l, c = q["open"][i], q["high"][i], q["low"][i], q["close"][i]
        if None in (o, h, l, c):
            continue
        out.append({"t": t, "o": o, "h": h, "l": l, "c": c, "v": q["volume"][i] or 0})
    return out


def candles(symbol: str, tf: str = "1h") -> tuple[list[dict], str]:
    """Returns (candles oldest-first, source)."""
    if tf not in TIMEFRAMES:
        raise ValueError(f"timeframe must be one of {list(TIMEFRAMES)}")
    p = catalog.get(symbol)
    if not sim_mode():
        try:
            fn = _coinbase_candles if p.kind == "crypto" else _yahoo_candles
            data = _cached(("c", symbol, tf), 20, lambda: fn(p.feed_id, tf))
            if len(data) >= 30:
                return data, "coinbase" if p.kind == "crypto" else "yahoo"
        except Exception:
            pass
    return sim_candles(symbol, tf), "simulated"


def trades(symbol: str) -> tuple[list[dict], str]:
    p = catalog.get(symbol)
    if p.kind == "crypto" and not sim_mode():
        try:
            rows = _cached(("t", symbol), 3, lambda: _get_json(
                f"https://api.exchange.coinbase.com/products/{p.feed_id}/trades?limit=1000"))
            # Coinbase "side" is the maker side: maker sell => taker bought.
            out = [{"t": _iso(r["time"]), "price": float(r["price"]), "size": float(r["size"]),
                    "side": "buy" if r["side"] == "sell" else "sell"} for r in reversed(rows)]
            return out, "coinbase"
        except Exception:
            pass
    return sim_trades(symbol), "simulated"


def book(symbol: str) -> tuple[dict, str]:
    p = catalog.get(symbol)
    if p.kind == "crypto" and not sim_mode():
        try:
            j = _cached(("b", symbol), 2, lambda: _get_json(
                f"https://api.exchange.coinbase.com/products/{p.feed_id}/book?level=2"))
            return {"bids": [[float(a), float(b)] for a, b, *_ in j["bids"][:50]],
                    "asks": [[float(a), float(b)] for a, b, *_ in j["asks"][:50]]}, "coinbase"
        except Exception:
            pass
    return sim_book(symbol), "simulated"


def _iso(s: str) -> float:
    from datetime import datetime
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
