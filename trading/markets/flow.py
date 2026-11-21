"""Order flow tools (crypto pages): Smart DOM, Smart Tape, footprint, cumulative delta.

Built from Coinbase public trades and the level-2 order book.
"""
from __future__ import annotations

import statistics


def smart_dom(book: dict, levels=15) -> dict:
    """Order book ladder with walls (unusually big resting size) flagged."""
    bids = book["bids"][:levels]
    asks = book["asks"][:levels]
    sizes = [s for _, s in bids + asks] or [0]
    med = statistics.median(sizes) or 1e-9
    row = lambda side: [{"price": p, "size": s, "wall": s >= 4 * med} for p, s in side]
    bid_total = sum(s for _, s in bids)
    ask_total = sum(s for _, s in asks)
    tot = bid_total + ask_total or 1e-9
    return {"bids": row(bids), "asks": row(asks),
            "imbalance": (bid_total - ask_total) / tot,
            "spread": (asks[0][0] - bids[0][0]) if bids and asks else None}


def smart_tape(trades: list[dict], n=60) -> dict:
    """Recent prints, with big ones (top 5% by size) highlighted."""
    if not trades:
        return {"prints": [], "big_threshold": None, "buy_share": None}
    sizes = sorted(t["size"] for t in trades)
    thr = sizes[int(len(sizes) * 0.95)] if len(sizes) > 1 else sizes[0]
    recent = trades[-n:]
    buy = sum(t["size"] for t in trades if t["side"] == "buy")
    tot = sum(t["size"] for t in trades) or 1e-9
    return {"prints": [dict(t, big=t["size"] >= thr) for t in reversed(recent)],
            "big_threshold": thr, "buy_share": buy / tot}


def footprint(trades: list[dict], bar_seconds=60, tick=None) -> list[dict]:
    """Per bar: bid (sell) and ask (buy) volume at each price, plus delta."""
    if not trades:
        return []
    if tick is None:
        px = trades[-1]["price"]
        tick = _nice_tick(px)
    bars = {}
    for t in trades:
        k = int(t["t"] // bar_seconds) * bar_seconds
        lvl = round(round(t["price"] / tick) * tick, 10)
        b = bars.setdefault(k, {})
        cell = b.setdefault(lvl, {"buy": 0.0, "sell": 0.0})
        cell[t["side"]] += t["size"]
    out = []
    for k in sorted(bars):
        cells = bars[k]
        rows = [{"price": p, "buy": v["buy"], "sell": v["sell"],
                 "imbalance": _imb(v["buy"], v["sell"])} for p, v in sorted(cells.items(), reverse=True)]
        delta = sum(r["buy"] - r["sell"] for r in rows)
        out.append({"t": k, "rows": rows, "delta": delta,
                    "volume": sum(r["buy"] + r["sell"] for r in rows)})
    return out


def cumulative_delta(trades: list[dict]) -> list[dict]:
    cd, out = 0.0, []
    for t in trades:
        cd += t["size"] if t["side"] == "buy" else -t["size"]
        out.append({"t": t["t"], "cd": cd})
    return out


def _imb(buy, sell):
    if sell == 0 and buy > 0:
        return "buy"
    if buy == 0 and sell > 0:
        return "sell"
    if buy >= 3 * sell:
        return "buy"
    if sell >= 3 * buy:
        return "sell"
    return None


def _nice_tick(price):
    raw = price * 0.0002
    mag = 10 ** __import__("math").floor(__import__("math").log10(raw)) if raw > 0 else 1
    for m in (1, 2, 5, 10):
        if raw <= m * mag:
            return m * mag
    return 10 * mag


def summary(trades, book):
    tape = smart_tape(trades)
    dom = smart_dom(book)
    cd = cumulative_delta(trades)
    return {"dom": dom, "tape": tape, "footprint": footprint(trades)[-12:],
            "cumulative_delta": cd[-300:],
            "delta_now": cd[-1]["cd"] if cd else 0.0}
