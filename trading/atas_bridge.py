"""ATAS orders and order flow through the ATAS MCP Bridge (github.com/Ax3lsk3r3/atas-mcp), with hard guards.

The bridge is a strategy that runs inside ATAS and listens on http://127.0.0.1:8787 on the owner's computer.
Only something on that same computer can reach it (the JARVIS desktop app, Claude Code, or the MCP server in
tools/atas/bridge_mcp.py). The cloud app on Render can't reach it.

Every order goes through `AtasBridge.place_order`, and these checks run in code, so no prompt can get around them:
  - Demo mode (the app's Demo/Live switch): orders only go to an ATAS portfolio whose name says demo, sim,
    paper, practice or test.
  - Live mode: needs all three of the app's switch on LIVE, LIVE_MONEY=on on Render, and
    ATAS_ALLOW_LIVE_ORDERS=true on this computer. Each one is off by default.
  - Kill switch: when the app's KILL is set, no new orders go out.
  - Size: qty at most ATAS_MAX_QTY (default 1, never above 10 contracts).
  - Symbols: chart symbol must be one of ATAS_ALLOWED_SYMBOLS (default NQ,MNQ,ES,MES; a month code like H27 is ok).
  - Every entry needs a stop loss on the right side of the price. A protective stop order is sent right after the
    entry fills.
  - At most ATAS_MAX_ORDERS_PER_DAY entries (default 10).
  - Every attempt is written to the audit log (~/.jarvisfeed/atas_orders.jsonl), whether it was sent or blocked.
Cancelling and flattening always work, because they only reduce risk.

The original bridge has no password. The Connect ATAS installer builds a locked copy
(trading/atas_install/McpBridgeStrategy.cs). That copy needs the X-Bridge-Token header on every call and refuses web
pages. The installer saves the token in ~/.jarvisfeed/config as bridge_token, and from_config() picks it up.
"""

from __future__ import annotations

import json
import math
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable

DEFAULT_URL = "http://127.0.0.1:8787"
DEFAULT_SYMBOLS = "NQ,MNQ,ES,MES"
HARD_MAX_QTY = 10.0
TYPES = {"market": "market", "limit": "limit", "stop": "stop", "stoplimit": "stoplimit", "stop_limit": "stoplimit"}
DEMO_WORDS = re.compile(r"demo|sim|paper|practice|test", re.I)
MONTH = r"[FGHJKMNQUVXZ]\d{1,2}"
LOOPBACK = {"127.0.0.1", "localhost", "::1"}


class GuardError(RuntimeError):
    """A pre-trade check blocked the order. The message says which one, in plain words."""


def _flag(v) -> bool:
    return str(v or "").strip().lower() in ("1", "true", "yes", "on")


def _num(v, name: str) -> float:
    try:
        f = float(v)
    except (TypeError, ValueError):
        raise GuardError(f"{name} must be a number") from None
    if not math.isfinite(f):
        raise GuardError(f"{name} must be a finite number")
    return f


def bridge_url(raw: str | None) -> str:
    """The bridge address. Only this computer's own address is allowed, so orders can't be sent to another machine."""
    url = (raw or DEFAULT_URL).strip().rstrip("/")
    parts = urllib.parse.urlsplit(url)
    if parts.scheme != "http" or (parts.hostname or "") not in LOOPBACK:
        raise GuardError("ATAS_BRIDGE_URL must be this computer's own address, like http://127.0.0.1:8787")
    return url


def symbol_allowed(symbol: str, allowed: set[str]) -> bool:
    s = (symbol or "").strip().upper()
    root = re.sub(rf"{MONTH}$", "", s)
    return bool(s) and (s in allowed or root in allowed)


