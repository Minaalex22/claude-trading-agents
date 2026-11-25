"""One account: every platform, every session desk and ATAS joined into one balance, one book and one set of limits.

Sources (each one is optional; a platform that is not connected just shows as such):
  agents' paper desk, practice wallet cash, Mitrade practice desk, BTCC practice desk, Polymarket paper book,
  Betfair paper book, MetaTrader 5 (MetaApi, any broker), eToro (demo or real portfolio, read with the owner's keys),
  Interactive Brokers (Client Portal gateway), ATAS. Keys for each: trading/live_apis.py.

What "one account" means here:
- One balance: equity of every connected account added up, with its starting money, so P&L is one number.
- One book: every open position from every platform in one list, netted by symbol across platforms.
- One set of risk limits, checked before any order from any platform:
    2% daily loss on the combined account (the owner's money rule) stops new risk for the day,
    never two positions in the same symbol (also across platforms), a cap on open positions,
    gross exposure at most GROSS_LIMIT x combined equity, and a file named KILL in EVENTS_DIR stops everything.
- Session desks: every closed trade from every platform is credited to the desk (Sydney, Asia, Europe,
  London, New York) that was leading when it closed, so each desk's P&L covers all platforms.

Demo / Live switch:
- Starts on Demo. Demo sends orders to the broker's own practice account (MT5 demo, eToro virtual) or our
  practice desks (Mitrade, BTCC, Polymarket, Betfair paper).
- Live needs: LIVE_MONEY=on on Render (the owner arms it once), the platform's keys on Render, and the owner
  typing LIVE_CONFIRM on the page. Switching back to Demo is instant and needs nothing.
- In Live, every order also needs a stop loss, is capped at LIVE_MAX_ORDER_USD (default $100) and at 1% of the
  live balance at risk, at most LIVE_MAX_OPEN (2) live positions, and the combined limits above.
- Platforms with no trading API (Mitrade, BTCC since Feb 2026) and Polymarket (not legal in Australia) stay on
  practice even in Live, and the page says so.
"""

from __future__ import annotations

import json
import os
import threading
import time
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path

from . import session_desks

LIVE_CONFIRM = "GO LIVE"
MODES = ("demo", "live")
DAILY_LOSS = 0.02
GROSS_LIMIT = 3.0
MAX_OPEN = 20
LIVE_MAX_OPEN = 2
LIVE_RISK = 0.01
HTTP_TIMEOUT = 15


class Refused(PermissionError):
    pass


def armed(env) -> bool:
    return str(env.get("LIVE_MONEY", "")).strip().lower() == "on"


def live_max_order(env) -> float:
    try:
        return max(0.0, float(env.get("LIVE_MAX_ORDER_USD") or 100))
    except ValueError:
        return 100.0


# ---------------------------------------------------------------- the switch

class ModeSwitch:
    """Demo or Live, saved in $EVENTS_DIR/account_mode.json with a history of every flip."""

    def __init__(self, folder, clock=time.time):
        self.path = Path(folder or ".") / "account_mode.json"
        self.clock = clock
        self.lock = threading.Lock()
        try:
            d = json.loads(self.path.read_text())
            assert d.get("mode") in MODES and isinstance(d.get("history"), list)
            self.data = d
        except (OSError, ValueError, AssertionError, AttributeError):
            self.data = {"mode": "demo", "since": clock(), "history": []}

    @property
    def mode(self) -> str:
        return self.data["mode"]

    def set(self, mode: str, confirm: str = "", env=os.environ) -> dict:
        if mode not in MODES:
            raise Refused("mode must be demo or live")
        if mode == "live":
            if not armed(env):
                raise Refused("Live is not armed. Add LIVE_MONEY=on in Render > Environment once, then flip again.")
            if str(confirm).strip().upper() != LIVE_CONFIRM:
                raise Refused(f'Type "{LIVE_CONFIRM}" to switch to real money.')
        with self.lock:
            if mode != self.data["mode"]:
                now = self.clock()
                self.data["history"] = (self.data["history"] + [{"ts": round(now), "to": mode}])[-100:]
                self.data["mode"], self.data["since"] = mode, now
                try:
                    self.path.parent.mkdir(parents=True, exist_ok=True)
                    self.path.write_text(json.dumps(self.data))
                except OSError:
                    pass
            return {"mode": self.data["mode"], "since": self.data["since"]}


