"""Mitrade (CFD broker) bridge: Mitrade's own live prices, plus a practice desk that fills at Mitrade's bid/ask.

What Mitrade offers (checked 2026-11-24):
- No public trading API, no MetaTrader 4/5, no EAs. Its own app and web terminal are the only way to trade.
- Its website (www.mitrade.com) shows live prices to visitors who are not signed in. Those come from the site's own
  backend, https://www.mizoneservice.com/api/v3, as a guest: no account, no login, no key. The site sends a few plain
  headers (appIdentifier, appVersion, deviceUUID, system, systemVersion, locale). We send the same, so the feed
  answers us like it answers any visitor of mitrade.com.
- That gives us Mitrade's real bid and ask (their spread), daily open/high/low/close and a short price trend for
  forex, gold, oil, indices, crypto and shares.

What this file does:
- quotes(): Mitrade's live bid/ask for our symbols. Cached a few seconds so we never hammer Mitrade (one request
  for all symbols, at most every QUOTE_TTL seconds).
- PracticeDesk: CFD-style practice trades (buy/sell, leverage, stop loss, take profit) that open at Mitrade's ask
  (buy) or bid (sell) and close on the other side, so the spread costs what it costs on Mitrade.
  Nothing is ever sent to Mitrade. Real money is locked.

Safety:
- Only GET requests to the guest price paths in READ_PATHS. Anything else is refused before the network.
- No login, no password, no account. The real Mitrade account is not touched by this file.
"""

import json
import os
import threading
import time
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

API_BASE = "https://www.mizoneservice.com/api/v3"
HTTP_TIMEOUT = 10
QUOTE_TTL = 5.0
TREND_TTL = 120.0
READ_PATHS = ("/instruments/quotations/all", "/instruments/trend")
DEVICE_ID = uuid.uuid4().hex  # one per server run, like a browser visit
HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "appIdentifier": "official",
    "appVersion": "1.0.0",
    "deviceUUID": DEVICE_ID,
    "system": "Chrome",
    "systemVersion": "131.0.0.0",
    "locale": "en-US",
    "Origin": "https://www.mitrade.com",
    "Referer": "https://www.mitrade.com/",
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/131.0.0.0 Safari/537.36"),
}
# symbol -> (group, digits, ASIC retail leverage cap)
SYMBOLS = {
    "XAUUSD": ("Gold and metals", 2, 20), "XAGUSD": ("Gold and metals", 3, 10),
    "EURUSD": ("Forex", 5, 30), "GBPUSD": ("Forex", 5, 30), "USDJPY": ("Forex", 3, 30),
    "AUDUSD": ("Forex", 5, 30), "USDCAD": ("Forex", 5, 30), "GBPJPY": ("Forex", 3, 20),
    "USOIL": ("Commodities", 2, 10), "UKOIL": ("Commodities", 2, 10), "NATGAS": ("Commodities", 3, 10),
    "NAS100": ("Indices", 1, 20), "US500": ("Indices", 1, 20), "US30": ("Indices", 1, 20),
    "AUS200": ("Indices", 1, 20), "JPN225": ("Indices", 0, 20),
    "BTCUSD": ("Crypto", 2, 2), "ETHUSD": ("Crypto", 2, 2),
    "TSLA": ("Shares", 2, 5), "NVDA": ("Shares", 2, 5), "AAPL": ("Shares", 2, 5),
}
MAX_LEVERAGE = 5  # our money rules; lower again where ASIC's retail cap is lower (crypto 2x)
START_CASH = 10_000.0  # practice USD
LINKS = {
    "site": "https://www.mitrade.com/au",
    "web_trader": "https://www.mitrade.com/au/trade",
    "demo": "https://www.mitrade.com/au/demo-account",
}


class Refused(PermissionError):
    pass


def _http(url: str) -> dict:
    req = urllib.request.Request(url, headers=HEADERS, method="GET")
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:  # noqa: S310 (fixed https host)
        return json.loads(resp.read() or b"null")


def _get(path: str, params: dict, http=_http):
    if path not in READ_PATHS:
        raise Refused(f"{path} is not a public price path; the Mitrade bridge never calls it.")
    data = http(f"{API_BASE}{path}?{urllib.parse.urlencode(params, safe=',')}") or {}
    if not data.get("success"):
        raise RuntimeError(f"Mitrade said: {str(data.get('message') or 'no answer')[:120]}")
    return data.get("value") or []


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def parse_quote(row: dict) -> dict | None:
    sym = str(row.get("symbol") or "").upper()
    ask, bid = _num(row.get("buyPrice")), _num(row.get("sellPrice"))
    if not sym or not ask or not bid:
        return None
    if bid > ask:
        bid, ask = ask, bid
    group, digits, _ = SYMBOLS.get(sym, (str(row.get("type") or "Other").title(), 5, 1))
    pct = _num(row.get("dailyChangeInPercentage"))
    return {"symbol": sym, "group": group, "digits": digits, "bid": bid, "ask": ask,
            "mid": round((bid + ask) / 2, digits + 1), "spread": round(ask - bid, digits + 1),
            "open": _num(row.get("open")), "high": _num(row.get("dailyHigh")), "low": _num(row.get("dailyLow")),
            "prev_close": _num(row.get("lastClose")), "change": _num(row.get("dailyChangeInValue")),
            "change_pct": round(pct * 100, 2) if pct is not None else None,
            "updated": row.get("updateDatetime"), "ts": _num(row.get("updateTimestamp"))}


