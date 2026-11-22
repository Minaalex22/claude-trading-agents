"""Atlas's extra eyes: our ATAS-style order flow plus the analysts' street view.

``AtlasSources.check(symbol, side)`` is called before Atlas approves a trade.
It returns whether the two sources agree, a confidence multiplier, and plain
reasons Atlas can show. Order flow is the short-term confirmation (minutes);
street view is the bigger picture (days). Real-money trading is untouched:
this only scores ideas, it never places orders.
"""
from __future__ import annotations

import threading

from .orderflow import OrderFlowDesk, Trade
from .street_view import StreetView

# Which street-view market a trading symbol belongs to.
SYMBOL_TO_MARKET = {
    "BTC-USD": "BTC", "BTCUSDT": "BTC", "ETH-USD": "ETH", "ETHUSDT": "ETH",
    "SPY": "SPX", "ES": "SPX", "QQQ": "NDX", "NQ": "NDX",
    "GC": "GOLD", "XAUUSD": "GOLD", "CL": "OIL", "BZ": "OIL",
    "EURUSD": "EURUSD", "USDJPY": "USDJPY", "GBPUSD": "GBPUSD",
    "2800.HK": "HSI", "ES3.SI": "STI", "USDSGD": "USDSGD", "USDCNH": "USDCNH",
}


class AtlasSources:
    def __init__(self, flow: OrderFlowDesk | None = None,
                 street: StreetView | None = None,
                 flow_weight: float = 0.6, street_weight: float = 0.4,
                 veto_below: float = -0.35):
        self.flow = flow or OrderFlowDesk()
        self.street = street or StreetView()
        self.flow_weight = flow_weight
        self.street_weight = street_weight
        self.veto_below = veto_below
        self.lock = threading.Lock()
        self.real_feed: set[str] = set()  # symbols with a real trade feed (not price ticks)

    def on_prices(self, prices: dict, ts: float) -> None:
        """Price-tick mode: with no trade feed, each new price counts as one trade and the tick
        rule decides its side (uptick = buyer). A real trade feed for a symbol replaces this."""
        with self.lock:
            for sym, price in prices.items():
                if sym in self.real_feed or not isinstance(price, (int, float)) or price <= 0:
                    continue
                self.flow.add(sym, Trade(float(ts), float(price), 1.0))

    def on_trades(self, symbol: str, trades: list[dict]) -> int:
        """Real trades pushed in (price, size, side "buy"/"sell", ts). Returns how many were used."""
        n = 0
        with self.lock:
            if symbol not in self.real_feed:
                self.real_feed.add(symbol)
                self.flow.books.pop(symbol, None)  # drop the price-tick estimate
            for t in trades[:5000]:
                try:
                    side = t.get("side")
                    side = side if side in ("buy", "sell") else None
                    trade = Trade(float(t["ts"]), float(t["price"]), float(t["size"]), side)
                except (KeyError, TypeError, ValueError):
                    continue
                if trade.price > 0 and trade.size > 0:
                    self.flow.add(symbol, trade)
                    n += 1
        return n

    def page_data(self) -> dict:
        """What the /orderflow page shows."""
        with self.lock:
            books = self.flow.snapshot(bars=12)
            for sym, b in books.items():
                b["feed"] = "trades" if sym in self.real_feed else "price ticks"
            return {"books": books, "houses": self.street.board()}

    def check(self, symbol: str, side: str) -> dict:
        """side is "buy" or "sell". Returns approve/veto + confidence multiplier."""
        with self.lock:
            return self._check(symbol, side)

    def _check(self, symbol: str, side: str) -> dict:
        want = 1.0 if side.lower() in ("buy", "long") else -1.0
        fv = self.flow.view(symbol)
        market = SYMBOL_TO_MARKET.get(symbol, symbol)
        sv = self.street.view(market)

        parts, weights, reasons = [], [], []
        if fv["ready"]:
            parts.append(want * fv["direction"] * max(fv["confidence"], 0.2))
            weights.append(self.flow_weight)
            reasons += [f"order flow: {r}" for r in fv["reasons"][:3]]
        if sv["ready"]:
            parts.append(want * sv["direction"] * max(sv["confidence"], 0.2))
            weights.append(self.street_weight)
            word = "agree" if want * sv["direction"] > 0 else "disagree"
            reasons.append(f"analysts {word} ({sv['houses']} houses)")

        if not weights:
            return {"symbol": symbol, "side": side, "agreement": 0.0,
                    "approve": True, "multiplier": 1.0,
                    "reasons": ["no order flow or analyst data yet; Atlas decides alone"],
                    "flow": fv, "street": sv}
        agreement = sum(p * w for p, w in zip(parts, weights)) / sum(weights)
        approve = agreement > self.veto_below
        multiplier = round(max(0.5, min(1.5, 1.0 + agreement)), 3)
        if not approve:
            reasons.insert(0, "vetoed: order flow and analysts point the other way")
        return {"symbol": symbol, "side": side, "agreement": round(agreement, 3),
                "approve": approve, "multiplier": multiplier if approve else 0.0,
                "reasons": reasons, "flow": fv, "street": sv}
