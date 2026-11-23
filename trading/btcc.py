"""BTCC (crypto futures exchange) bridge: read the account when BTCC's API is on, practise BTCC-style futures always.

What BTCC offers (checked 2026-11-23):
- BTCC's TradeOpenAPI signs every call with an MD5 of the sorted parameters plus the secret key, and needs a
  session login (api key + BTCC username + password) before reading positions.
- On 13 Feb 2026 BTCC **suspended its Futures API and closed the API key portal** "until further notice"
  (https://www.btcc.com/en-US/detail/176811). The App and Web terminal still trade. So this bridge is ready for
  the day BTCC switches the API back on, and until then it says so plainly instead of pretending.
- BTCC has no demo/testnet API. So orders here are PRACTICE only: BTCC-style USDT perpetuals (long/short,
  leverage, stop loss, take profit) on live prices, kept in our own book. Nothing is ever sent to BTCC.

Safety:
- Only two BTCC calls exist in this file: the session login and the position list (read only). Any other path
  is refused before a network request is made. There is no order, transfer or withdrawal call at all.
- Keys come only from environment variables (Render). Nothing here logs or returns them.
- Practice rules match our money rules: stop loss required, leverage capped at MAX_LEVERAGE.

Environment (Render > Environment, never in the repo or chat):
  BTCC_API_KEY, BTCC_SECRET_KEY   from BTCC > profile > API Setting (read-only boxes only), once BTCC reopens it
  BTCC_USERNAME, BTCC_PASSWORD    BTCC's API login needs them; set them only if you accept that
  BTCC_API_BASE_URL               the https address BTCC gives with the key
"""

import hashlib
import json
import os
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path
from xml.etree import ElementTree

HTTP_TIMEOUT = 10
NOTICE_URL = "https://www.btcc.com/en-US/detail/176811"
API_STATUS = ("BTCC paused its Futures API and closed new API keys on 13 Feb 2026. The BTCC App and website "
              "still trade. This bridge switches on by itself once BTCC reopens the API and the keys are set.")
ENV_KEYS = ("BTCC_API_KEY", "BTCC_SECRET_KEY", "BTCC_USERNAME", "BTCC_PASSWORD", "BTCC_API_BASE_URL")
READ_CALLS = {("POST", "/v1/user/login"), ("GET", "/v1/account/positionlist")}
SYMBOLS = ("BTC", "ETH", "SOL")
MAX_LEVERAGE = 5
START_CASH = 10_000.0  # practice USDT
MARK_TTL = 5.0
FEE_RATE = 0.0006  # taker fee per side, a typical crypto-futures rate
NEWS_TTL = 900
NEWS_FEEDS = (  # public, no keys: BTCC's own news as carried by Google News
    "https://news.google.com/rss/search?q=%22BTCC%22+exchange&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=site%3Abtcc.com&hl=en-US&gl=US&ceid=US:en",
)
LINKS = {  # BTCC's own pages, opened in a new tab
    "announcements": "https://www.btcc.com/en-US/announcements",
    "futures": "https://www.btcc.com/en-US/trade/perpetual/BTCUSDT",
    "copy_trading": "https://www.btcc.com/en-US/copy-trading",
    "api_notice": NOTICE_URL,
}
USER_AGENT = "Mozilla/5.0 (BTCC bridge reader)"


class Refused(PermissionError):
    pass


def configured(env=os.environ) -> bool:
    return all((env.get(k) or "").strip() for k in ENV_KEYS)


def missing(env=os.environ) -> list[str]:
    return [k for k in ENV_KEYS if not (env.get(k) or "").strip()]


def _enc(value) -> str:
    return urllib.parse.quote(str(value), safe="-_.~")


def sign(params: dict, secret: str) -> str:
    """BTCC TradeOpenAPI signature: md5 of the sorted, url-encoded params with secret_key added."""
    items = sorted({**{k: str(v) for k, v in params.items()}, "secret_key": secret}.items())
    return hashlib.md5("&".join(f"{_enc(k)}={_enc(v)}" for k, v in items).encode()).hexdigest()  # noqa: S324


def _http(method: str, url: str, body: bytes | None) -> dict:
    headers = {"Accept": "application/json", "User-Agent": USER_AGENT}
    if body is not None:
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:  # noqa: S310 (https only, checked below)
        return json.loads(resp.read() or b"null")


