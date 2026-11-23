"""MetaTrader 5 through the MetaApi cloud, so the app can use an MT5 account (e.g. GO Markets) from anywhere.

The official MetaTrader5 Python package only runs on Windows next to the terminal. MetaApi (metaapi.cloud)
hosts the terminal for us and gives a plain HTTPS API, so the app on Render can read the account and send
demo orders with no Mac or Windows machine switched on.

Safety:
- Reading (account, positions, deal history) is always allowed.
- Orders are sent only when the account reports type ACCOUNT_TRADE_MODE_DEMO. Anything else is refused,
  whatever the settings say. Real money stays locked (platforms.LIVE_TRADING_ENABLED is False).
- The token and account id come only from environment variables. Nothing here logs or returns them.

Environment (set them on Render, never in the repo):
  METAAPI_TOKEN        API token from app.metaapi.cloud (Settings > API tokens)
  METAAPI_ACCOUNT_ID   optional: the id MetaApi shows for your MT5 account. Left empty, the app finds it
                       with the token (the demo account first) and deploys it if it is switched off.
  METAAPI_REGION       optional: taken from the account when it is found automatically, else new-york
"""

import json
import os
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import datetime, timedelta, timezone

HTTP_TIMEOUT = 20
DEMO = "ACCOUNT_TRADE_MODE_DEMO"
CLOSING = ("DEAL_ENTRY_OUT", "DEAL_ENTRY_INOUT", "DEAL_ENTRY_OUT_BY")
TRADE_TYPES = ("DEAL_TYPE_BUY", "DEAL_TYPE_SELL")
ENV_KEYS = ("METAAPI_TOKEN",)
PROVISIONING = "https://mt-provisioning-api-v1.agiliumtrade.agiliumtrade.ai/users/current/accounts"
_found: dict = {}  # token fingerprint -> (account id, region), so discovery runs once per process


class NotDemo(PermissionError):
    pass