class Feed:
    """Mitrade's guest price feed with a short cache. Never raises from quotes()/trend()."""

    def __init__(self, http=_http, now=time.time):
        self.http, self.now = http, now
        self.lock = threading.Lock()
        self._q = {"at": 0.0, "rows": {}, "error": None}
        self._t = {"at": 0.0, "rows": {}}

    def quotes(self) -> dict:
        with self.lock:
            if self._q["rows"] and self.now() - self._q["at"] < QUOTE_TTL:
                return self._q["rows"]
            try:
                rows = _get(READ_PATHS[0], {"symbols": ",".join(SYMBOLS)}, self.http)
                got = {q["symbol"]: q for q in (parse_quote(r) for r in rows if isinstance(r, dict)) if q}
                if got:
                    self._q.update(at=self.now(), rows=got, error=None)
                else:
                    self._q["error"] = "Mitrade sent no prices"
            except Exception as e:  # noqa: BLE001  (network or Mitrade change: report it, keep the last prices)
                self._q["error"] = f"{type(e).__name__}: {str(e)[:140]}"
                self._q["at"] = self.now() - QUOTE_TTL + 2  # wait a moment before asking again
            return self._q["rows"]

    def status(self) -> dict:
        rows = self.quotes()
        return {"connected": bool(rows) and not self._q["error"], "error": self._q["error"],
                "count": len(rows), "source": "mitrade.com public price feed (guest, no login)"}

    def quote(self, symbol: str) -> dict | None:
        return self.quotes().get(str(symbol).upper())

    def trend(self) -> dict:
        with self.lock:
            if self._t["rows"] and self.now() - self._t["at"] < TREND_TTL:
                return self._t["rows"]
            try:
                rows = _get(READ_PATHS[1], {"symbol": ",".join(SYMBOLS)}, self.http)
                got = {str(r.get("symbol")).upper(): [float(x) for x in (r.get("data") or [])][-48:]
                       for r in rows if isinstance(r, dict) and r.get("symbol")}
                if got:
                    self._t.update(at=self.now(), rows=got)
            except Exception:  # noqa: BLE001
                self._t["at"] = self.now() - TREND_TTL + 30
            return self._t["rows"]


FEED = Feed()


def price(symbol: str) -> float | None:
    """Mid price from Mitrade, for other parts of the app. None when Mitrade doesn't answer."""
    q = FEED.quote(symbol)
    return q["mid"] if q else None


def max_leverage(symbol: str) -> int:
    return min(MAX_LEVERAGE, SYMBOLS.get(str(symbol).upper(), ("", 0, 1))[2])


# ---------------------------------------------------------------- practice desk (never touches Mitrade)

