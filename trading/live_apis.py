"""Live order APIs for every platform, and the Keys list the One account page shows.

Every platform's keys come only from Render environment variables. Nothing here returns or logs a value:
`keys_status()` says only whether each variable is set.

Platforms and how real orders reach them (official APIs only):
  MetaTrader 5   MetaApi cloud (metaapi.cloud), works with any MT5 broker: GO Markets, AvaTrade, IC Markets,
                 Pepperstone and the rest. Demo orders go to the demo account, Live orders to the real one.
                 The real account is found from METAAPI_TOKEN (the account MetaApi reports as not demo), or set
                 METAAPI_LIVE_ACCOUNT_ID. With MT5_LIVE_LOGIN / MT5_LIVE_PASSWORD / MT5_LIVE_SERVER on Render
                 the app adds the account to MetaApi itself (same for MT5_DEMO_*), so any broker works by name.
  eToro          eToro Public API (x-api-key + x-user-key). Demo = virtual portfolio, Live = real account.
                 Symbols ("AAPL", "BTC") are turned into eToro instrument ids here.
  IBKR           Interactive Brokers Client Portal Web API through the IBKR gateway (or IBeam) that the owner
                 runs and signs in to. Demo = paper account (ids start DU), Live = real account. A stop order is
                 attached to every live order.
  Betfair (AU)   Betfair Exchange API, trading/betfair.py (real bets need BETFAIR_REAL_MONEY=on + confirm).
  moomoo         moomoo OpenD on the owner's computer, trading/bridge.py + trading/live_orders.py.
  Polymarket     read and paper only: Polymarket blocks Australian accounts itself.
  Mitrade        no trading API (live prices from its public feed, practice desk).
  BTCC           trading API paused by BTCC since 13 Feb 2026; read-only bridge waits for it.
  Session desks  no keys: every desk trades through whichever platform above the order goes to.
"""

from __future__ import annotations

import json
import os
import ssl
import urllib.parse
import urllib.request
import uuid

HTTP_TIMEOUT = 20


class Refused(PermissionError):
    """An order the bridge will not send, with a plain reason."""


def _http(method: str, url: str, headers: dict, body: dict | None = None, context=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json", "Accept": "application/json", **headers})
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT, context=context) as r:  # noqa: S310 (owner's broker hosts)
        raw = r.read()
        return json.loads(raw) if raw else {}


# ---------------------------------------------------------------- the Keys list

def _v(name: str, need: str, what: str) -> dict:
    return {"var": name, "need": need, "what": what}