class BtccClient:
    """Read-only. Login + position list, nothing else."""

    def __init__(self, env=os.environ, http=_http):
        gone = missing(env)
        if gone:
            raise PermissionError(f"Set {', '.join(gone)} on Render to read the BTCC account.")
        base = env["BTCC_API_BASE_URL"].strip().rstrip("/")
        if urllib.parse.urlsplit(base).scheme != "https":
            raise ValueError("BTCC_API_BASE_URL must start with https://")
        self.base = base
        self._key, self._secret = env["BTCC_API_KEY"].strip(), env["BTCC_SECRET_KEY"]
        self._user, self._password = env["BTCC_USERNAME"].strip(), env["BTCC_PASSWORD"]
        self.http = http
        self._token, self.account_id = "", (env.get("BTCC_ACCOUNT_ID") or "").strip()

    def __repr__(self) -> str:  # never show keys
        return "BtccClient(read-only)"

    def _call(self, method: str, path: str, params: dict) -> dict:
        if (method, path) not in READ_CALLS:
            raise Refused(f"{method} {path} is not a read-only call; the BTCC bridge never sends it.")
        fields = urllib.parse.urlencode({**{k: str(v) for k, v in params.items()}, "sign": sign(params, self._secret)})
        url = self.base + path + (f"?{fields}" if method == "GET" else "")
        data = self.http(method, url, fields.encode() if method == "POST" else None) or {}
        if data.get("code") != 0:
            raise RuntimeError(f"BTCC said: {str(data.get('msg') or 'no answer')[:120]}")
        return data

    def login(self) -> None:
        data = self._call("POST", "/v1/user/login", {"user_name": self._user, "password": self._password,
                                                       "company_id": 1, "api_key": self._key})
        self._token = str(data.get("token") or "")
        acct = data.get("account") or {}
        if acct.get("id"):
            self.account_id = str(acct["id"])
        if not self._token:
            raise RuntimeError("BTCC login gave no session")

    def positions(self) -> list[dict]:
        if not self._token:
            self.login()
        data = self._call("GET", "/v1/account/positionlist",
                          {"token": self._token, "accountid": self.account_id, "pageNo": 1, "pageSize": 100})
        rows = data.get("positions") or data.get("list") or data.get("data") or []
        out = []
        for p in rows if isinstance(rows, list) else []:
            out.append({"symbol": p.get("symbol"), "side": "long" if str(p.get("direction")) == "1" else "short",
                        "volume": _num(p.get("volume") or p.get("request_volume")),
                        "entry": _num(p.get("open_price") or p.get("price")),
                        "leverage": _num(p.get("multiple") or p.get("leverage")),
                        "pnl": _num(p.get("profit") or p.get("unrealized_pnl")),
                        "stop_loss": _num(p.get("stop_loss")), "take_profit": _num(p.get("take_profit"))})
        return out


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def live_account(env=os.environ, http=_http) -> dict:
    """What the BTCC page shows about the real account. Read only, never raises."""
    if not configured(env):
        return {"connected": False, "api": "paused", "status": API_STATUS, "notice": NOTICE_URL,
                "needs": missing(env)}
    try:
        return {"connected": True, "api": "on", "positions": BtccClient(env, http).positions()}
    except Exception as e:  # noqa: BLE001  (network, paused API, bad keys: report it, keep serving)
        return {"connected": False, "api": "error", "status": API_STATUS, "notice": NOTICE_URL,
                "error": f"{type(e).__name__}: {str(e)[:160]}"}


# ---------------------------------------------------------------- practice desk (never touches BTCC)

def _default_price(symbol: str) -> float:
    from . import live_market
    return live_market.fetch_price(symbol)


