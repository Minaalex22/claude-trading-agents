"""Price changes (5 min, 1 day, 40 days) and top risers / fallers."""
from __future__ import annotations

from . import catalog, feeds


def pct(a, b):
    return None if not a else (b - a) / a * 100


def change_since(candles, seconds):
    if not candles:
        return None
    end = candles[-1]["t"]
    ref = None
    for c in candles:
        if c["t"] <= end - seconds:
            ref = c
    if ref is None:
        return None
    return pct(ref["c"], candles[-1]["c"])


def summary(symbol: str) -> dict:
    p = catalog.get(symbol)
    m1, src = feeds.candles(symbol, "1m")
    d1, _ = feeds.candles(symbol, "1d")
    h1, _ = feeds.candles(symbol, "1h")
    price = m1[-1]["c"] if m1 else d1[-1]["c"]
    ch_1d = change_since(h1, 86400)
    if ch_1d is None:
        ch_1d = change_since(d1, 86400)
    return {
        **p.to_dict(),
        "price": price,
        "change_5m": change_since(m1, 300),
        "change_1d": ch_1d,
        "change_40d": change_since(d1, 40 * 86400),
        "spark": [c["c"] for c in h1[-48:]],
        "source": src,
    }


def board(symbols=None) -> list[dict]:
    return [summary(s) for s in (symbols or [p.symbol for p in catalog.PRODUCTS])]


def movers(rows, key="change_1d", n=5):
    ok = [r for r in rows if r.get(key) is not None]
    ok.sort(key=lambda r: r[key], reverse=True)
    return {"risers": ok[:n], "fallers": list(reversed(ok[-n:]))}