# need: "demo" = needed for demo orders, "live" = needed for real orders too, "either" = one of a group,
# "optional" = has a safe default. Names only; values are never read out.
KEYS = (
    {"key": "all", "name": "Live switch (all platforms)", "api": "this app",
     "live_possible": True,
     "vars": [_v("LIVE_MONEY", "live", "set to on to allow the LIVE button on /account"),
              _v("LIVE_MAX_ORDER_USD", "optional", "biggest live order in USD (default 100)")],
     "how": "Render > ats-matrix-6la7 > Environment."},
    {"key": "mt5", "name": "MetaTrader 5 (any broker)", "api": "MetaApi cloud",
     "live_possible": True,
     "vars": [_v("METAAPI_TOKEN", "demo", "API token from app.metaapi.cloud (Settings > API tokens)"),
              _v("METAAPI_LIVE_ACCOUNT_ID", "optional", "real account id in MetaApi; found from the token if empty"),
              _v("METAAPI_ACCOUNT_ID", "optional", "demo account id in MetaApi; found from the token if empty"),
              _v("MT5_LIVE_LOGIN", "optional", "real MT5 login number, so the app adds it to MetaApi itself"),
              _v("MT5_LIVE_PASSWORD", "optional", "real MT5 trading password (not investor)"),
              _v("MT5_LIVE_SERVER", "optional", "broker server, e.g. GoMarkets-Live or Ava-Real 1-MT5"),
              _v("MT5_DEMO_LOGIN", "optional", "demo MT5 login, same idea"),
              _v("MT5_DEMO_PASSWORD", "optional", "demo MT5 password"),
              _v("MT5_DEMO_SERVER", "optional", "demo server, e.g. GoMarkets-Demo or Ava-Demo 1-MT5")],
     "how": "Make a free MetaApi account, add your MT5 logins there (or put them on Render as above), "
            "copy the API token into METAAPI_TOKEN. Works with GO Markets, AvaTrade and any MT5 broker."},
    {"key": "etoro", "name": "eToro", "api": "eToro Public API",
     "live_possible": True,
     "vars": [_v("ETORO_API_KEY", "demo", "application key (eToro > Settings > Trading API)"),
              _v("ETORO_USER_KEY", "demo", "user key from the same page; make it with trading rights for live")],
     "how": "eToro web > Settings > Trading > API keys. One key pair covers virtual and real."},
    {"key": "ibkr", "name": "Interactive Brokers", "api": "IBKR Client Portal Web API",
     "live_possible": True,
     "vars": [_v("IBKR_GATEWAY_URL", "demo", "https address of your signed-in IBKR gateway or IBeam"),
              _v("IBKR_ACCOUNT_ID", "optional", "account to trade; the first one on the gateway if empty"),
              _v("IBKR_GATEWAY_TOKEN", "optional", "bearer token if your gateway sits behind a proxy that needs one")],
     "how": "Run IBKR's Client Portal gateway or IBeam (Docker) where Render can reach it over https and sign in. "
            "Sign in with the paper login for demo, the real login for live."},
    {"key": "betfair", "name": "Betfair Exchange (AU)", "api": "Betfair Exchange API",
     "live_possible": True,
     "vars": [_v("BETFAIR_APP_KEY", "demo", "application key from developer.betfair.com"),
              _v("BETFAIR_USERNAME", "demo", "Betfair login name (or BETFAIR_SESSION_TOKEN)"),
              _v("BETFAIR_PASSWORD", "demo", "Betfair password (or BETFAIR_SESSION_TOKEN)"),
              _v("BETFAIR_REAL_MONEY", "live", "set to on to allow real bets"),
              _v("BETFAIR_MAX_STAKE", "optional", "biggest real stake in A$ (default 10)")],
     "how": "developer.betfair.com > Accounts > create a delayed or live app key. Bets on /polymarket."},
    {"key": "moomoo", "name": "moomoo", "api": "moomoo OpenD (on your computer)",
     "live_possible": True,
     "vars": [_v("BRIDGE_TOKEN", "live", "shared secret between this app and the bridge on your computer"),
              _v("LIVE_SYMBOLS", "live", "symbols the bridge may trade, e.g. AAPL,MSFT"),
              _v("LIVE_MAX_ORDER_USD", "live", "biggest order in USD"),
              _v("LIVE_MAX_DAY_USD", "optional", "most spent per day")],
     "how": "Run moomoo OpenD and python -m trading.bridge on your computer with the same BRIDGE_TOKEN."},
    {"key": "polymarket", "name": "Polymarket", "api": "Polymarket CLOB (read only)",
     "live_possible": False, "vars": [],
     "how": "No keys. Polymarket blocks Australian accounts itself, so bets stay on paper at real prices. "
            "Betfair is the real-money prediction market here."},
    {"key": "btcc", "name": "BTCC", "api": "BTCC TradeOpenAPI (paused)",
     "live_possible": False,
     "vars": [_v("BTCC_API_KEY", "optional", "only once BTCC reopens its API"),
              _v("BTCC_SECRET_KEY", "optional", "same"),
              _v("BTCC_USERNAME", "optional", "same"),
              _v("BTCC_PASSWORD", "optional", "same")],
     "how": "BTCC closed its API key portal on 13 Feb 2026. The bridge turns on by itself if it comes back."},
    {"key": "mitrade", "name": "Mitrade", "api": "none (public price feed)",
     "live_possible": False, "vars": [],
     "how": "Mitrade has no trading API, MT4 or MT5, so it stays on its practice desk at Mitrade's live prices."},
    {"key": "atas", "name": "ATAS", "api": "ATAS indicator feed",
     "live_possible": False,
     "vars": [_v("ATAS_TOKEN", "optional", "made for you by Connect ATAS on /account")],
     "how": "Data only. ATAS trades go through the broker you connect inside ATAS."},
    {"key": "grok", "name": "Grok (xAI) brain", "api": "xAI API",
     "live_possible": False,
     "vars": [_v("XAI_API_KEY", "demo", "API key from console.x.ai (API Keys); turns Grok on"),
              _v("GROK_MODEL", "optional", "model for X reads and replays (default grok-4.7)"),
              _v("GROK_FAST_MODEL", "optional", "model for JARVIS fast mode (default = GROK_MODEL)"),
              _v("GROK_DAILY_CALLS", "optional", "most Grok calls a day for X reads, same again for JARVIS (default 150)"),
              _v("GROK_REFRESH_MINS", "optional", "minutes between X reads of each market (default 30)")],
     "how": "console.x.ai > API Keys > Create, paste it into XAI_API_KEY on Render. Grok reads X for each market, "
            "gives a second opinion on /decision and answers in JARVIS fast mode. It never places orders."},
    {"key": "whales", "name": "Unusual Whales (0DTE whale flow)", "api": "Unusual Whales API (data only)",
     "live_possible": False,
     "vars": [_v("UW_API_TOKEN", "demo", "API token from unusualwhales.com > API dashboard"),
              _v("UW_MAX_CALLS_DAY", "optional", "hard cap on calls per US day (default 450)")],
     "how": "Polls $3M+ 0DTE opening prints once a minute in US hours and feeds them to every desk."},
    {"key": "desks", "name": "Session desks (Sydney, Asia, Europe, London, New York)", "api": "through the platforms above",
     "live_possible": True, "vars": [],
     "how": "No keys of their own: each desk sends its orders to the platform you pick, in the switch's mode."},
)


