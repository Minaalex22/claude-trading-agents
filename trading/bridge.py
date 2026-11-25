"""The bridge: places the owner's approved real-money orders through moomoo, from the owner's own computer.

Run it on the computer where moomoo's OpenD app is open and logged in:

  export BRIDGE_URL=https://<your app>.onrender.com
  export BRIDGE_TOKEN=...                      # the same value as the app's BRIDGE_TOKEN setting
  export MOOMOO_TRADE_PASSWORD=...             # moomoo's trade password; stays on this computer
  export LIVE_MAX_ORDER_USD=500 LIVE_MAX_DAY_USD=1000 LIVE_SYMBOLS=AAPL,MSFT,NVDA
  export BRIDGE_REAL_MONEY=I-ACCEPT-THE-RISK   # without this it places on moomoo's paper account
  python -m trading.bridge

It only reaches out (HTTPS to the app, and OpenD on this computer); nothing can connect in. Every 5 seconds it
asks the app for the next approved order, re-checks this computer's own limits (a hacked app still can't
make it place a bigger order), places a limit order a little through the price, and reports the result and
the account's positions back. A file named KILL (or STOP_TRADING, as on the JARVIS Mac) next to where it runs stops it placing anything.
"""

import json
import os
import sys
import time
import urllib.request
from pathlib import Path

from .live_orders import Limits

POLL_SECONDS = 5.0
LIMIT_SLACK = 0.003  # limit price 0.3% through the last price, so the order fills but can't run away
REAL_MONEY_PHRASE = "I-ACCEPT-THE-RISK"


class Refused(Exception):
    pass


def check(order: dict, limits: Limits, spent_today: float, orders_today: int = 0) -> None:
    """This computer's own limits, checked again before anything is placed."""
    if not limits.on:
        raise Refused("LIVE_MAX_ORDER_USD, LIVE_MAX_DAY_USD and LIVE_SYMBOLS must all be set on this computer")
    symbol, side, qty, price = order["symbol"], order["side"], order["qty"], order["price"]
    if symbol not in limits.symbols:
        raise Refused(f"{symbol} is not in this computer's LIVE_SYMBOLS")
    if side not in ("buy", "sell") or not isinstance(qty, int) or qty <= 0 or not price > 0:
        raise Refused("malformed order")
    if qty * price > limits.max_order * 1.02:  # allow a small move between proposal and placing
        raise Refused(f"order is ${qty * price:,.0f}, over this computer's ${limits.max_order:,.0f} limit")
    if side == "buy" and spent_today + qty * price > limits.max_day:
        raise Refused("over this computer's daily limit")
    if orders_today >= limits.max_orders_day:
        raise Refused(f"already {limits.max_orders_day} orders today on this computer (LIVE_MAX_ORDERS_DAY)")


class MoomooBroker:
    """US stocks through moomoo OpenD. `real` picks the real account; otherwise moomoo's paper account."""

    def __init__(self, real: bool, env=os.environ, mm=None):
        if mm is None:
            import moomoo as mm  # pip install moomoo-api
        self.mm = mm
        self.real = real
        self.env_ = mm.TrdEnv.REAL if real else mm.TrdEnv.SIMULATE
        self.ctx = mm.OpenSecTradeContext(filter_trdmarket=mm.TrdMarket.US,
                                          host=env.get("MOOMOO_HOST", "127.0.0.1"),
                                          port=int(env.get("MOOMOO_PORT", 11111)),
                                          security_firm=getattr(mm.SecurityFirm, env.get("MOOMOO_FIRM", "FUTUAU")))
        if real:
            ret, data = self.ctx.unlock_trade(env["MOOMOO_TRADE_PASSWORD"])
            if ret != mm.RET_OK:
                raise RuntimeError(f"moomoo would not unlock trading: {data}")

    def place(self, symbol: str, side: str, qty: int, price: float) -> dict:
        buy = side == "buy"
        limit = round(price * (1 + LIMIT_SLACK if buy else 1 - LIMIT_SLACK), 2)
        ret, data = self.ctx.place_order(price=limit, qty=qty, code=f"US.{symbol}",
                                         trd_side=self.mm.TrdSide.BUY if buy else self.mm.TrdSide.SELL,
                                         order_type=self.mm.OrderType.NORMAL, trd_env=self.env_)
        if ret != self.mm.RET_OK:
            raise RuntimeError(f"moomoo refused the order: {data}")
        return {"order_id": str(data["order_id"][0]), "limit": limit}

    def place_stop(self, symbol: str, qty: int, stop: float) -> dict:
        """A sell stop resting at moomoo: it triggers at the broker, not in Python."""
        ret, data = self.ctx.place_order(price=round(stop, 2), qty=qty, code=f"US.{symbol}",
                                         trd_side=self.mm.TrdSide.SELL, order_type=self.mm.OrderType.STOP,
                                         aux_price=round(stop, 2), trd_env=self.env_)
        if ret != self.mm.RET_OK:
            raise RuntimeError(f"moomoo refused the stop: {data}")
        return {"order_id": str(data["order_id"][0])}

    def positions(self) -> dict:
        ret, data = self.ctx.position_list_query(trd_env=self.env_)
        if ret != self.mm.RET_OK:
            raise RuntimeError(f"moomoo positions failed: {data}")
        return {str(code).split(".")[-1]: int(q) for code, q in zip(data["code"], data["qty"]) if int(q)}