# ---------------------------------------------------------------- where orders go

def routes(env, mode: str) -> list[dict]:
    """For each platform: where an order goes in the current mode, and why."""
    def has(*keys):
        return all(env.get(k) for k in keys)

    live_on = mode == "live" and armed(env)
    rows = [
        ("mt5", "MetaTrader 5 (any broker)", has("METAAPI_TOKEN"), has("METAAPI_TOKEN"),
         "MT5 demo account (MetaApi)", "your real MT5 account (MetaApi; found from the token or METAAPI_LIVE_ACCOUNT_ID)",
         "add METAAPI_TOKEN"),
        ("etoro", "eToro", has("ETORO_API_KEY", "ETORO_USER_KEY"), has("ETORO_API_KEY", "ETORO_USER_KEY"),
         "eToro virtual portfolio", "your real eToro account", "add ETORO_API_KEY and ETORO_USER_KEY"),
        ("ibkr", "Interactive Brokers", has("IBKR_GATEWAY_URL"), has("IBKR_GATEWAY_URL"),
         "IBKR paper account (gateway signed in with the paper login)", "your real IBKR account (gateway signed in with the real login)",
         "add IBKR_GATEWAY_URL (your signed-in IBKR gateway)"),
        ("moomoo", "moomoo", True, has("BRIDGE_TOKEN", "LIVE_SYMBOLS", "LIVE_MAX_ORDER_USD"),
         "agents' paper desk", "your real moomoo account (approve each order; bridge on your computer)",
         "add BRIDGE_TOKEN, LIVE_SYMBOLS, LIVE_MAX_ORDER_USD and run the bridge next to moomoo OpenD"),
        ("betfair", "Betfair (AU)", True, has("BETFAIR_APP_KEY", "BETFAIR_USERNAME", "BETFAIR_PASSWORD"),
         "Betfair paper book at real Betfair prices", "your real Betfair account (bets on /polymarket)",
         "add BETFAIR_APP_KEY, BETFAIR_USERNAME, BETFAIR_PASSWORD"),
        ("mitrade", "Mitrade", True, False, "Mitrade practice desk at Mitrade's prices", None,
         "Mitrade has no trading API, so it stays on practice"),
        ("btcc", "BTCC", True, False, "BTCC practice desk", None,
         "BTCC paused its trading API on 13 Feb 2026; turns on if it comes back"),
        ("polymarket", "Polymarket", True, False, "Polymarket paper bets at real prices", None,
         "Polymarket is not legal in Australia; use Betfair for real bets"),
        ("atas", "ATAS", True, False, "order flow into every decision", None,
         "ATAS sends data only; its trades run on the broker you connect inside ATAS"),
    ]
    out = []
    for key, name, demo_ok, live_ready, demo_to, live_to, why in rows:
        live = live_on and live_ready
        out.append({"key": key, "name": name, "goes_to": live_to if live else demo_to,
                    "mode": "live" if live else ("demo" if key in ("mt5", "etoro", "ibkr") and demo_ok else "practice"),
                    "live_ready": live_ready and armed(env), "live_possible": live_to is not None,
                    "why": None if live_ready and armed(env) else (why if live_to is None or not live_ready else "add LIVE_MONEY=on")})
    return out


# ---------------------------------------------------------------- sources -> rows

def _row(key, name, kind, state, start=None, equity=None, unrealized=0.0, realized=0.0, positions=None,
         closed=None, note="", currency="USD") -> dict:
    pnl = (equity - start) if equity is not None and start is not None else (realized + unrealized)
    return {"key": key, "name": name, "kind": kind, "state": state, "start": start, "equity": equity,
            "pnl": round(pnl, 2), "unrealized": round(unrealized or 0.0, 2), "realized": round(realized or 0.0, 2),
            "positions": positions or [], "closed": closed or [], "note": note, "currency": currency}


