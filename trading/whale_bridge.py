"""Unusual Whales -> 0DTE whale-flow feed (option_flows.FEED), so $3M+ opening prints reach the desks by themselves.

Every minute during US regular hours (09:30-16:00 New York, 01:30-08:00 Melbourne in summer) the bridge asks
Unusual Whales for new flow alerts (GET https://api.unusualwhales.com/api/option-trades/flow-alerts, bearer
UW_API_TOKEN) filtered on the server: min_premium 3,000,000, max_dte 0, all_opening true, our tickers only.
Each alert becomes an option_flows.FlowPrint and goes through the existing decoder, which inverts put polarity
and weighs key levels. Prints are also written to $EVENTS_DIR/whale_prints.jsonl so the engine processes see them.

Fields read from each alert (UW's flow alert: ticker, option_chain, type, strike, expiry, total_premium,
total_ask_side_prem, total_bid_side_prem, volume, open_interest, underlying_price, all_opening_trades, id,
created_at / executed_at). Missing strike, type or expiry are taken from the OCC option_chain
(e.g. SPXW251125C06700000). Buy or sell: ask-side premium bigger than bid-side = bought, else sold.
SPX's index price comes back empty from UW, so spot falls back to our own Yahoo price.

Key levels: the prior day's high, low and its typical price (high + low + close) / 3 as the VWAP stand-in
(Yahoo's free daily bars carry no intraday volume profile). The nearest of the three to spot is the key level.

Budget: one call a minute, 390 minutes a day, about 8,200 calls a month on 21 trading days, under the
25,000-a-month Starter cap. UW_MAX_CALLS_DAY (default 450) is a hard stop per New York day.

Environment: UW_API_TOKEN (Render), UW_MAX_CALLS_DAY optional, WHALE_BRIDGE=off to stop it.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from . import option_flows

URL = "https://api.unusualwhales.com/api/option-trades/flow-alerts"
TICKERS = ("SPX", "SPXW", "SPY", "QQQ", "NVDA", "AAPL", "MSFT", "AMZN", "META", "GOOGL", "TSLA")
MIN_PREMIUM = 3_000_000
NY = ZoneInfo("America/New_York")
YAHOO = {"SPX": "%5EGSPC", "SPXW": "%5EGSPC"}
OCC = re.compile(r"^([A-Z.]+?)(\d{6})([CP])(\d{8})$")
HTTP_TIMEOUT = 15


def _http(url: str, headers: dict) -> dict:
    req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "ats-matrix-whale-bridge/1.0",
                                               **headers})
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:  # noqa: S310 (fixed https hosts)
        return json.loads(r.read() or b"null")


def us_rth_open(now: datetime | None = None) -> bool:
    """US regular hours, 09:30-16:00 New York time on weekdays (holidays just return no alerts)."""
    t = (now or datetime.now(timezone.utc)).astimezone(NY)
    if t.weekday() >= 5:
        return False
    mins = t.hour * 60 + t.minute
    return 9 * 60 + 30 <= mins < 16 * 60


def parse_occ(chain: str) -> dict:
    """'SPXW251125C06700000' -> {'root': 'SPXW', 'expiry': date(2025, 11, 25), 'type': 'CALL', 'strike': 6700.0}."""
    m = OCC.match(str(chain or "").strip().upper())
    if not m:
        return {}
    root, ymd, cp, strike = m.groups()
    return {"root": root, "expiry": date(2000 + int(ymd[:2]), int(ymd[2:4]), int(ymd[4:])),
            "type": "CALL" if cp == "C" else "PUT", "strike": int(strike) / 1000}


def _num(v, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _when(row: dict) -> float:
    for k in ("executed_at", "created_at", "start_time"):
        v = row.get(k)
        if v in (None, ""):
            continue
        if isinstance(v, (int, float)) or str(v).isdigit():
            x = float(v)
            return x / 1000 if x > 1e11 else x
        try:
            return datetime.fromisoformat(str(v).replace("Z", "+00:00")).timestamp()
        except ValueError:
            continue
    return time.time()


def key_level(spot: float, levels: dict | None) -> float | None:
    vals = [v for v in (levels or {}).values() if v]
    return min(vals, key=lambda v: abs(v - spot)) if vals and spot else None


def to_print(row: dict, levels: dict[str, dict], spots: dict[str, float] | None = None,
             today: date | None = None) -> option_flows.FlowPrint | None:
    """One UW flow alert -> FlowPrint, or None if it isn't a $3M+ 0DTE opening print on our list."""
    occ = parse_occ(row.get("option_chain", ""))
    under = str(row.get("ticker") or occ.get("root") or "").upper()
    under = "SPX" if under == "SPXW" else under
    if under not in TICKERS:
        return None
    if row.get("all_opening_trades") is False:
        return None
    premium = _num(row.get("total_premium"))
    if premium < MIN_PREMIUM:
        return None
    kind = str(row.get("type") or occ.get("type") or "").upper()
    kind = "CALL" if kind.startswith("C") else "PUT" if kind.startswith("P") else ""
    if not kind:
        return None
    expiry = occ.get("expiry")
    if row.get("expiry"):
        try:
            expiry = date.fromisoformat(str(row["expiry"])[:10])
        except ValueError:
            pass
    today = today or datetime.now(timezone.utc).astimezone(NY).date()
    dte = (expiry - today).days if expiry else 0
    ask, bid = _num(row.get("total_ask_side_prem")), _num(row.get("total_bid_side_prem"))
    action = "SELL_TO_OPEN" if bid > ask else "BUY_TO_OPEN"
    spot = _num(row.get("underlying_price")) or (spots or {}).get(under, 0.0)
    return option_flows.FlowPrint(
        underlying=under, option_type=kind, action=action, premium=premium, dte=max(dte, 0),
        strike=_num(row.get("strike")) or occ.get("strike", 0.0), volume=int(_num(row.get("volume"))),
        open_interest=int(_num(row.get("open_interest"))), spot=spot, key_level=key_level(spot, levels.get(under)),
        at=_when(row))


