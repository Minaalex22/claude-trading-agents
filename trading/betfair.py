"""Betfair Exchange bridge: the real-money prediction market that is legal in Australia.

Polymarket is blocked in Australia (ACMA), so this is the bridge for real bets. It works like the
MT5 and BTCC bridges:

- **Off** until the owner adds keys on Render (Environment), never in chat:
    BETFAIR_APP_KEY     the application key from Betfair's developer portal
    BETFAIR_USERNAME    Betfair login name
    BETFAIR_PASSWORD    Betfair password
  (or BETFAIR_SESSION_TOKEN instead of username + password)
- **Paper** by default: real Betfair prices, bets fill on our own paper book ($EVENTS_DIR/betfair_paper.json).
  Betfair has no demo account, so paper is the practice mode.
- **Real money** only when ALL of these hold:
    1. the owner sets BETFAIR_REAL_MONEY=on on Render,
    2. the order says live=true and confirm="REAL MONEY",
    3. the money rules pass: stake <= BETFAIR_MAX_STAKE (default A$10) and <= BETFAIR_RISK_PCT (default 1, max 10) % of available balance,
       at most 2 open real bets, daily real stake <= BETFAIR_DAILY_STAKE (default A$50),
       odds between 1.1 and 20, lay liability inside the same caps.
  Real orders are limit orders that lapse at the off (persistenceType LAPSE) and can be cancelled.

Hosts: identitysso.betfair.com.au (AU login) and api.betfair.com (Exchange API, JSON-RPC).
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path

LOGIN_URL = "https://identitysso.betfair.com.au/api/login"
API_BASE = "https://api.betfair.com/exchange"
ENV_KEYS = ("BETFAIR_APP_KEY", "BETFAIR_USERNAME", "BETFAIR_PASSWORD")
REAL_CONFIRM = "REAL MONEY"
MIN_ODDS, MAX_ODDS = 1.1, 20.0
MAX_OPEN_REAL = 2
PAPER_START = 1_000.0  # A$
SESSION_HOURS = 8.0
USER_AGENT = "ats-matrix-bridge/1.0"
TICKS = ((2, 0.01), (3, 0.02), (4, 0.05), (6, 0.1), (10, 0.2), (20, 0.5), (30, 1), (50, 2), (100, 5), (1000, 10))


class Refused(PermissionError):
    """An order the bridge will not send, with a plain reason."""


class NotConnected(RuntimeError):
    pass


def configured(env=os.environ) -> bool:
    return bool(env.get("BETFAIR_APP_KEY")) and (bool(env.get("BETFAIR_SESSION_TOKEN"))
                                                  or bool(env.get("BETFAIR_USERNAME") and env.get("BETFAIR_PASSWORD")))


def missing(env=os.environ) -> list[str]:
    if configured(env):
        return []
    if env.get("BETFAIR_SESSION_TOKEN"):
        return ["BETFAIR_APP_KEY"]
    return [k for k in ENV_KEYS if not env.get(k)]


def real_switch_on(env=os.environ) -> bool:
    return (env.get("BETFAIR_REAL_MONEY") or "").strip().lower() in ("on", "true", "yes", "1")


def limits(env=os.environ) -> dict:
    def num(key, default):
        try:
            v = float(env.get(key) or default)
            return v if v > 0 else default
        except ValueError:
            return default
    return {"max_stake": num("BETFAIR_MAX_STAKE", 10.0), "daily_stake": num("BETFAIR_DAILY_STAKE", 50.0),
            "max_open": MAX_OPEN_REAL, "min_odds": MIN_ODDS, "max_odds": MAX_ODDS,
            "balance_pct": min(num("BETFAIR_RISK_PCT", 1.0), 10.0)}


def tick_price(price: float) -> float:
    """Round odds to a valid Betfair price step (1.01-1000)."""
    price = min(1000.0, max(1.01, float(price)))
    for top, step in TICKS:
        if price <= top:
            return round(round(price / step) * step, 2)
    return 1000.0


def _post(url: str, body: bytes, headers: dict, timeout: float = 10.0):
    req = urllib.request.Request(url, data=body, headers={"Accept": "application/json", "User-Agent": USER_AGENT, **headers},
                                 method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (fixed https hosts)
        return json.loads(resp.read() or b"null")


class Betfair:
    """Thin JSON-RPC client. Never prints or returns keys."""

    def __init__(self, env=os.environ, post=_post, now=time.time):
        if not configured(env):
            raise NotConnected("Add " + ", ".join(missing(env)) + " on Render (Environment).")
        self.env, self.post, self.now = env, post, now
        self.app_key = env["BETFAIR_APP_KEY"]
        self.base = (env.get("BETFAIR_API_BASE") or API_BASE).rstrip("/")
        self.token = env.get("BETFAIR_SESSION_TOKEN") or None
        self.token_at = now() if self.token else 0.0
        self.lock = threading.Lock()

    def __repr__(self) -> str:
        return "Betfair(<keys hidden>)"

    def login(self) -> None:
        if not (self.env.get("BETFAIR_USERNAME") and self.env.get("BETFAIR_PASSWORD")):
            raise NotConnected("Session token expired. Add BETFAIR_USERNAME and BETFAIR_PASSWORD, or a fresh BETFAIR_SESSION_TOKEN.")
        body = urllib.parse.urlencode({"username": self.env["BETFAIR_USERNAME"], "password": self.env["BETFAIR_PASSWORD"]}).encode()
        r = self.post(LOGIN_URL, body, {"X-Application": self.app_key, "Content-Type": "application/x-www-form-urlencoded"})
        if not isinstance(r, dict) or r.get("status") != "SUCCESS" or not r.get("token"):
            raise NotConnected(f"Betfair login refused: {(r or {}).get('error') or (r or {}).get('status') or 'no answer'}")
        self.token, self.token_at = r["token"], self.now()

    def _session(self) -> str:
        with self.lock:
            if not self.token or self.now() - self.token_at > SESSION_HOURS * 3600:
                self.login()
            return self.token

    def call(self, service: str, method: str, params: dict, retry: bool = True):
        path, prefix = ("betting", "SportsAPING") if service == "betting" else ("account", "AccountAPING")
        payload = json.dumps({"jsonrpc": "2.0", "method": f"{prefix}/v1.0/{method}", "params": params, "id": 1}).encode()
        r = self.post(f"{self.base}/{path}/json-rpc/v1", payload,
                      {"X-Application": self.app_key, "X-Authentication": self._session(), "Content-Type": "application/json"})
        err = (r or {}).get("error") if isinstance(r, dict) else None
        if err:
            code = json.dumps(err)
            if retry and ("INVALID_SESSION" in code or "NO_SESSION" in code):
                with self.lock:
                    self.token = None
                return self.call(service, method, params, retry=False)
            raise RuntimeError(f"Betfair {method}: {_short_error(err)}")
        return (r or {}).get("result")

    # ---- reads
    def funds(self) -> dict:
        r = self.call("account", "getAccountFunds", {}) or {}
        return {"available": r.get("availableToBetBalance"), "exposure": r.get("exposure"), "wallet": r.get("wallet", "AUS")}

    def search(self, text: str, limit: int = 8) -> list[dict]:
        flt = {"textQuery": text[:80], "marketTypeCodes": ["MATCH_ODDS", "WINNER", "MONEY_LINE", "OUTRIGHT", "SPECIAL"]}
        cats = self.call("betting", "listMarketCatalogue", {
            "filter": flt, "maxResults": str(limit), "sort": "MAXIMUM_TRADED",
            "marketProjection": ["EVENT", "EVENT_TYPE", "RUNNER_DESCRIPTION", "MARKET_START_TIME"]}) or []
        return self._with_prices(cats)

    def _with_prices(self, cats: list[dict]) -> list[dict]:
        if not cats:
            return []
        books = self.call("betting", "listMarketBook", {
            "marketIds": [c["marketId"] for c in cats],
            "priceProjection": {"priceData": ["EX_BEST_OFFERS"]}}) or []
        by_id = {b["marketId"]: b for b in books}
        out = []
        for c in cats:
            b = by_id.get(c["marketId"], {})
            prices = {r["selectionId"]: r for r in b.get("runners", [])}
            runners = []
            for r in c.get("runners", []):
                ex = (prices.get(r["selectionId"]) or {}).get("ex", {})
                back = (ex.get("availableToBack") or [{}])[0].get("price")
                lay = (ex.get("availableToLay") or [{}])[0].get("price")
                runners.append({"id": r["selectionId"], "name": str(r.get("runnerName", ""))[:60], "back": back, "lay": lay,
                                "implied": round(1 / back, 4) if back else None})
            out.append({"market_id": c["marketId"], "name": str(c.get("marketName", ""))[:80],
                        "event": str((c.get("event") or {}).get("name", ""))[:100],
                        "sport": str((c.get("eventType") or {}).get("name", ""))[:40],
                        "start": c.get("marketStartTime"), "status": b.get("status", "UNKNOWN"),
                        "matched": round(b.get("totalMatched") or 0), "runners": runners,
                        "url": f"https://www.betfair.com.au/exchange/plus/market/{c['marketId']}"})
        return out

    def market(self, market_id: str) -> dict | None:
        cats = self.call("betting", "listMarketCatalogue", {
            "filter": {"marketIds": [market_id]}, "maxResults": "1",
            "marketProjection": ["EVENT", "EVENT_TYPE", "RUNNER_DESCRIPTION", "MARKET_START_TIME"]}) or []
        got = self._with_prices(cats)
        return got[0] if got else None

    def orders(self) -> list[dict]:
        r = self.call("betting", "listCurrentOrders", {}) or {}
        return [{"bet_id": o.get("betId"), "market_id": o.get("marketId"), "selection_id": o.get("selectionId"),
                 "side": o.get("side"), "price": (o.get("priceSize") or {}).get("price"),
                 "size": (o.get("priceSize") or {}).get("size"), "matched": o.get("sizeMatched"),
                 "status": o.get("status")} for o in r.get("currentOrders", [])]

    # ---- writes (real money; only reached through Bridge.place with every check passed)
    def place(self, market_id: str, selection_id: int, side: str, price: float, size: float, ref: str) -> dict:
        r = self.call("betting", "placeOrders", {
            "marketId": market_id, "customerRef": ref,
            "instructions": [{"selectionId": int(selection_id), "handicap": 0, "side": side, "orderType": "LIMIT",
                              "limitOrder": {"size": round(size, 2), "price": price, "persistenceType": "LAPSE"}}]}) or {}
        if r.get("status") != "SUCCESS":
            raise RuntimeError(f"Betfair refused the bet: {r.get('errorCode') or r.get('status')}")
        rep = (r.get("instructionReports") or [{}])[0]
        return {"bet_id": rep.get("betId"), "matched": rep.get("sizeMatched"), "avg_price": rep.get("averagePriceMatched"),
                "status": rep.get("orderStatus")}

    def cancel(self, market_id: str, bet_id: str) -> dict:
        r = self.call("betting", "cancelOrders", {"marketId": market_id, "instructions": [{"betId": str(bet_id)}]}) or {}
        return {"status": r.get("status")}


def _short_error(err) -> str:
    text = json.dumps(err)
    m = re.search(r'"errorCode":\s*"([A-Z_]+)"', text)
    return m.group(1) if m else text[:120]


# ---------------------------------------------------------------- matching Polymarket picks to Betfair

STOP = {"will", "the", "be", "a", "an", "of", "by", "in", "on", "to", "win", "who", "what", "next", "vs", "vs.", "and",
        "or", "is", "at", "for", "before", "after", "december", "november", "january", "2026", "2027", "2028", "yes", "no",
        "game", "match", "winner", "there"}


def search_terms(question: str, outcomes: list[str] | None = None) -> str:
    """Two or three strong words from a Polymarket question for Betfair's text search."""
    names = [o for o in (outcomes or []) if o.lower() not in ("yes", "no")]
    if len(names) == 2:  # team vs team: search the first team
        return names[0][:40]
    words = [w.strip("?.,:;!'\"()$") for w in question.split()]
    keep = [w for w in words if w and w.lower() not in STOP and not w.replace(",", "").isdigit() and len(w) > 2]
    caps = [w for w in keep if w[0].isupper()]
    return " ".join((caps or keep)[:3])