def from_desk(trades: dict | None) -> dict:
    t = (trades or {}).get("totals") or {}
    closed = [{"platform": "desk", "symbol": x.get("symbol"), "pnl": float(x.get("pnl") or 0), "ts": x.get("ts"),
               "won": bool(x.get("won"))} for x in (trades or {}).get("trades") or []]
    return _row("desk", "Agents' paper desk", "practice", "connected", realized=float(t.get("pnl") or 0), closed=closed,
                note=f"{t.get('trades', 0)} closed trades by the agents")


def from_wallet(cash: float | None, start: float | None) -> dict:
    return _row("wallet", "Practice wallet", "practice", "connected", start=start, equity=cash,
                note="practice cash you can add or take out on /wallet")


def from_cfd(key: str, name: str, snap: dict | None) -> dict:
    if not snap:
        return _row(key, name, "practice", "idle", note="opens when you first use its page")
    pos = [{"platform": key, "symbol": p["symbol"], "side": p["side"], "notional": round(p["margin"] * p["leverage"], 2),
            "pnl": p.get("pnl", 0.0), "entry": p.get("entry"), "mark": p.get("mark"), "stop_loss": p.get("stop_loss")}
           for p in snap.get("open", [])]
    closed = [{"platform": key, "symbol": c["symbol"], "pnl": c["pnl"], "ts": c.get("closed"), "won": c["pnl"] > 0}
              for c in snap.get("closed", [])]
    return _row(key, name, "practice", "connected", start=snap.get("start"), equity=snap.get("equity"),
                unrealized=snap.get("unrealized", 0.0), realized=snap.get("realized", 0.0), positions=pos, closed=closed,
                note="practice desk")


def from_poly(snap: dict | None) -> dict:
    if not snap:
        return _row("polymarket", "Polymarket paper book", "practice", "idle", note="opens when you first use /polymarket")
    pos = [{"platform": "polymarket", "symbol": str(p.get("question") or p.get("market_id"))[:40], "side": p.get("side", "YES"),
            "notional": p.get("value", 0.0), "pnl": p.get("pnl", 0.0)} for p in snap.get("open", [])]
    closed = [{"platform": "polymarket", "symbol": str(c.get("question") or c.get("market_id"))[:40], "pnl": c.get("pnl", 0.0),
               "ts": c.get("closed") or c.get("at"), "won": c.get("pnl", 0) > 0} for c in snap.get("closed", [])]
    return _row("polymarket", "Polymarket paper book", "practice", "connected", start=snap.get("start"),
                equity=snap.get("equity"), unrealized=snap.get("unrealised", 0.0), realized=snap.get("realised", 0.0),
                positions=pos, closed=closed, note="real prices, paper bets")


def from_betfair(snap: dict | None, live: bool = False) -> dict:
    if not snap:
        return _row("betfair", "Betfair paper book", "practice", "idle", note="opens when you first use /polymarket")
    pos = [{"platform": "betfair", "symbol": str(b.get("runner") or b.get("market") or b.get("market_id"))[:40],
            "side": b.get("side", "back"), "notional": b.get("liability") or b.get("stake") or 0.0, "pnl": 0.0}
           for b in snap.get("open", [])]
    closed = [{"platform": "betfair", "symbol": str(c.get("runner") or c.get("market_id"))[:40], "pnl": c.get("pnl", 0.0),
               "ts": c.get("closed") or c.get("at"), "won": c.get("pnl", 0) > 0} for c in snap.get("closed", [])]
    open_stake = sum(p["notional"] for p in pos)
    return _row("betfair", "Betfair paper book", "practice", "connected", start=snap.get("start"),
                equity=round(float(snap.get("cash") or 0) + open_stake, 2), realized=snap.get("pnl", 0.0),
                positions=pos, closed=closed, note=f"{snap.get('bets', 0)} settled paper bets")