class PracticeDesk:
    """BTCC-style USDT perpetuals on live prices with practice money. Stored as JSON next to the events."""

    def __init__(self, folder: str | Path | None = None, price=_default_price):
        self.path = Path(folder or os.environ.get("EVENTS_DIR") or ".") / "btcc_practice.json"
        self.price = price
        self.lock = threading.Lock()
        self.state = self._load()
        self._marks: dict[str, tuple[float, float]] = {}  # symbol -> (time, price), a few seconds' cache

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

    def _mark(self, symbol: str) -> float | None:
        hit = self._marks.get(symbol)
        if hit and time.time() - hit[0] < MARK_TTL:
            return hit[1]
        try:
            p = float(self.price(symbol))
        except Exception:  # noqa: BLE001
            return hit[1] if hit else None
        if p > 0:
            self._marks[symbol] = (time.time(), p)
            return p
        return None

    def marks(self) -> dict:
        return {s: self._mark(s) for s in SYMBOLS}

    @staticmethod
    def _pnl(pos: dict, price: float) -> float:
        move = (price - pos["entry"]) / pos["entry"] * (1 if pos["side"] == "long" else -1)
        return pos["margin"] * pos["leverage"] * move

    def open(self, symbol: str, side: str, margin: float, leverage: float, stop_loss: float,
             take_profit: float | None = None, live: bool = False) -> dict:
        if live:
            raise Refused("Real-money BTCC orders are locked. This is a practice desk.")
        symbol = str(symbol).upper().replace("USDT", "")
        if symbol not in SYMBOLS:
            raise ValueError(f"symbol must be one of {', '.join(SYMBOLS)}")
        if side not in ("long", "short"):
            raise ValueError("side must be long or short")
        margin, leverage, stop_loss = float(margin), float(leverage), float(stop_loss)
        if not 1 <= leverage <= MAX_LEVERAGE:
            raise ValueError(f"leverage must be 1 to {MAX_LEVERAGE}x (our money rules)")
        price = self._mark(symbol)
        if price is None:
            raise RuntimeError("no live price right now, try again in a minute")
        long = side == "long"
        if (long and stop_loss >= price) or (not long and stop_loss <= price):
            raise ValueError("stop loss must be below the price for a long, above it for a short")
        if take_profit is not None:
            take_profit = float(take_profit)
            if (long and take_profit <= price) or (not long and take_profit >= price):
                raise ValueError("take profit must be above the price for a long, below it for a short")
        with self.lock:
            fee = margin * leverage * FEE_RATE
            if not 0 < margin + fee <= self.state["cash"]:
                raise ValueError("not enough practice USDT for that margin")
            self.state["cash"] -= margin + fee
            pos = {"id": self.state["next_id"], "symbol": symbol, "side": side, "margin": margin,
                   "leverage": leverage, "entry": price, "stop_loss": stop_loss, "take_profit": take_profit,
                   "fees": fee, "opened": time.time()}
            self.state["next_id"] += 1
            self.state["open"].append(pos)
            self._save()
        return dict(pos)

    def close(self, pos_id: int, reason: str = "closed by hand", price: float | None = None) -> dict:
        with self.lock:
            pos = next((p for p in self.state["open"] if p["id"] == int(pos_id)), None)
            if pos is None:
                raise KeyError("no open practice position with that id")
            price = price if price is not None else self._mark(pos["symbol"])
            if price is None:
                raise RuntimeError("no live price right now, try again in a minute")
            fee = pos["margin"] * pos["leverage"] * FEE_RATE
            pnl = self._pnl(pos, price)
            pnl = max(pnl, -pos["margin"])  # isolated margin: can't lose more than the margin
            self.state["cash"] += pos["margin"] + pnl - fee
            self.state["open"].remove(pos)
            done = {**pos, "exit": price, "pnl": round(pnl - fee - pos["fees"], 2), "reason": reason,
                    "closed": time.time()}
            self.state["closed"] = (self.state["closed"] + [done])[-200:]
            self._save()
        return done

    def check_stops(self) -> list[dict]:
        """Close positions whose stop loss or take profit has been hit on the live price."""
        hits = []
        for pos in list(self.state["open"]):
            price = self._mark(pos["symbol"])
            if price is None:
                continue
            long = pos["side"] == "long"
            if (long and price <= pos["stop_loss"]) or (not long and price >= pos["stop_loss"]):
                hits.append(self.close(pos["id"], "stop loss", pos["stop_loss"]))
            elif pos.get("take_profit") and ((long and price >= pos["take_profit"]) or
                                             (not long and price <= pos["take_profit"])):
                hits.append(self.close(pos["id"], "take profit", pos["take_profit"]))
        return hits

    def snapshot(self) -> dict:
        self.check_stops()
        with self.lock:
            marks = {s: self._mark(s) for s in {p["symbol"] for p in self.state["open"]}}
            rows, upnl, margin = [], 0.0, 0.0
            for p in self.state["open"]:
                m = marks.get(p["symbol"])
                pnl = self._pnl(p, m) if m else 0.0
                upnl += pnl
                margin += p["margin"]
                rows.append({**p, "mark": m, "pnl": round(pnl, 2),
                             "roe": round(pnl / p["margin"] * 100, 1) if p["margin"] else 0.0})
            closed = self.state["closed"]
            wins = sum(1 for c in closed if c["pnl"] > 0)
            return {"cash": round(self.state["cash"], 2), "margin": round(margin, 2),
                    "equity": round(self.state["cash"] + margin + upnl, 2), "unrealized": round(upnl, 2),
                    "start": START_CASH, "open": rows, "closed": closed[-30:][::-1],
                    "win_rate": round(wins / len(closed) * 100, 1) if closed else None,
                    "realized": round(sum(c["pnl"] for c in closed), 2), "max_leverage": MAX_LEVERAGE}


# ---------------------------------------------------------------- news

def _get_text(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:  # noqa: S310 (fixed https feeds)
        return resp.read()


def parse_rss(xml: bytes, limit: int = 20) -> list[dict]:
    out = []
    try:
        root = ElementTree.fromstring(xml)
    except ElementTree.ParseError:
        return out
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        if not title or not link.startswith("https://"):
            continue
        src = item.find("source")
        out.append({"title": title, "link": link, "published": (item.findtext("pubDate") or "").strip(),
                    "source": (src.text or "").strip() if src is not None and src.text else "BTCC"})
        if len(out) >= limit:
            break
    return out


_news = {"at": 0.0, "items": []}
_news_lock = threading.Lock()


def news(get=_get_text, now=time.time) -> list[dict]:
    """BTCC headlines (announcements, listings, campaigns), cached NEWS_TTL seconds. Never raises."""
    with _news_lock:
        if _news["items"] and now() - _news["at"] < NEWS_TTL:
            return _news["items"]
        seen, items = set(), []
        for url in NEWS_FEEDS:
            try:
                for it in parse_rss(get(url)):
                    key = it["title"].lower()[:80]
                    if key not in seen:
                        seen.add(key)
                        items.append(it)
            except Exception:  # noqa: BLE001
                continue
        if items:
            _news.update(at=now(), items=items[:30])
        return _news["items"]