class AtasBridge:
    def __init__(self, env=None, mode: Callable[[], dict] | None = None, opener=None, clock=time.time,
                 audit_path: str | Path | None = None, timeout: float = 3.0):
        env = os.environ if env is None else env
        self.base = bridge_url(env.get("ATAS_BRIDGE_URL"))
        self.token = env.get("ATAS_BRIDGE_TOKEN", "")
        self.allow_live = _flag(env.get("ATAS_ALLOW_LIVE_ORDERS"))
        self.max_qty = min(HARD_MAX_QTY, max(0.0, _num(env.get("ATAS_MAX_QTY") or 1, "ATAS_MAX_QTY")))
        self.allowed = {s.strip().upper() for s in (env.get("ATAS_ALLOWED_SYMBOLS") or DEFAULT_SYMBOLS).split(",")
                        if s.strip()}
        self.max_per_day = int(_num(env.get("ATAS_MAX_ORDERS_PER_DAY") or 10, "ATAS_MAX_ORDERS_PER_DAY"))
        # The app's Demo/Live switch. Anything unknown counts as demo with the kill switch on: fail closed.
        self.mode = mode or (lambda: {"mode": "demo", "armed": False, "kill": False})
        self.opener = opener or urllib.request.urlopen
        self.clock = clock
        self.timeout = timeout
        self.audit_path = Path(audit_path or Path.home() / ".jarvisfeed" / "atas_orders.jsonl")
        self.lock = threading.Lock()
        self.sent_today: list[float] = []

    # --- talking to the bridge ---
    def _request(self, method: str, path: str, payload: dict | None = None) -> dict:
        data = json.dumps(payload).encode() if payload is not None else None
        headers = {"Content-Type": "application/json"} if data is not None else {}
        if self.token:
            headers["X-Bridge-Token"] = self.token
        req = urllib.request.Request(self.base + path, data=data, headers=headers, method=method)
        try:
            with self.opener(req, timeout=self.timeout) as resp:
                return json.loads(resp.read().decode() or "{}")
        except urllib.error.HTTPError as e:
            try:
                return json.loads(e.read().decode() or "{}") | {"ok": False, "http": e.code}
            except ValueError:
                return {"ok": False, "http": e.code}
        except (urllib.error.URLError, OSError) as e:
            raise ConnectionError(f"ATAS bridge not reachable at {self.base}: open ATAS, add the MCP Bridge strategy "
                                  "to a chart and tick IsActivated") from e

    def _audit(self, event: str, **details: Any) -> None:
        rec = {"ts": round(self.clock(), 3), "event": event, **details}
        try:
            self.audit_path.parent.mkdir(parents=True, exist_ok=True)
            with self.audit_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec, default=str) + "\n")
        except OSError:
            pass  # a full disk must never turn a block into a send

    # --- read only ---
    def health(self) -> dict: return self._request("GET", "/api/health")
    def status(self) -> dict: return self._request("GET", "/api/status")
    def quote(self) -> dict: return self._request("GET", "/api/quote")
    def dom(self) -> dict: return self._request("GET", "/api/dom")
    def candles(self) -> dict: return self._request("GET", "/api/candles")
    def position(self) -> dict: return self._request("GET", "/api/position")
    def orders(self) -> dict: return self._request("GET", "/api/orders")
    def trades(self) -> dict: return self._request("GET", "/api/trades")
    def snapshot(self) -> dict: return self._request("GET", "/api/snapshot")
    def log(self) -> dict: return self._request("GET", "/api/log")

    # --- the guards ---
    def _gate(self, status: dict) -> dict:
        """Demo/Live, kill switch and symbol checks against what the bridge says is on the chart."""
        sw = {"mode": "demo", "armed": False, "kill": True}
        try:
            sw |= dict(self.mode() or {})
        except Exception:  # noqa: BLE001  (switch unreachable: stay demo with the kill switch on)
            pass
        if sw.get("kill"):
            raise GuardError("kill switch is on (or the app's switch couldn't be read): no new orders")
        if not status.get("activated", True):
            raise GuardError("the MCP Bridge strategy isn't activated in ATAS")
        portfolio = str(status.get("portfolio") or "")
        demo_book = bool(DEMO_WORDS.search(portfolio))
        if sw.get("mode") == "live":
            if not (sw.get("armed") and self.allow_live):
                raise GuardError("live needs the app on LIVE, LIVE_MONEY=on on Render and ATAS_ALLOW_LIVE_ORDERS=true "
                                 "on this computer")
        elif not demo_book:
            raise GuardError(f"the app is on DEMO but ATAS portfolio '{portfolio or '?'}' isn't a demo/sim account: "
                             "pick a Sim or Demo portfolio in the MCP Bridge settings")
        symbol = str(status.get("symbol") or "")
        if not symbol_allowed(symbol, self.allowed):
            raise GuardError(f"symbol '{symbol or '?'}' isn't in ATAS_ALLOWED_SYMBOLS ({', '.join(sorted(self.allowed))})")
        return {"mode": sw.get("mode"), "portfolio": portfolio, "symbol": symbol}

    def _ref_price(self, order_type: str, price: float | None, trigger: float | None) -> float:
        if order_type == "limit" or order_type == "stoplimit":
            return price  # type: ignore[return-value]
        if order_type == "stop":
            return trigger  # type: ignore[return-value]
        q = self.quote()
        for k in ("last", "lastPrice", "price"):
            if isinstance(q.get(k), (int, float)) and q[k] > 0:
                return float(q[k])
        raise GuardError("no live price from ATAS to check the stop loss against")

    def place_order(self, direction: str, order_type: str, qty, stop_loss, price=None, trigger_price=None,
                    comment: str = "jarvis") -> dict:
        d = str(direction or "").strip().lower()
        t = TYPES.get(str(order_type or "").strip().lower())
        ask = {"direction": d, "type": order_type, "qty": qty, "price": price, "trigger": trigger_price, "stop": stop_loss}
        try:
            if d not in ("buy", "sell"):
                raise GuardError("direction must be buy or sell")
            if not t:
                raise GuardError("order type must be market, limit, stop or stoplimit")
            q = _num(qty, "qty")
            if q <= 0 or q > self.max_qty:
                raise GuardError(f"qty {q:g} is over the cap of {self.max_qty:g} (ATAS_MAX_QTY)")
            p = _num(price, "price") if price is not None else None
            tr = _num(trigger_price, "trigger price") if trigger_price is not None else None
            if t in ("limit", "stoplimit") and not p:
                raise GuardError(f"{t} orders need a price")
            if t in ("stop", "stoplimit") and not tr:
                raise GuardError(f"{t} orders need a trigger price")
            if stop_loss is None:
                raise GuardError("every order needs a stop loss")
            sl = _num(stop_loss, "stop loss")
            with self.lock:
                day = self.clock() - 86400
                self.sent_today = [x for x in self.sent_today if x > day]
                if len(self.sent_today) >= self.max_per_day:
                    raise GuardError(f"daily limit of {self.max_per_day} ATAS orders reached (ATAS_MAX_ORDERS_PER_DAY)")
            where = self._gate(self.status())
            ref = self._ref_price(t, p, tr)
            if (d == "buy" and sl >= ref) or (d == "sell" and sl <= ref):
                raise GuardError(f"stop loss {sl:g} is on the wrong side of {ref:g} for a {d}")
        except GuardError as e:
            self._audit("blocked", reason=str(e), **ask)
            raise
        body: dict[str, Any] = {"direction": d, "type": t, "qty": q, "comment": str(comment)[:40]}
        if p is not None:
            body["price"] = p
        if tr is not None:
            body["triggerPrice"] = tr
        with self.lock:
            self.sent_today.append(self.clock())
        self._audit("sent", **where, **body, stop=sl)
        res = self._request("POST", "/api/order", body)
        protect = None
        if res.get("ok", True):
            stop_body = {"direction": "sell" if d == "buy" else "buy", "type": "stop", "qty": q, "triggerPrice": sl,
                         "comment": "jarvis-stop"}
            protect = self._request("POST", "/api/order", stop_body)
            self._audit("stop_sent", **where, **stop_body, answer=protect)
        self._audit("answer", answer=res)
        return {"ok": bool(res.get("ok", True)), "entry": res, "stop": protect, **where}

    def cancel_order(self, order_id: str) -> dict:
        oid = str(order_id or "").strip()
        if not oid:
            raise GuardError("order id required")
        self._audit("cancel", id=oid)
        return self._request("POST", "/api/order/cancel", {"id": oid})

    def close_position(self) -> dict:
        self._audit("flatten")
        return self._request("POST", "/api/position/close", {})