class PracticeDesk:
    """CFD practice trades at Mitrade's own bid/ask. Stored as JSON next to the events."""

    def __init__(self, folder: str | Path | None = None, feed: Feed | None = None):
        self.path = Path(folder or os.environ.get("EVENTS_DIR") or ".") / "mitrade_practice.json"
        self.feed = feed or FEED
        self.lock = threading.Lock()
        self.state = self._load()

    def _load(self) -> dict:
        try:
            data = json.loads(self.path.read_text())
            if isinstance(data, dict) and "cash" in data:
                return data
        except (OSError, ValueError):
            pass
        return {"cash": START_CASH, "open": [], "closed": [], "next_id": 1}

    def _save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self.state))
        except OSError:
            pass  # Render's disk can be read-only or wiped; the desk keeps working in memory

    @staticmethod
    def _exit_price(pos: dict, q: dict) -> float:
        return q["bid"] if pos["side"] == "buy" else q["ask"]  # a buy closes by selling at the bid

    @staticmethod
    def _pnl(pos: dict, price: float) -> float:
        move = (price - pos["entry"]) / pos["entry"] * (1 if pos["side"] == "buy" else -1)
        return pos["margin"] * pos["leverage"] * move

    def open(self, symbol: str, side: str, margin: float, leverage: float, stop_loss: float,
             take_profit: float | None = None, live: bool = False) -> dict:
        if live:
            raise Refused("Real-money Mitrade orders are locked. This is a practice desk.")
        symbol = str(symbol).upper()
        if symbol not in SYMBOLS:
            raise ValueError(f"symbol must be one of {', '.join(SYMBOLS)}")
        side = {"long": "buy", "short": "sell"}.get(side, side)
        if side not in ("buy", "sell"):
            raise ValueError("side must be buy or sell")
        margin, leverage, stop_loss = float(margin), float(leverage), float(stop_loss)
        cap = max_leverage(symbol)
        if not 1 <= leverage <= cap:
            raise ValueError(f"leverage for {symbol} must be 1 to {cap}x (our money rules and ASIC's cap)")
        q = self.feed.quote(symbol)
        if not q:
            raise RuntimeError("no Mitrade price right now, try again in a minute")
        buy = side == "buy"
        price = q["ask"] if buy else q["bid"]
        if (buy and stop_loss >= q["bid"]) or (not buy and stop_loss <= q["ask"]):
            raise ValueError("stop loss must be below the price for a buy, above it for a sell")
        if take_profit is not None:
            take_profit = float(take_profit)
            if (buy and take_profit <= price) or (not buy and take_profit >= price):
                raise ValueError("take profit must be above the price for a buy, below it for a sell")
        with self.lock:
            if not 0 < margin <= self.state["cash"]:
                raise ValueError("not enough practice cash for that margin")
            self.state["cash"] -= margin
            pos = {"id": self.state["next_id"], "symbol": symbol, "side": side, "margin": margin,
                   "leverage": leverage, "entry": price, "spread": q["spread"], "stop_loss": stop_loss,
                   "take_profit": take_profit, "opened": time.time()}
            self.state["next_id"] += 1
            self.state["open"].append(pos)
            self._save()
        return dict(pos)

    def close(self, pos_id: int, reason: str = "closed by hand", price: float | None = None) -> dict:
        with self.lock:
            pos = next((p for p in self.state["open"] if p["id"] == int(pos_id)), None)
            if pos is None:
                raise KeyError("no open practice position with that id")
            if price is None:
                q = self.feed.quote(pos["symbol"])
                if not q:
                    raise RuntimeError("no Mitrade price right now, try again in a minute")
                price = self._exit_price(pos, q)
            pnl = max(self._pnl(pos, price), -pos["margin"])  # can't lose more than the margin
            self.state["cash"] += pos["margin"] + pnl
            self.state["open"].remove(pos)
            done = {**pos, "exit": price, "pnl": round(pnl, 2), "reason": reason, "closed": time.time()}
            self.state["closed"] = (self.state["closed"] + [done])[-200:]
            self._save()
        return done

    def check_stops(self) -> list[dict]:
        hits = []
        for pos in list(self.state["open"]):
            q = self.feed.quote(pos["symbol"])
            if not q:
                continue
            px, buy = self._exit_price(pos, q), pos["side"] == "buy"
            if (buy and px <= pos["stop_loss"]) or (not buy and px >= pos["stop_loss"]):
                hits.append(self.close(pos["id"], "stop loss", pos["stop_loss"]))
            elif pos.get("take_profit") and ((buy and px >= pos["take_profit"]) or
                                             (not buy and px <= pos["take_profit"])):
                hits.append(self.close(pos["id"], "take profit", pos["take_profit"]))
        return hits

    def snapshot(self) -> dict:
        self.check_stops()
        with self.lock:
            rows, upnl, margin = [], 0.0, 0.0
            for p in self.state["open"]:
                q = self.feed.quote(p["symbol"])
                mark = self._exit_price(p, q) if q else None
                pnl = self._pnl(p, mark) if mark else 0.0
                upnl += pnl
                margin += p["margin"]
                rows.append({**p, "mark": mark, "pnl": round(pnl, 2),
                             "roe": round(pnl / p["margin"] * 100, 1) if p["margin"] else 0.0})
            closed = self.state["closed"]
            wins = sum(1 for c in closed if c["pnl"] > 0)
            return {"cash": round(self.state["cash"], 2), "margin": round(margin, 2),
                    "equity": round(self.state["cash"] + margin + upnl, 2), "unrealized": round(upnl, 2),
                    "start": START_CASH, "open": rows, "closed": closed[-30:][::-1],
                    "win_rate": round(wins / len(closed) * 100, 1) if closed else None,
                    "realized": round(sum(c["pnl"] for c in closed), 2), "max_leverage": MAX_LEVERAGE,
                    "caps": {s: max_leverage(s) for s in SYMBOLS}}


ACCOUNT_NOTE = ("Your own Mitrade account is not linked. Mitrade has no API for accounts, so linking it means "
                "copying the web app's sign-in, which Mitrade can treat as breaking its terms. Prices and "
                "practice trades here need no sign-in.")