def _set(env, name: str) -> bool:
    return bool(str(env.get(name, "") or "").strip())


def demo_ready(key: str, env) -> bool:
    if key == "mt5":
        return _set(env, "METAAPI_TOKEN")
    if key == "etoro":
        return _set(env, "ETORO_API_KEY") and _set(env, "ETORO_USER_KEY")
    if key == "ibkr":
        return _set(env, "IBKR_GATEWAY_URL")
    if key == "betfair":
        return _set(env, "BETFAIR_APP_KEY") and (_set(env, "BETFAIR_SESSION_TOKEN")
                                                  or (_set(env, "BETFAIR_USERNAME") and _set(env, "BETFAIR_PASSWORD")))
    if key == "moomoo":
        return _set(env, "BRIDGE_TOKEN")
    if key == "grok":
        return _set(env, "XAI_API_KEY")
    return True  # practice desks and paper books need nothing


def live_ready(key: str, env) -> bool:
    on = str(env.get("LIVE_MONEY", "")).strip().lower() == "on"
    if key == "all":
        return on
    if key == "betfair":
        return demo_ready("betfair", env) and str(env.get("BETFAIR_REAL_MONEY", "")).strip().lower() == "on"
    if key == "moomoo":
        return all(_set(env, k) for k in ("BRIDGE_TOKEN", "LIVE_SYMBOLS", "LIVE_MAX_ORDER_USD"))
    if key == "desks":
        return on
    spec = next((k for k in KEYS if k["key"] == key), None)
    return bool(spec and spec["live_possible"] and on and demo_ready(key, env))


def keys_status(env=os.environ) -> list[dict]:
    """Every platform, every env var it reads, and whether each is set. Never the values."""
    out = []
    for spec in KEYS:
        vars_ = [{**v, "set": _set(env, v["var"])} for v in spec["vars"]]
        missing = [v["var"] for v in vars_ if v["need"] in ("demo", "live") and not v["set"]]
        if spec["key"] == "betfair" and _set(env, "BETFAIR_SESSION_TOKEN"):
            missing = [m for m in missing if m not in ("BETFAIR_USERNAME", "BETFAIR_PASSWORD")]
        out.append({"key": spec["key"], "name": spec["name"], "api": spec["api"], "live_possible": spec["live_possible"],
                    "demo_ready": demo_ready(spec["key"], env), "live_ready": live_ready(spec["key"], env),
                    "vars": vars_, "missing": missing, "how": spec["how"]})
    return out