def app_switch(url: str, token: str, opener=None, timeout: float = 5.0) -> Callable[[], dict]:
    """Reads the app's Demo/Live switch and kill switch (GET /api/atas/mode with the ATAS feed token)."""
    opener = opener or urllib.request.urlopen

    def read() -> dict:
        req = urllib.request.Request(url.rstrip("/") + "/api/atas/mode", headers={"Authorization": f"Bearer {token}"})
        with opener(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    return read


def from_config(env=None) -> AtasBridge:
    """The bridge client for this computer, using the app link and token the Connect ATAS installer saved."""
    env = dict(os.environ if env is None else env)
    cfg = Path.home() / ".jarvisfeed" / "config"
    saved: dict[str, str] = {}
    if cfg.exists():
        for line in cfg.read_text().splitlines():
            k, _, v = line.partition("=")
            saved[k.strip()] = v.strip()
    url, token = env.get("JARVIS_APP_URL") or saved.get("url"), env.get("ATAS_FEED_TOKEN") or saved.get("token")
    if saved.get("bridge_token") and not env.get("ATAS_BRIDGE_TOKEN"):
        env["ATAS_BRIDGE_TOKEN"] = saved["bridge_token"]  # the locked bridge's password, set by the installer
    mode = app_switch(url, token) if url and token else None
    return AtasBridge(env, mode=mode)