def compare(poly: dict, bf: dict) -> dict | None:
    """Line up a Polymarket market with the best-matching Betfair runner: price gap in percentage points."""
    names = [n.lower() for n in poly.get("outcomes", [])]
    target = names[0] if names and names[0] not in ("yes", "no") else ""
    q = poly.get("question", "").lower()
    best = None
    for r in bf.get("runners", []):
        n = r["name"].lower()
        score = (3 if target and (n in target or target in n) else 0) + (1 if n and n in q else 0)
        if score and r.get("implied") and (best is None or score > best[0]):
            best = (score, r)
    if not best:
        return None
    r = best[1]
    pm = poly.get("yes")
    return {"market_id": bf["market_id"], "event": bf["event"], "market": bf["name"], "runner": r["name"],
            "selection_id": r["id"], "back": r["back"], "lay": r["lay"], "betfair_pct": round(r["implied"] * 100, 1),
            "polymarket_pct": round(pm * 100, 1) if pm is not None else None,
            "gap_pts": round((r["implied"] - pm) * 100, 1) if pm is not None else None, "url": bf["url"]}


# ---------------------------------------------------------------- paper book + the gatekeeper

class Bridge:
    """Everything the page talks to: status, search, matches, paper bets, and the gated real-money path."""

    def __init__(self, env=os.environ, folder: str | Path | None = None, client: Betfair | None = None, now=time.time):
        self.env, self.now = env, now
        self.path = Path(folder or env.get("EVENTS_DIR") or ".") / "betfair_paper.json"
        self.lock = threading.Lock()
        self.client = client
        self.state = self._load()

    def api(self) -> Betfair:
        if self.client is None:
            self.client = Betfair(self.env, now=self.now)
        return self.client

    def _load(self) -> dict:
        try:
            d = json.loads(self.path.read_text())
            if isinstance(d, dict) and "cash" in d:
                d.setdefault("open", []); d.setdefault("closed", []); d.setdefault("real", []); d.setdefault("next_id", 1)
                return d
        except (OSError, ValueError):
            pass
        return {"cash": PAPER_START, "start": PAPER_START, "next_id": 1, "open": [], "closed": [], "real": []}

    def _save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.state))
            tmp.replace(self.path)
        except OSError:
            pass

    def status(self) -> dict:
        on = configured(self.env)
        out = {"configured": on, "missing": missing(self.env), "mode": "paper", "real_switch": real_switch_on(self.env),
               "limits": limits(self.env), "funds": None, "error": None,
               "help": None if on else "Add BETFAIR_APP_KEY, BETFAIR_USERNAME and BETFAIR_PASSWORD on Render (Environment). "
                                       "Get the app key at developer.betfair.com (Account > API keys)."}
        if on and real_switch_on(self.env):
            out["mode"] = "real money unlocked (each bet still needs confirm)"
        if on:
            try:
                out["funds"] = self.api().funds()
            except Exception as e:  # noqa: BLE001 (show why, keep the page up)
                out["error"] = str(e)[:200] if isinstance(e, (NotConnected, RuntimeError)) else f"Betfair not reachable ({type(e).__name__})"
        return out

    def search(self, text: str) -> list[dict]:
        if not configured(self.env):
            return []
        return self.api().search(text)

    def matches(self, polys: list[dict], max_lookups: int = 6) -> list[dict]:
        """The same events on Betfair for the agents' Polymarket picks (sports and politics mostly)."""
        if not configured(self.env):
            return []
        out = []
        for p in polys[:max_lookups]:
            terms = search_terms(p.get("question", ""), p.get("outcomes"))
            if not terms:
                continue
            try:
                found = self.api().search(terms, limit=3)
            except Exception:  # noqa: BLE001
                continue
            for bf in found:
                c = compare(p, bf)
                if c:
                    out.append({"polymarket_id": p.get("id"), "question": p.get("question"), "lean": (p.get("lean") or {}).get("side"), **c})
                    break
        return out

    # ---- paper
    def _paper_fill(self, mkt: dict, selection_id: int, side: str, stake: float) -> dict:
        r = next((x for x in mkt["runners"] if int(x["id"]) == int(selection_id)), None)
        if not r:
            raise Refused("That runner isn't in this market.")
        price = r["back"] if side == "BACK" else r["lay"]
        if not price:
            raise Refused("No price on Betfair for that side right now.")
        return {"runner": r["name"], "price": price}

    def place(self, market_id: str, selection_id: int, side: str, stake: float, odds: float | None = None,
              live: bool = False, confirm: str = "") -> dict:
        side = str(side).upper()
        if side not in ("BACK", "LAY"):
            raise Refused("Side must be BACK or LAY.")
        stake = float(stake)
        if not stake == stake or stake < 1:
            raise Refused("Stake at least A$1.")
        if live:
            return self._place_real(market_id, int(selection_id), side, stake, odds, confirm)
        mkt = self.api().market(market_id) if configured(self.env) else None
        if not mkt:
            raise Refused("Connect Betfair first (keys on Render) so paper bets use real Betfair prices.")
        fill = self._paper_fill(mkt, selection_id, side, stake)
        liability = stake if side == "BACK" else stake * (fill["price"] - 1)
        with self.lock:
            if liability > self.state["cash"]:
                raise Refused(f"Not enough paper money (A${self.state['cash']:.2f}).")
            bet = {"id": self.state["next_id"], "mode": "paper", "market_id": market_id, "event": mkt["event"], "market": mkt["name"],
                   "selection_id": int(selection_id), "runner": fill["runner"], "side": side, "odds": fill["price"],
                   "stake": round(stake, 2), "liability": round(liability, 2), "at": round(self.now())}
            self.state["next_id"] += 1
            self.state["cash"] = round(self.state["cash"] - liability, 2)
            self.state["open"].append(bet)
            self._save()
        return bet

    def _place_real(self, market_id: str, selection_id: int, side: str, stake: float, odds, confirm: str) -> dict:
        if not configured(self.env):
            raise Refused("Betfair isn't connected. Add the keys on Render first.")
        if not real_switch_on(self.env):
            raise Refused("Real money is off. The owner turns it on with BETFAIR_REAL_MONEY=on on Render.")
        if str(confirm).strip().upper() != REAL_CONFIRM:
            raise Refused(f'Type "{REAL_CONFIRM}" to confirm a real bet.')
        if odds is None:
            raise Refused("Real bets need the odds you want (a limit price).")
        price = tick_price(float(odds))
        lim = limits(self.env)
        if not lim["min_odds"] <= price <= lim["max_odds"]:
            raise Refused(f"Odds must be between {lim['min_odds']} and {lim['max_odds']} (money rule: no long shots).")
        liability = stake if side == "BACK" else stake * (price - 1)
        if liability > lim["max_stake"]:
            raise Refused(f"Money rule: risk per bet is at most A${lim['max_stake']:.0f} (this one risks A${liability:.2f}).")
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        with self.lock:
            spent = sum(b["liability"] for b in self.state["real"] if b.get("day") == today)
            if spent + liability > lim["daily_stake"]:
                raise Refused(f"Money rule: daily real-money risk cap A${lim['daily_stake']:.0f} reached (A${spent:.2f} used today).")
        api = self.api()
        open_real = [o for o in api.orders() if o.get("status") in ("EXECUTABLE", "EXECUTION_COMPLETE")]
        if len(open_real) >= lim["max_open"]:
            raise Refused(f"Money rule: at most {lim['max_open']} open real bets.")
        funds = api.funds()
        avail = float(funds.get("available") or 0)
        cap = avail * lim["balance_pct"] / 100
        if liability > cap:
            raise Refused(f"Money rule: risk at most {lim['balance_pct']:g}% of your Betfair balance (A${cap:.2f} of A${avail:.2f}).")
        rep = api.place(market_id, selection_id, side, price, stake, ref=uuid.uuid4().hex[:24])
        rec = {"mode": "real", "day": today, "at": round(self.now()), "market_id": market_id, "selection_id": selection_id,
               "side": side, "odds": price, "stake": round(stake, 2), "liability": round(liability, 2), **rep}
        with self.lock:
            self.state["real"].append(rec)
            self.state["real"] = self.state["real"][-500:]
            self._save()
        return rec

    def cancel_real(self, market_id: str, bet_id: str) -> dict:
        if not configured(self.env):
            raise Refused("Betfair isn't connected.")
        return self.api().cancel(market_id, bet_id)

    def settle_paper(self, pos_id: int) -> dict:
        """Close a paper bet at the current Betfair price (cash out), or settle if the market is closed."""
        with self.lock:
            bet = next((b for b in self.state["open"] if b["id"] == int(pos_id)), None)
        if not bet:
            raise Refused("No open paper bet with that number.")
        mkt = self.api().market(bet["market_id"]) if configured(self.env) else None
        if not mkt:
            raise Refused("Can't price this market right now.")
        r = next((x for x in mkt["runners"] if int(x["id"]) == bet["selection_id"]), {})
        # cash out: a back closes by laying at the current lay price and vice versa
        now_price = r.get("lay") if bet["side"] == "BACK" else r.get("back")
        if not now_price:
            raise Refused("No price to cash out at right now.")
        if bet["side"] == "BACK":
            pnl = bet["stake"] * (bet["odds"] / now_price - 1)
        else:
            pnl = bet["stake"] * (1 - bet["odds"] / now_price)
        with self.lock:
            self.state["open"] = [b for b in self.state["open"] if b["id"] != bet["id"]]
            done = {**bet, "exit_odds": now_price, "pnl": round(pnl, 2), "closed": round(self.now())}
            self.state["closed"].append(done)
            self.state["closed"] = self.state["closed"][-300:]
            self.state["cash"] = round(self.state["cash"] + bet["liability"] + pnl, 2)
            self._save()
        return done

    def snapshot(self) -> dict:
        with self.lock:
            st = self.state
            closed = st["closed"]
            return {"cash": st["cash"], "start": st["start"], "open": list(st["open"]), "closed": closed[::-1][:50],
                    "pnl": round(sum(c["pnl"] for c in closed), 2), "wins": sum(1 for c in closed if c["pnl"] > 0),
                    "bets": len(closed), "real": st["real"][::-1][:30]}