def from_mt5(snap: dict | None, error: str | None = None, live: bool = False) -> dict:
    name = "MetaTrader 5 " + ("real" if live else "demo")
    if error:
        return _row("mt5", name, "broker", "error", note=error)
    if not snap:
        return _row("mt5", name, "broker", "not_connected", note="add METAAPI_TOKEN in Render > Environment")
    acc = snap.get("account") or {}
    pos = []
    for p in snap.get("positions") or []:
        side = "buy" if "BUY" in str(p.get("type", "")) else "sell"
        price = p.get("currentPrice") or p.get("openPrice") or 0
        pos.append({"platform": "mt5", "id": p.get("id"), "symbol": p.get("symbol"), "side": side, "lots": p.get("volume"),
                    "notional": None, "pnl": float(p.get("profit") or 0), "entry": p.get("openPrice"), "mark": price,
                    "stop_loss": p.get("stopLoss")})
    upnl = sum(p["pnl"] for p in pos)
    bal, eq = acc.get("balance"), acc.get("equity")
    return _row("mt5", name, "broker", "connected", start=bal, equity=eq, unrealized=upnl, positions=pos,
                note=f"{acc.get('broker') or 'MT5'} · {'demo' if acc.get('demo') else 'REAL'} · {acc.get('currency') or 'USD'}",
                currency=acc.get("currency") or "USD")


def from_etoro(portfolio: dict | None, error: str | None = None, live: bool = False) -> dict:
    name = "eToro " + ("real" if live else "virtual")
    if error:
        return _row("etoro", name, "broker", "error", note=error)
    if not portfolio:
        return _row("etoro", name, "broker", "not_connected", note="add ETORO_API_KEY and ETORO_USER_KEY")
    cp = portfolio.get("clientPortfolio") or portfolio
    pos = []
    for p in cp.get("positions") or []:
        u = p.get("unrealizedPnL") or {}
        pnl = float(u.get("pnL") if isinstance(u, dict) and u.get("pnL") is not None else p.get("pnL") or 0)
        pos.append({"platform": "etoro", "id": p.get("positionID") or p.get("positionId"),
                    "instrument_id": p.get("instrumentID") or p.get("instrumentId"),
                    "symbol": f"#{p.get('instrumentID') or p.get('instrumentId')}",
                    "side": "buy" if p.get("isBuy") else "sell",
                    "notional": round(float(p.get("amount") or 0) * float(p.get("leverage") or 1), 2), "pnl": pnl,
                    "stop_loss": p.get("stopLossRate"), "entry": p.get("openRate")})
    invested = sum(float(p.get("amount") or 0) for p in cp.get("positions") or [])
    upnl = float(cp.get("unrealizedPnL") or sum(p["pnl"] for p in pos))
    equity = float(cp.get("credit") or 0) + invested + upnl
    return _row("etoro", name, "broker", "connected", start=float(cp.get("credit") or 0) + invested, equity=round(equity, 2),
                unrealized=upnl, positions=pos, note="read with your eToro keys")


def from_atas(status: dict | None) -> dict:
    status = status or {}
    acc = status.get("account")
    state = status.get("state", "off")
    books = len(status.get("books") or [])
    if not acc:
        return _row("atas", "ATAS", "data", state,
                    note=f"order flow for {books} symbol(s)" if books else (status.get("help") or "waiting for the ATAS indicator"))
    pos = [{"platform": "atas", "symbol": p["symbol"], "side": p["side"], "notional": None, "lots": p["size"],
            "pnl": p["pnl"]} for p in acc.get("positions", [])]
    upnl = acc.get("open_pnl") if acc.get("open_pnl") is not None else sum(p["pnl"] for p in pos)
    bal = acc.get("balance")
    return _row("atas", f"ATAS · {acc.get('name') or 'account'}", "broker", state, start=bal,
                equity=None if bal is None else round(bal + (upnl or 0), 2), unrealized=upnl or 0.0, positions=pos,
                note=f"from the ATAS indicator · order flow for {books} symbol(s)")


# ---------------------------------------------------------------- one account

