"""Markets section: 25 products across crypto, forex, commodities, indices and shares.

Board (favourites, top risers/fallers, live prices, 5 min / 1 day / 40 day change) and a
page per product: candles on 6 timeframes with MA, Bollinger, RSI and MACD; a 1m/1h/4h/24h
outlook with reasons, stop loss, take profit and a not-worth-the-risk warning; why it is
moving; volume profile; and, for crypto, Smart DOM, Smart Tape, footprint and cumulative
delta from Coinbase trades.

Read-only analysis. It never places orders; real money stays locked elsewhere in the app.
"""
from __future__ import annotations

import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import catalog, changes, feeds, flow, indicators, outlook, volume_profile

_pool = ThreadPoolExecutor(max_workers=8)
_fav_lock = threading.Lock()
DEFAULT_FAVOURITES = ["BTC", "ETH", "GOLD", "SPX", "NVDA"]


def _fav_path() -> Path:
    return Path(os.environ.get("EVENTS_DIR", "events")) / "markets-favourites.json"


def favourites() -> list[str]:
    try:
        favs = json.loads(_fav_path().read_text())
        return [s for s in favs if s in catalog.BY_SYMBOL]
    except (OSError, ValueError):
        return list(DEFAULT_FAVOURITES)


def toggle_favourite(symbol: str) -> list[str]:
    sym = catalog.get(symbol).symbol
    with _fav_lock:
        favs = favourites()
        favs = [s for s in favs if s != sym] if sym in favs else favs + [sym]
        path = _fav_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(favs))
        os.replace(tmp, path)
    return favs


def board() -> dict:
    rows = list(_pool.map(changes.summary, [p.symbol for p in catalog.PRODUCTS]))
    return {"products": rows, "favourites": favourites(), "kinds": catalog.KINDS,
            "movers_1d": changes.movers(rows, "change_1d"),
            "movers_5m": changes.movers(rows, "change_5m"), "time": time.time()}


def order_flow(symbol: str) -> dict:
    trades, src = feeds.trades(symbol)
    book, _ = feeds.book(symbol)
    out = flow.summary(trades, book)
    out["source"] = src
    return out


def product(symbol: str, tf: str = "1h") -> dict:
    p = catalog.get(symbol)
    candles, src = feeds.candles(p.symbol, tf)
    of, share = None, None
    if p.kind == "crypto":
        of = order_flow(p.symbol)
        share = of["tape"]["buy_share"]
    return {"product": p.to_dict(), "timeframe": tf, "timeframes": list(feeds.TIMEFRAMES),
            "candles": candles, "indicators": indicators.all_for(candles), "source": src,
            "volume_profile": volume_profile.profile(candles[-200:]),
            "outlook": outlook.outlook(p.symbol, share),
            "why": outlook.why_moving(p.symbol, share),
            "orderflow": of, "favourite": p.symbol in favourites(),
            "summary": changes.summary(p.symbol)}