def daily_levels(symbol: str, get=None) -> tuple[dict, float]:
    """Prior day's high, low and typical price (VWAP stand-in) from Yahoo, plus the latest price."""
    get = get or (lambda u: _http(u, {}))
    data = get(f"https://query1.finance.yahoo.com/v8/finance/chart/{YAHOO.get(symbol, symbol)}?interval=1d&range=5d")
    r = data["chart"]["result"][0]
    q = r["indicators"]["quote"][0]
    bars = [(h, lo, c) for h, lo, c in zip(q["high"], q["low"], q["close"]) if h and lo and c]
    spot = _num(r.get("meta", {}).get("regularMarketPrice")) or (bars[-1][2] if bars else 0.0)
    if len(bars) < 2:
        return {}, spot
    h, lo, c = bars[-2]  # the last full day before today's bar
    return {"prior_high": h, "prior_low": lo, "prior_vwap": round((h + lo + c) / 3, 2)}, spot


class WhaleBridge:
    def __init__(self, env=os.environ, feed: option_flows.FlowFeed | None = None, http=_http, yahoo=None,
                 folder=None, clock=time.time):
        self.token = str(env.get("UW_API_TOKEN", "") or "").strip()
        self.max_day = int(_num(env.get("UW_MAX_CALLS_DAY"), 450) or 450)
        self.feed = feed or option_flows.FEED
        self.http, self.yahoo, self.clock = http, yahoo, clock
        self.folder = folder if folder is not None else env.get("EVENTS_DIR")
        self.seen: dict[str, float] = {}
        self.newest = 0.0
        self.levels: dict[str, dict] = {}
        self.spots: dict[str, float] = {}
        self.levels_at = 0.0
        self.calls_day, self.calls, self.day = "", 0, ""
        self.last: dict = {"state": "off" if not self.token else "waiting", "error": None, "at": None, "ingested": 0}
        self.total = 0

    def __repr__(self) -> str:
        return "WhaleBridge(unusual whales)"

    def refresh_levels(self, force: bool = False) -> None:
        if not force and self.clock() - self.levels_at < 900:
            return
        self.levels_at = self.clock()
        for s in ("SPX", "SPY", "QQQ", "NVDA", "AAPL", "MSFT", "AMZN", "META", "GOOGL", "TSLA"):
            try:
                self.levels[s], self.spots[s] = daily_levels(s, self.yahoo)
            except Exception:  # noqa: BLE001  (one symbol missing never stops the rest)
                continue

    def _budget_ok(self) -> bool:
        day = datetime.fromtimestamp(self.clock(), timezone.utc).astimezone(NY).strftime("%Y-%m-%d")
        if day != self.day:
            self.day, self.calls = day, 0
        return self.calls < self.max_day

    def poll(self) -> list[dict]:
        """One call to Unusual Whales; returns the decoded prints that went into the feed."""
        if not self.token:
            self.last["state"] = "off"
            return []
        if not self._budget_ok():
            self.last.update(state="budget", error=f"daily cap of {self.max_day} calls reached")
            return []
        self.refresh_levels()
        q = {"min_premium": MIN_PREMIUM, "max_dte": 0, "all_opening": "true", "limit": 200,
             "ticker_symbol": ",".join(TICKERS)}
        if self.newest:
            q["newer_than"] = int(self.newest)
        self.calls += 1
        try:
            res = self.http(f"{URL}?{urllib.parse.urlencode(q)}", {"Authorization": f"Bearer {self.token}"})
        except Exception as e:  # noqa: BLE001
            code = getattr(e, "code", None)
            self.last.update(state="error", at=self.clock(),
                             error="UW token rejected: check UW_API_TOKEN on Render" if code in (401, 403)
                             else "UW rate limit hit" if code == 429 else f"{type(e).__name__}")
            return []
        rows = res.get("data", []) if isinstance(res, dict) else res if isinstance(res, list) else []
        out = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            rid = str(row.get("id") or f"{row.get('option_chain')}|{row.get('executed_at') or row.get('created_at')}|{row.get('total_premium')}")
            if rid in self.seen:
                continue
            self.seen[rid] = self.clock()
            p = to_print(row, self.levels, self.spots)
            if p is None:
                continue
            self.newest = max(self.newest, p.at)
            d = self.feed.ingest(p)
            self.feed.record(p, self.folder)
            out.append({"underlying": p.underlying, "type": p.option_type, "action": p.action,
                        "premium": p.premium, "bias": d.bias, "score": d.score, "at_key_level": d.at_key_level})
        if len(self.seen) > 5000:
            cut = sorted(self.seen.values())[-2000]
            self.seen = {k: v for k, v in self.seen.items() if v >= cut}
        self.total += len(out)
        self.last.update(state="live", error=None, at=self.clock(), ingested=len(out))
        return out

    def status(self) -> dict:
        return {**self.last, "calls_today": self.calls, "cap_today": self.max_day, "total_ingested": self.total,
                "us_hours": us_rth_open(), "levels": self.levels, "source": "Unusual Whales flow alerts"}

    def run(self, stop: threading.Event | None = None, every: float = 60.0) -> None:
        stop = stop or threading.Event()
        while not stop.is_set():
            if us_rth_open():
                try:
                    self.poll()
                except Exception as e:  # noqa: BLE001  (never let the loop die)
                    self.last.update(state="error", error=type(e).__name__)
            elif self.token:
                self.last["state"] = "market closed"
            stop.wait(every)


def start(env=os.environ) -> WhaleBridge:
    """Start the 60-second loop in the web process when UW_API_TOKEN is set."""
    bridge = WhaleBridge(env)
    if bridge.token and str(env.get("WHALE_BRIDGE", "on")).lower() not in ("off", "0", "false", "no"):
        threading.Thread(target=bridge.run, daemon=True, name="whale-bridge").start()
    return bridge