def _melbourne_day(ts) -> str | None:
    try:
        return session_desks.melbourne(datetime.fromtimestamp(float(ts), timezone.utc)).strftime("%Y-%m-%d")
    except (TypeError, ValueError, OSError, OverflowError):
        return None


def _norm(sym) -> str:
    s = str(sym or "").upper().replace("/", "").replace("-", "").replace("_", "")
    for tail in ("USDT", "USD") if s not in ("EURUSD", "GBPUSD", "AUDUSD", "NZDUSD") else ():
        if s.endswith(tail) and len(s) > len(tail) + 1 and s[:-len(tail)] in ("BTC", "ETH", "SOL", "XRP", "DOGE"):
            s = s[:-len(tail)]
    return {"XAUUSD": "GOLD", "XAGUSD": "SILVER", "US500": "SPX", "NAS100": "NDX", "GER40": "DAX"}.get(s, s)


def combine(rows: list[dict], mode: str = "demo", env=None, now: float | None = None, kill: bool = False) -> dict:
    env = env or {}
    now = time.time() if now is None else now
    today = _melbourne_day(now)
    equity = sum(r["equity"] for r in rows if r["equity"] is not None)
    start = sum(r["start"] for r in rows if r["equity"] is not None and r["start"] is not None)
    pnl = sum(r["pnl"] for r in rows)
    positions = [p for r in rows for p in r["positions"]]
    closed = [c for r in rows for c in r["closed"]]
    gross = sum(abs(p["notional"] or 0) for p in positions)
    by_symbol: dict[str, dict] = {}
    for p in positions:
        k = _norm(p["symbol"])
        s = by_symbol.setdefault(k, {"symbol": k, "net": 0.0, "gross": 0.0, "pnl": 0.0, "platforms": []})
        n = abs(p["notional"] or 0) * (1 if p["side"] in ("buy", "YES", "back") else -1)
        s["net"] += n
        s["gross"] += abs(n)
        s["pnl"] += p["pnl"] or 0
        if p["platform"] not in s["platforms"]:
            s["platforms"].append(p["platform"])
    for s in by_symbol.values():
        s.update({k: round(s[k], 2) for k in ("net", "gross", "pnl")})
    today_closed = [c for c in closed if _melbourne_day(c.get("ts")) == today]
    today_pnl = sum(c["pnl"] for c in today_closed) + sum(p["pnl"] or 0 for p in positions)
    loss_limit = round(max(equity, 0) * DAILY_LOSS, 2)
    max_open = LIVE_MAX_OPEN if mode == "live" else MAX_OPEN
    breaches = []
    if kill:
        breaches.append("KILL file is in place: nothing new opens")
    if today_pnl <= -loss_limit and loss_limit > 0:
        breaches.append(f"2% daily loss hit ({today_pnl:,.2f} today): no new risk until tomorrow")
    if equity > 0 and gross > GROSS_LIMIT * equity:
        breaches.append(f"gross exposure {gross:,.0f} is over {GROSS_LIMIT:g}x equity")
    doubled = sorted(k for k, s in by_symbol.items() if len(s["platforms"]) > 1)
    if doubled:
        breaches.append("same symbol open on two platforms: " + ", ".join(doubled))
    # every closed trade from every platform goes to the desk that was leading when it closed
    assigned = session_desks.assign([c for c in closed if c.get("ts") is not None])
    lead = session_desks.lead(datetime.fromtimestamp(now, timezone.utc))
    desks = []
    for k in session_desks.ORDER:
        mine = assigned[k]
        won = sum(1 for c in mine if c.get("won"))
        by_pf: dict[str, float] = {}
        for c in mine:
            by_pf[c["platform"]] = round(by_pf.get(c["platform"], 0.0) + c["pnl"], 2)
        desks.append({"key": k, "name": session_desks.DESKS[k]["name"], "leading": k == lead,
                      "open": session_desks.is_open(k, datetime.fromtimestamp(now, timezone.utc)),
                      "trades": len(mine), "won": won, "pnl": round(sum(c["pnl"] for c in mine), 2),
                      "win_rate": round(won / len(mine), 4) if mine else None, "by_platform": by_pf})
    return {
        "mode": mode, "armed": armed(env), "lead_desk": lead,
        "totals": {"equity": round(equity, 2), "start": round(start, 2), "pnl": round(pnl, 2),
                   "unrealized": round(sum(r["unrealized"] for r in rows), 2),
                   "realized": round(sum(r["realized"] for r in rows), 2), "today_pnl": round(today_pnl, 2),
                   "open_positions": len(positions), "gross_exposure": round(gross, 2),
                   "closed_trades": len(closed), "won": sum(1 for c in closed if c.get("won"))},
        "risk": {"daily_loss_limit": loss_limit, "daily_loss_used": round(max(-today_pnl, 0), 2),
                 "max_open": max_open, "gross_limit": round(GROSS_LIMIT * equity, 2), "breaches": breaches,
                 "stopped": bool(kill or any("daily loss" in b for b in breaches)),
                 "live_max_order": live_max_order(env), "live_risk_pct": LIVE_RISK,
                 "rules": ["2% daily loss on the whole account stops new risk for the day",
                           "never two positions in the same symbol, on any platform",
                           f"at most {MAX_OPEN} open positions in demo, {LIVE_MAX_OPEN} in live",
                           f"gross exposure at most {GROSS_LIMIT:g}x combined equity",
                           f"live orders: stop loss required, at most ${live_max_order(env):,.0f} each and 1% of the balance at risk",
                           "a file named KILL in the events folder stops every new order"]},
        "accounts": [{k: v for k, v in r.items() if k != "closed"} for r in rows],
        "positions": positions, "by_symbol": sorted(by_symbol.values(), key=lambda s: -s["gross"]),
        "recent": sorted(closed, key=lambda c: -(float(c["ts"]) if isinstance(c.get("ts"), (int, float)) else 0))[:40],
        "desks": desks, "routes": routes(env, mode),
    }