# ---------------------------------------------------------------- MetaTrader 5: any broker through MetaApi

PROVISIONING = "https://mt-provisioning-api-v1.agiliumtrade.agiliumtrade.ai/users/current/accounts"
DEMO_TYPE = "ACCOUNT_TRADE_MODE_DEMO"
_live_found: dict = {}


def mt5_accounts(token: str, http=_http) -> list[dict]:
    """Every MT5 account added in MetaApi, whatever the broker. No passwords come back from MetaApi."""
    rows = http("GET", PROVISIONING, {"auth-token": token}) or []
    return [{"id": str(a.get("_id") or a.get("id")), "name": a.get("name"), "login": str(a.get("login") or ""),
             "server": a.get("server"), "platform": a.get("platform", "mt5"), "state": a.get("state"),
             "region": a.get("region") or "new-york",
             "demo_by_name": "demo" in str(a.get("server", "")).lower() or "demo" in str(a.get("name", "")).lower()}
            for a in rows]


def mt5_provision(env, which: str, http=_http) -> str | None:
    """Add MT5_<WHICH>_LOGIN/PASSWORD/SERVER to MetaApi if it isn't there yet. Returns its MetaApi id, or None."""
    which = which.upper()
    login, password, server = (str(env.get(f"MT5_{which}_{k}", "") or "").strip() for k in ("LOGIN", "PASSWORD", "SERVER"))
    token = str(env.get("METAAPI_TOKEN", "") or "").strip()
    if not (token and login and password and server):
        return None
    for a in mt5_accounts(token, http):
        if a["login"] == login and str(a["server"]).lower() == server.lower():
            return a["id"]
    body = {"name": f"ATS {which.lower()} {login[-4:]}", "type": "cloud-g2", "login": login, "password": password,
            "server": server, "platform": "mt5", "magic": 20261125}
    res = http("POST", PROVISIONING, {"auth-token": token, "transaction-id": uuid.uuid4().hex}, body) or {}
    acc_id = res.get("id") or res.get("_id")
    if not acc_id:
        raise Refused(f"MetaApi did not add MT5 {which.lower()} account: {str(res.get('message') or res)[:160]}")
    return str(acc_id)


def mt5_live_account(env, http=_http) -> tuple[str, str]:
    """The real MT5 account: METAAPI_LIVE_ACCOUNT_ID, else MT5_LIVE_* added to MetaApi, else the one account MetaApi
    reports as not demo. Never falls back to a demo account. Returns (id, region)."""
    given = str(env.get("METAAPI_LIVE_ACCOUNT_ID", "") or "").strip()
    region = str(env.get("METAAPI_REGION", "") or "").strip()
    if given:
        return given, region or "new-york"
    token = str(env.get("METAAPI_TOKEN", "") or "").strip()
    if not token:
        raise Refused("Add METAAPI_TOKEN on Render to connect MetaTrader 5.")
    added = mt5_provision(env, "live", http)
    accounts = mt5_accounts(token, http)
    if added:
        hit = next((a for a in accounts if a["id"] == added), None)
        return added, region or (hit or {}).get("region", "new-york")
    key = hash(token)
    if key in _live_found:
        return _live_found[key]
    real = []
    for a in accounts:
        if a["demo_by_name"] or a["state"] not in ("DEPLOYED", "DEPLOYING"):
            continue
        base = f"https://mt-client-api-v1.{a['region']}.agiliumtrade.ai/users/current/accounts/{urllib.parse.quote(a['id'], safe='')}"
        try:
            info = http("GET", base + "/account-information", {"auth-token": token}) or {}
        except Exception:  # noqa: BLE001  (an account that doesn't answer is skipped)
            continue
        if info.get("type") and info.get("type") != DEMO_TYPE:
            real.append(a)
    if not real:
        raise Refused("No real MT5 account found in MetaApi. Add your live login at app.metaapi.cloud, or put "
                      "MT5_LIVE_LOGIN, MT5_LIVE_PASSWORD and MT5_LIVE_SERVER on Render.")
    if len(real) > 1:
        raise Refused("More than one real MT5 account in MetaApi: set METAAPI_LIVE_ACCOUNT_ID to the one to trade.")
    _live_found[key] = (real[0]["id"], region or real[0]["region"])
    return _live_found[key]