def http_json(method: str, url: str, headers: dict, body: dict | None = None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Accept": "application/json", "Content-Type": "application/json", **headers})
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:  # noqa: S310 (fixed https host)
        return json.loads(resp.read() or b"null")


def explain(e: Exception) -> str:
    """A plain reason the owner can act on, never the token."""
    code = getattr(e, "code", None)
    if code in (401, 403):
        return ("MetaApi token rejected: it was deleted or is wrong. Make a new token at app.metaapi.cloud "
                "(Settings > API tokens) and paste it into METAAPI_TOKEN on Render.")
    if code == 404:
        return "MetaApi could not find that account. Check it is added in app.metaapi.cloud."
    if isinstance(e, PermissionError):
        return str(e)
    return f"MetaTrader cloud: {type(e).__name__}: {str(e)[:200]}"


def configured(env=os.environ) -> bool:
    return all(env.get(k) for k in ENV_KEYS)


def _pick(accounts: list[dict]) -> dict:
    """Prefer an MT5 demo account, then any MT5 account, then anything."""
    def score(a):
        server = str(a.get("server", "")).lower()
        return (str(a.get("platform", "mt5")).lower() == "mt5", "demo" in server or a.get("type") == "demo",
                a.get("state") == "DEPLOYED")
    return max(accounts, key=score)


def discover(token: str, http=http_json) -> tuple[str, str]:
    """Find the MetaApi account for this token (and deploy it if it is off). Returns (id, region)."""
    key = hash(token)
    if key in _found:
        return _found[key]
    accounts = http("GET", PROVISIONING, {"auth-token": token}) or []
    if not accounts:
        raise PermissionError("No MetaTrader account is added in MetaApi yet. Add your MT5 demo login at "
                              "app.metaapi.cloud.")
    acc = _pick(accounts)
    acc_id = str(acc.get("_id") or acc.get("id"))
    if acc.get("state") not in ("DEPLOYED", "DEPLOYING"):
        http("POST", f"{PROVISIONING}/{urllib.parse.quote(acc_id, safe='')}/deploy", {"auth-token": token}, {})
    _found[key] = (acc_id, str(acc.get("region") or "new-york"))
    return _found[key]


class MetaApi:
    def __init__(self, env=os.environ, http=http_json):
        missing = [k for k in ENV_KEYS if not env.get(k)]
        if missing:
            raise PermissionError(f"Set {', '.join(missing)} on the server to connect MetaTrader 5.")
        self._token = env["METAAPI_TOKEN"].strip()
        acc_id, region = (env.get("METAAPI_ACCOUNT_ID") or "").strip(), env.get("METAAPI_REGION")
        if not acc_id:
            acc_id, found_region = discover(self._token, http)
            region = region or found_region
        self.account_id = urllib.parse.quote(acc_id, safe="")
        region = (region or "new-york").strip().lower()
        if not region.replace("-", "").isalnum():
            raise ValueError("METAAPI_REGION looks wrong")
        self.base = f"https://mt-client-api-v1.{region}.agiliumtrade.ai/users/current/accounts/{self.account_id}"
        self.http = http

    def __repr__(self) -> str:  # never show the token
        return f"MetaApi(account=…{self.account_id[-4:]})"

    def _get(self, path: str):
        return self.http("GET", self.base + path, {"auth-token": self._token})

    # ---- read only ----
    def account(self) -> dict:
        return self._get("/account-information")

    def positions(self) -> list[dict]:
        return self._get("/positions") or []

    def deals(self, start: datetime, end: datetime) -> list[dict]:
        out, offset = [], 0
        s, e = (urllib.parse.quote(t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")) for t in (start, end))
        while True:
            page = self._get(f"/history-deals/time/{s}/{e}?offset={offset}&limit=1000") or []
            out.extend(page)
            if len(page) < 1000 or offset > 50_000:
                return out
            offset += 1000

    def price(self, symbol: str) -> float:
        p = self._get(f"/symbols/{urllib.parse.quote(symbol, safe='')}/current-price")
        return (float(p["bid"]) + float(p["ask"])) / 2

    def is_demo(self) -> bool:
        return self.account().get("type") == DEMO

    # ---- demo orders only ----
    def demo_order(self, symbol: str, side: str, volume: float, stop_loss: float | None = None,
                   take_profit: float | None = None) -> dict:
        if side not in ("buy", "sell") or not 0 < float(volume) <= 100:
            raise ValueError("side must be buy or sell and volume between 0 and 100 lots")
        account = self.account()
        if account.get("type") != DEMO:
            raise NotDemo("This MetaTrader account is not a demo account. Real money is locked, so no order was sent.")
        body = {"actionType": "ORDER_TYPE_BUY" if side == "buy" else "ORDER_TYPE_SELL", "symbol": symbol,
                "volume": float(volume), "comment": "claude-trading-agents demo"}
        if stop_loss:
            body["stopLoss"] = float(stop_loss)
        if take_profit:
            body["takeProfit"] = float(take_profit)
        result = self.http("POST", self.base + "/trade", {"auth-token": self._token}, body)
        return {"platform": "mt5", "mode": "demo", "via": "metaapi", "symbol": symbol, "side": side,
                "qty": float(volume), "ok": result.get("stringCode") == "TRADE_RETCODE_DONE",
                "code": result.get("stringCode"), "message": result.get("message"),
                "position_id": result.get("positionId"), "order_id": result.get("orderId")}

    def demo_close(self, position_id: str) -> dict:
        if not self.is_demo():
            raise NotDemo("This MetaTrader account is not a demo account. Real money is locked.")
        result = self.http("POST", self.base + "/trade", {"auth-token": self._token},
                           {"actionType": "POSITION_CLOSE_ID", "positionId": str(position_id)})
        return {"ok": result.get("stringCode") == "TRADE_RETCODE_DONE", "code": result.get("stringCode"),
                "message": result.get("message")}

    # ---- views for the app ----
    def snapshot(self) -> dict:
        acc = self.account()
        return {"account": public_account(acc), "positions": [public_position(p) for p in self.positions()]}

    def report(self, days: int = 30, now: datetime | None = None) -> dict:
        now = now or datetime.now(timezone.utc)
        days = max(1, min(int(days), 365))
        return loss_report(self.deals(now - timedelta(days=days), now), public_account(self.account()), days)


def public_account(a: dict) -> dict:
    keep = ("broker", "server", "currency", "balance", "equity", "margin", "freeMargin", "marginLevel",
            "leverage", "name", "type", "tradeAllowed")
    out = {k: a.get(k) for k in keep if k in a}
    out["demo"] = a.get("type") == DEMO
    return out


def public_position(p: dict) -> dict:
    keep = ("id", "symbol", "type", "volume", "openPrice", "currentPrice", "stopLoss", "takeProfit", "profit",
            "swap", "commission", "unrealizedProfit", "time")
    return {k: p.get(k) for k in keep if k in p}


def _net(d: dict) -> float:
    return float(d.get("profit") or 0) + float(d.get("commission") or 0) + float(d.get("swap") or 0)


def loss_report(deals: list[dict], account: dict | None = None, days: int = 30) -> dict:
    """Where the money went: closed trades only (deposits and withdrawals are listed apart)."""
    closed = [d for d in deals if d.get("type") in TRADE_TYPES and d.get("entryType") in CLOSING]
    cash = [d for d in deals if d.get("type") == "DEAL_TYPE_BALANCE"]
    costs = sum(float(d.get("commission") or 0) + float(d.get("swap") or 0) for d in deals
                if d.get("type") in TRADE_TYPES)
    closed.sort(key=lambda d: d.get("time", ""))
    nets = [_net(d) for d in closed]
    wins = [n for n in nets if n > 0]
    losses = [n for n in nets if n < 0]
    equity = peak = drawdown = 0.0
    for n in nets:
        equity += n
        peak = max(peak, equity)
        drawdown = min(drawdown, equity - peak)
    by_symbol = defaultdict(lambda: {"trades": 0, "net": 0.0, "wins": 0})
    by_side = defaultdict(lambda: {"trades": 0, "net": 0.0, "wins": 0})
    for d, n in zip(closed, nets):
        # the closing deal is the opposite side of the position it closes
        side = "long" if d.get("type") == "DEAL_TYPE_SELL" else "short"
        for bucket in (by_symbol[d.get("symbol") or "?"], by_side[side]):
            bucket["trades"] += 1
            bucket["net"] = round(bucket["net"] + n, 2)
            bucket["wins"] += n > 0
    worst = sorted(zip(closed, nets), key=lambda x: x[1])[:5]
    streak = longest = 0
    for n in nets:
        streak = streak + 1 if n < 0 else 0
        longest = max(longest, streak)
    total = round(sum(nets), 2)
    report = {
        "days": days, "trades": len(closed), "net": total,
        "win_rate": round(len(wins) / len(nets), 3) if nets else None,
        "avg_win": round(sum(wins) / len(wins), 2) if wins else 0.0,
        "avg_loss": round(sum(losses) / len(losses), 2) if losses else 0.0,
        "profit_factor": round(sum(wins) / -sum(losses), 2) if losses else None,
        "costs": round(costs, 2), "max_drawdown": round(drawdown, 2), "longest_losing_streak": longest,
        "deposits_withdrawals": round(sum(_net(d) for d in cash), 2),
        "by_symbol": dict(sorted(by_symbol.items(), key=lambda kv: kv[1]["net"])),
        "by_side": dict(by_side),
        "worst_trades": [{"symbol": d.get("symbol"), "time": d.get("time"), "volume": d.get("volume"),
                          "net": round(n, 2)} for d, n in worst if n < 0],
    }
    if account:
        report["account"] = account
        bal = account.get("balance")
        if isinstance(bal, (int, float)) and bal - total > 0:
            report["net_pct_of_start"] = round(100 * total / (bal - total), 2)
    report["notes"] = notes(report)
    return report


def notes(r: dict) -> list[str]:
    """Plain-English reasons, so the owner sees the cause and not just numbers."""
    out = []
    if not r["trades"]:
        return ["No closed trades in this period."]
    if r["avg_loss"] and r["avg_win"] and -r["avg_loss"] > r["avg_win"]:
        out.append(f"Average loss ({-r['avg_loss']:.2f}) is bigger than average win ({r['avg_win']:.2f}): "
                   "losers are being held too long or stops are too wide.")
    if r["win_rate"] is not None and r["win_rate"] < 0.45:
        out.append(f"Only {r['win_rate']:.0%} of trades won.")
    losers = [(s, v) for s, v in r["by_symbol"].items() if v["net"] < 0]
    if losers and r["net"] < 0:
        s, v = losers[0]
        out.append(f"{s} lost the most: {v['net']:.2f} over {v['trades']} trades "
                   f"({v['net'] / r['net']:.0%} of the total loss).")
    if r["costs"] < 0 and r["net"] and abs(r["costs"]) > 0.2 * abs(r["net"]):
        out.append(f"Commission and swap cost {-r['costs']:.2f}, a big share of the result.")
    if r["longest_losing_streak"] >= 4:
        out.append(f"{r['longest_losing_streak']} losses in a row at worst: a daily loss stop would have helped.")
    if worst := r["worst_trades"]:
        if r["net"] < 0 and -worst[0]["net"] > 0.3 * -r["net"]:
            out.append(f"One trade ({worst[0]['symbol']}, {worst[0]['net']:.2f}) made up a large part of the loss.")
    return [n for n in out if n] or ["Nothing stands out; results look balanced."]