def check_order(acct: dict, platform: str, symbol: str, notional: float, mode: str = "demo",
                stop_loss=None, entry=None) -> None:
    """Raise Refused if the order breaks a combined limit. Closing orders never come here."""
    risk, totals = acct["risk"], acct["totals"]
    if risk["stopped"]:
        raise Refused(risk["breaches"][0] if risk["breaches"] else "trading stopped for today")
    key = _norm(symbol)
    held = next((s for s in acct["by_symbol"] if s["symbol"] == key), None)
    if held:
        raise Refused(f"One account rule: {key} is already open on {', '.join(held['platforms'])}. "
                      "Never two positions in the same symbol.")
    if totals["open_positions"] >= risk["max_open"]:
        raise Refused(f"One account rule: at most {risk['max_open']} open positions.")
    if totals["equity"] > 0 and totals["gross_exposure"] + abs(notional) > risk["gross_limit"]:
        raise Refused(f"One account rule: gross exposure would pass {GROSS_LIMIT:g}x the combined equity.")
    if mode == "live":
        if not stop_loss:
            raise Refused("Live orders need a stop loss.")
        if abs(notional) > risk["live_max_order"]:
            raise Refused(f"Live order is over the ${risk['live_max_order']:,.0f} cap (LIVE_MAX_ORDER_USD on Render).")
        if entry and stop_loss:
            at_risk = abs(notional) * abs(float(entry) - float(stop_loss)) / float(entry)
            if totals["equity"] > 0 and at_risk > LIVE_RISK * totals["equity"]:
                raise Refused(f"Live order risks ${at_risk:,.2f}, over 1% of the balance.")


# ---------------------------------------------------------------- order routing (MT5 and eToro)