def mt5_close(api, live: bool, position_id: str) -> dict:
    """Close one MT5 position. Demo uses the demo-checked path; live closes on the real account."""
    if not live:
        return api.demo_close(position_id)
    if api.account().get("type") == DEMO_TYPE:
        raise Refused("Live is on but this MT5 account is a demo account.")
    res = api.http("POST", api.base + "/trade", {"auth-token": api._token},
                   {"actionType": "POSITION_CLOSE_ID", "positionId": str(position_id)})
    return {"platform": "mt5", "mode": "live", "ok": res.get("stringCode") == "TRADE_RETCODE_DONE",
            "code": res.get("stringCode"), "message": res.get("message")}


# ---------------------------------------------------------------- eToro: symbols and closing

ETORO_BASE = "https://public-api.etoro.com"


def _etoro_headers(env) -> dict:
    return {"x-api-key": env["ETORO_API_KEY"], "x-user-key": env["ETORO_USER_KEY"], "x-request-id": str(uuid.uuid4())}


def etoro_instrument_id(env, symbol: str, http=_http) -> int:
    """'AAPL' or 'BTC' -> eToro instrument id, from eToro's own instrument search."""
    s = str(symbol).strip().upper()
    if s.lstrip("#").isdigit():
        return int(s.lstrip("#"))
    base = env.get("ETORO_API_BASE", ETORO_BASE).rstrip("/")
    q = urllib.parse.urlencode({"fields": "instrumentId,internalSymbolFull,displayname", "internalSymbolFull": s,
                                "pageSize": 5})
    res = http("GET", f"{base}/api/v1/market-data/search?{q}", _etoro_headers(env)) or {}
    items = res.get("items") or []
    hit = next((i for i in items if str(i.get("internalSymbolFull", "")).upper() == s), items[0] if items else None)
    if not hit or hit.get("instrumentId") is None:
        raise Refused(f"eToro has no instrument called {s}.")
    return int(hit["instrumentId"])


def etoro_close(env, live: bool, position_id: int, instrument_id: int | None = None, http=_http) -> dict:
    base = env.get("ETORO_API_BASE", ETORO_BASE).rstrip("/")
    mid = "" if live else "demo/"
    body = {"InstrumentId": int(instrument_id)} if instrument_id else {}
    res = http("POST", f"{base}/api/v1/trading/execution/{mid}market-close-orders/positions/{int(position_id)}",
               _etoro_headers(env), body)
    return {"platform": "etoro", "mode": "live" if live else "demo", "position": int(position_id), "result": res}


# ---------------------------------------------------------------- Interactive Brokers (Client Portal Web API)