class Bridge:
    def __init__(self, url: str, token: str, broker, limits: Limits, http=None, kill_path=Path("KILL"),
                 clock=time.time, log=print):
        self.url = url.rstrip("/")
        self.token = token
        self.broker = broker
        self.limits = limits
        self.http = http or self._http
        self.kill_path = kill_path
        self.clock = clock
        self.log = log
        self.spent: dict[str, float] = {}  # UTC day -> dollars bought
        self.placed: dict[str, int] = {}  # UTC day -> orders placed
        self.pending_stops: dict[str, tuple[int, float]] = {}  # symbol -> (qty, stop) to rest once the buy fills
        self.synced = False

    def _http(self, method: str, path: str, body: dict | None = None) -> dict:
        req = urllib.request.Request(self.url + path, method=method,
                                     data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Authorization": f"Bearer {self.token}",
                                              "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=15) as resp:  # noqa: S310 (the owner's own app)
            return json.loads(resp.read() or b"{}")

    def sync(self) -> dict | None:
        """At start: tell the app what the broker really holds, so a restart never doubles or orphans a position."""
        try:
            positions = self.broker.positions()
        except RuntimeError:
            return None
        self.http("POST", "/api/bridge/positions", {"positions": positions})
        self.synced = True
        return positions

    def rest_stops(self, positions: dict | None) -> None:
        """Broker-side protection: once a buy shows in the broker's positions, rest its stop there, so it holds even
        if this computer or the app goes offline."""
        for symbol, (qty, stop) in list(self.pending_stops.items()):
            held = int((positions or {}).get(symbol, 0))
            if held <= 0:
                continue
            try:
                placed = self.broker.place_stop(symbol, min(qty, held), stop)
                self.log(f"stop resting at the broker: sell {min(qty, held)} {symbol} at {stop} ({placed['order_id']})")
                del self.pending_stops[symbol]
            except (RuntimeError, AttributeError) as e:
                self.log(f"WARNING: could not rest the stop for {symbol} at the broker: {e}")

    def once(self) -> str:
        if self.kill_path.exists() or self.kill_path.with_name("STOP_TRADING").exists():
            return "kill switch on"
        if not self.synced:
            self.sync()
        if self.pending_stops:
            try:
                self.rest_stops(self.broker.positions())
            except RuntimeError:
                pass
        order = self.http("GET", "/api/bridge/next").get("order")
        if not order:
            return "idle"
        day = time.strftime("%Y-%m-%d", time.gmtime(self.clock()))
        try:
            check(order, self.limits, self.spent.get(day, 0.0), self.placed.get(day, 0))
            placed = self.broker.place(order["symbol"], order["side"], order["qty"], order["price"])
            self.placed[day] = self.placed.get(day, 0) + 1
            if order["side"] == "buy":
                self.spent[day] = self.spent.get(day, 0.0) + order["qty"] * order["price"]
                if order.get("stop_price"):
                    self.pending_stops[order["symbol"]] = (int(order["qty"]), float(order["stop_price"]))
            ok, detail = True, f"placed {'real' if self.broker.real else 'paper'} order {placed['order_id']} " \
                               f"limit {placed['limit']}"
        except (Refused, RuntimeError, KeyError) as e:
            ok, detail = False, str(e)
        try:
            positions = self.broker.positions()
        except RuntimeError:
            positions = None
        self.rest_stops(positions)
        self.http("POST", "/api/bridge/result", {"id": order["id"], "ok": ok, "detail": detail,
                                                  "filled_qty": order["qty"] if ok else 0,
                                                  "fill_price": order["price"] if ok else None,
                                                  "positions": positions})
        self.log(f"{order['side']} {order['qty']} {order['symbol']}: {detail}")
        return detail


def main(env=os.environ) -> None:
    for name in ("BRIDGE_URL", "BRIDGE_TOKEN"):
        if not env.get(name):
            sys.exit(f"Set {name} first (see python -m trading.bridge --help).")
    url = env["BRIDGE_URL"]
    local = url.startswith(("http://127.0.0.1", "http://localhost"))  # the app running on this same computer
    if not (url.startswith("https://") or local):
        sys.exit("BRIDGE_URL must start with https:// (or be http://127.0.0.1 for the app on this computer)")
    real = env.get("BRIDGE_REAL_MONEY") == REAL_MONEY_PHRASE
    if real and not env.get("MOOMOO_TRADE_PASSWORD"):
        sys.exit("Set MOOMOO_TRADE_PASSWORD to trade the real account.")
    limits = Limits(env)
    if not limits.on:
        sys.exit("Set LIVE_MAX_ORDER_USD, LIVE_MAX_DAY_USD and LIVE_SYMBOLS on this computer too.")
    bridge = Bridge(env["BRIDGE_URL"], env["BRIDGE_TOKEN"], MoomooBroker(real, env), limits)
    print(f"Bridge running: {'REAL MONEY' if real else 'moomoo paper account'}, "
          f"max ${limits.max_order:,.0f} per order, ${limits.max_day:,.0f} a day, {sorted(limits.symbols)}")
    while True:
        try:
            bridge.once()
        except OSError as e:  # the app is restarting or the network blipped: try again
            print(f"app not reachable: {e}")
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    if "--help" in sys.argv:
        print(__doc__)
    else:
        main()