def _http(method: str, url: str, headers: dict, body: dict | None = None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json", "Accept": "application/json", **headers})
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:  # noqa: S310 (fixed broker hosts)
        raw = r.read()
        return json.loads(raw) if raw else {}


ETORO_BASE = "https://public-api.etoro.com"


def etoro_headers(env) -> dict:
    return {"x-api-key": env["ETORO_API_KEY"], "x-user-key": env["ETORO_USER_KEY"], "x-request-id": str(uuid.uuid4())}


def etoro_portfolio(env, live: bool, http=_http) -> dict:
    base = env.get("ETORO_API_BASE", ETORO_BASE).rstrip("/")
    return http("GET", f"{base}/api/v1/trading/info/{'real' if live else 'demo'}/pnl", etoro_headers(env))


def etoro_order(env, live: bool, instrument_id: int, side: str, amount: float, leverage: int, stop_loss: float | None,
                take_profit: float | None, http=_http) -> dict:
    base = env.get("ETORO_API_BASE", ETORO_BASE).rstrip("/")
    path = "/api/v1/trading/execution/market-open-orders/by-amount" if live else \
        "/api/v1/trading/execution/demo/market-open-orders/by-amount"
    body = {"InstrumentID": int(instrument_id), "IsBuy": side == "buy", "Leverage": int(leverage), "Amount": float(amount)}
    if stop_loss:
        body["StopLossRate"] = float(stop_loss)
    if take_profit:
        body["TakeProfitRate"] = float(take_profit)
    res = http("POST", base + path, etoro_headers(env), body)
    return {"platform": "etoro", "mode": "live" if live else "demo", "instrument": int(instrument_id), "side": side,
            "amount": float(amount), "leverage": int(leverage), "result": res}


def mt5_env(env, live: bool, http=None) -> dict:
    """The env MetaApi should use: the real account in Live, the demo account in Demo.

    Live: METAAPI_LIVE_ACCOUNT_ID, else MT5_LIVE_LOGIN/PASSWORD/SERVER added to MetaApi, else the one account MetaApi
    reports as real (any broker). Live never falls back to a demo account: no real account means Refused.
    Demo: METAAPI_ACCOUNT_ID, else MT5_DEMO_LOGIN/PASSWORD/SERVER added to MetaApi, else MetaApi's demo account."""
    from . import live_apis
    kw = {"http": http} if http else {}
    e = dict(env)
    if live:
        try:
            acc_id, region = live_apis.mt5_live_account(env, **kw)
        except live_apis.Refused as err:
            raise Refused(str(err)) from err
        e["METAAPI_ACCOUNT_ID"] = acc_id
        e["METAAPI_REGION"] = env.get("METAAPI_REGION") or region
        e.pop("METAAPI_MT5_DEMO_ACCOUNT_ID", None)
    elif not (env.get("METAAPI_ACCOUNT_ID") or env.get("METAAPI_MT5_DEMO_ACCOUNT_ID")):
        try:
            added = live_apis.mt5_provision(env, "demo", **kw)
        except live_apis.Refused as err:
            raise Refused(str(err)) from err
        if added:
            e["METAAPI_ACCOUNT_ID"] = added
    return e


def mt5_order(api, live: bool, symbol: str, side: str, volume: float, stop_loss=None, take_profit=None) -> dict:
    """Demo: the existing demo-only path. Live: the real account, stop loss required, at most 1 lot."""
    if not live:
        return api.demo_order(symbol, side, volume, stop_loss, take_profit)
    if side not in ("buy", "sell") or not 0 < float(volume) <= 1:
        raise Refused("Live MT5: side buy or sell, volume up to 1 lot.")
    if not stop_loss:
        raise Refused("Live orders need a stop loss.")
    body = {"actionType": "ORDER_TYPE_BUY" if side == "buy" else "ORDER_TYPE_SELL", "symbol": symbol,
            "volume": float(volume), "stopLoss": float(stop_loss), "comment": "ats one account live"}
    if take_profit:
        body["takeProfit"] = float(take_profit)
    result = api.http("POST", api.base + "/trade", {"auth-token": api._token}, body)
    return {"platform": "mt5", "mode": "live", "symbol": symbol, "side": side, "qty": float(volume),
            "ok": result.get("stringCode") == "TRADE_RETCODE_DONE", "code": result.get("stringCode"),
            "message": result.get("message"), "position_id": result.get("positionId")}