class Ibkr:
    """IBKR Client Portal Web API through the owner's signed-in gateway (or IBeam).

    Demo mode trades only a paper account (id starts with DU); Live trades only a real account. Every live
    order carries an attached stop order, and IBKR's "are you sure" prompts are answered only for those two
    order legs."""

    def __init__(self, env=os.environ, http=_http):
        url = str(env.get("IBKR_GATEWAY_URL", "") or "").strip().rstrip("/")
        if not url:
            raise Refused("Add IBKR_GATEWAY_URL on Render (your signed-in IBKR gateway).")
        if not url.startswith("https://"):
            raise Refused("IBKR_GATEWAY_URL must start with https://")
        self.base = url if url.endswith("/v1/api") else url + "/v1/api"
        self.account_id = str(env.get("IBKR_ACCOUNT_ID", "") or "").strip()
        tok = str(env.get("IBKR_GATEWAY_TOKEN", "") or "").strip()
        self.headers = {"User-Agent": "ats-matrix/1.0", **({"Authorization": f"Bearer {tok}"} if tok else {})}
        self.http = http

    def __repr__(self) -> str:
        return "Ibkr(gateway)"

    def _call(self, method: str, path: str, body=None):
        return self.http(method, self.base + path, self.headers, body)

    def status(self) -> dict:
        s = self._call("POST", "/iserver/auth/status", {}) or {}
        return {"authenticated": bool(s.get("authenticated")), "connected": bool(s.get("connected"))}

    def account(self) -> str:
        if self.account_id:
            return self.account_id
        res = self._call("GET", "/iserver/accounts") or {}
        accts = res.get("accounts") or []
        if not accts:
            raise Refused("The IBKR gateway is not signed in. Sign in on the gateway page and try again.")
        self.account_id = str(res.get("selectedAccount") or accts[0])
        return self.account_id

    @staticmethod
    def is_paper(account_id: str) -> bool:
        return str(account_id).upper().startswith("DU")

    def conid(self, symbol: str) -> int:
        s = str(symbol).strip().upper()
        if s.isdigit():
            return int(s)
        rows = self._call("GET", "/iserver/secdef/search?" + urllib.parse.urlencode({"symbol": s})) or []
        hit = next((r for r in rows if str(r.get("symbol", "")).upper() == s and r.get("conid")), rows[0] if rows else None)
        if not hit or not hit.get("conid"):
            raise Refused(f"IBKR has no contract called {s}.")
        return int(hit["conid"])

    def snapshot(self) -> dict:
        acct = self.account()
        summary = self._call("GET", f"/portfolio/{acct}/summary") or {}
        positions = self._call("GET", f"/portfolio/{acct}/positions/0") or []

        def amt(k):
            v = summary.get(k) or {}
            return float(v.get("amount")) if isinstance(v, dict) and v.get("amount") is not None else None
        return {"account": {"id_tail": acct[-4:], "paper": self.is_paper(acct), "equity": amt("netliquidation"),
                            "cash": amt("totalcashvalue"), "currency": (summary.get("netliquidation") or {}).get("currency")},
                "positions": [{"symbol": p.get("contractDesc") or p.get("ticker"), "conid": p.get("conid"),
                               "qty": p.get("position"), "avg": p.get("avgCost"), "mark": p.get("mktPrice"),
                               "value": p.get("mktValue"), "pnl": p.get("unrealizedPnl")} for p in positions]}

    def _check_mode(self, live: bool) -> str:
        acct = self.account()
        if live and self.is_paper(acct):
            raise Refused("Live is on but the IBKR gateway is signed in to a paper account. Sign in with your real login.")
        if not live and not self.is_paper(acct):
            raise Refused("Demo is on but the IBKR gateway is signed in to a real account. Use the paper login, "
                          "or switch One account to Live.")
        return acct

    def order(self, live: bool, symbol: str, side: str, qty: float, stop_loss=None, take_profit=None) -> dict:
        if side not in ("buy", "sell") or not float(qty) > 0:
            raise Refused("side must be buy or sell and quantity above 0")
        if live and not stop_loss:
            raise Refused("Live orders need a stop loss.")
        acct = self._check_mode(live)
        conid = self.conid(symbol)
        parent = f"ats-{uuid.uuid4().hex[:12]}"
        exit_side = "SELL" if side == "buy" else "BUY"
        orders = [{"acctId": acct, "conid": conid, "cOID": parent, "orderType": "MKT", "side": side.upper(),
                   "quantity": float(qty), "tif": "DAY"}]
        if stop_loss:
            orders.append({"acctId": acct, "conid": conid, "cOID": parent + "-sl", "parentId": parent, "orderType": "STP",
                           "price": float(stop_loss), "side": exit_side, "quantity": float(qty), "tif": "GTC"})
        if take_profit:
            orders.append({"acctId": acct, "conid": conid, "cOID": parent + "-tp", "parentId": parent, "orderType": "LMT",
                           "price": float(take_profit), "side": exit_side, "quantity": float(qty), "tif": "GTC"})
        res = self._call("POST", f"/iserver/account/{acct}/orders", {"orders": orders})
        for _ in range(4):  # IBKR asks "are you sure?" for some orders; answer for this order only
            first = res[0] if isinstance(res, list) and res else res if isinstance(res, dict) else {}
            if not (isinstance(first, dict) and first.get("id") and first.get("message")):
                break
            res = self._call("POST", f"/iserver/reply/{urllib.parse.quote(str(first['id']), safe='')}", {"confirmed": True})
        rows = res if isinstance(res, list) else [res]
        ok = any(isinstance(r, dict) and (r.get("order_id") or r.get("orderId")) for r in rows)
        err = next((r.get("error") for r in rows if isinstance(r, dict) and r.get("error")), None)
        return {"platform": "ibkr", "mode": "live" if live else "demo", "symbol": str(symbol).upper(), "side": side,
                "qty": float(qty), "ok": ok and not err, "message": err,
                "order_ids": [r.get("order_id") or r.get("orderId") for r in rows if isinstance(r, dict)]}

    def close(self, live: bool, symbol: str) -> dict:
        """Flatten one symbol with a market order the other way."""
        acct = self._check_mode(live)
        conid = self.conid(symbol)
        held = next((p for p in self._call("GET", f"/portfolio/{acct}/positions/0") or []
                     if int(p.get("conid") or 0) == conid), None)
        qty = float((held or {}).get("position") or 0)
        if not qty:
            raise Refused(f"No IBKR position in {symbol}.")
        res = self._call("POST", f"/iserver/account/{acct}/orders",
                         {"orders": [{"acctId": acct, "conid": conid, "orderType": "MKT", "side": "SELL" if qty > 0 else "BUY",
                                      "quantity": abs(qty), "tif": "DAY", "cOID": f"ats-close-{uuid.uuid4().hex[:10]}"}]})
        first = res[0] if isinstance(res, list) and res else {}
        if isinstance(first, dict) and first.get("id") and first.get("message"):
            res = self._call("POST", f"/iserver/reply/{urllib.parse.quote(str(first['id']), safe='')}", {"confirmed": True})
        return {"platform": "ibkr", "mode": "live" if live else "demo", "symbol": str(symbol).upper(), "closed_qty": qty,
                "result": res}


def from_ibkr(snap: dict | None, error: str | None = None, live: bool = False) -> dict:
    """IBKR as a row of the one account (same shape as one_account._row)."""
    from .one_account import _row  # local import: one_account imports nothing from here at module load
    name = "Interactive Brokers " + ("real" if live else "paper")
    if error:
        return _row("ibkr", name, "broker", "error", note=error)
    if not snap:
        return _row("ibkr", name, "broker", "not_connected", note="add IBKR_GATEWAY_URL (your signed-in IBKR gateway)")
    acc = snap.get("account") or {}
    pos = [{"platform": "ibkr", "symbol": p.get("symbol"), "side": "buy" if float(p.get("qty") or 0) >= 0 else "sell",
            "notional": abs(float(p.get("value") or 0)), "pnl": float(p.get("pnl") or 0), "entry": p.get("avg"),
            "mark": p.get("mark")} for p in snap.get("positions") or []]
    upnl = sum(p["pnl"] for p in pos)
    eq = acc.get("equity")
    return _row("ibkr", name, "broker", "connected", start=None if eq is None else eq - upnl, equity=eq, unrealized=upnl,
                positions=pos, note=f"IBKR · {'paper' if acc.get('paper') else 'REAL'} · account …{acc.get('id_tail', '')}",
                currency=acc.get("currency") or "USD")


def explain(e: Exception) -> str:
    code = getattr(e, "code", None)
    if isinstance(e, PermissionError):
        return str(e)
    if code in (401, 403):
        return "The broker refused the keys (401/403). Check the env vars on Render."
    if isinstance(e, ssl.SSLError):
        return "Could not make a secure connection to the broker."
    return f"{type(e).__name__}: {str(e)[:160]}"
