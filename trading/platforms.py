"""Connectors for the real platforms: Polymarket, eToro, MetaTrader 5, moomoo and BTCC.

Safety first:
- Every platform starts in **paper** mode: quotes come from the real platform, but orders fill on
  our own paper book (trading/broker.py). Nothing reaches the platform's order endpoint.
- MetaTrader 5 and moomoo also support **demo** mode (<KEY>_MODE=demo): orders go to the broker's
  own practice account. The connector checks the account really is a demo account first.
- **Real money is locked.** LIVE_TRADING_ENABLED is False in code, and <KEY>_MODE=live is refused
  (the platform stays in paper mode). Switching it on needs a reviewed code change after the
  paper-trading period, not just an environment variable.
- Keys come only from environment variables. status() reports key *names*, never values.

Environment:
  POLYMARKET_MODE                    quotes need no key (public order book)
  ETORO_MODE, ETORO_API_KEY, ETORO_USER_KEY, ETORO_API_BASE (default https://public-api.etoro.com)
  METAAPI_TOKEN (+ optional METAAPI_ACCOUNT_ID, MT5_MODE defaults to demo) (MetaApi cloud, no computer needed)
  or MT5_LOGIN, MT5_PASSWORD, MT5_SERVER          (MetaTrader5 package next to the terminal, Windows only)
  MOOMOO_MODE, MOOMOO_HOST, MOOMOO_PORT           (needs the moomoo package and the OpenD gateway)
  BTCC_API_KEY, BTCC_SECRET_KEY, BTCC_USERNAME, BTCC_PASSWORD, BTCC_API_BASE_URL  (read only, trading/btcc.py)

Run `python -m trading.platforms` for a status table, or
`python -m trading.platforms quote polymarket <token_id>` to read a live price.
"""

import json
import os
import sys
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass

from . import metaapi
from .broker import PaperBroker

LIVE_TRADING_ENABLED = False  # real-money orders: locked in this build
MODES = ("paper", "demo")
HTTP_TIMEOUT = 10


class LiveTradingLocked(PermissionError):
    pass


@dataclass(frozen=True)
class Platform:
    key: str
    name: str
    markets: str
    keys: tuple[str, ...]  # environment variables a connector needs (names only)
    demo: bool  # broker offers its own practice account we can trade


PLATFORMS = (
    Platform("polymarket", "Polymarket", "prediction markets", (), False),
    Platform("etoro", "eToro", "stocks, crypto, ETFs, FX", ("ETORO_API_KEY", "ETORO_USER_KEY"), False),
    Platform("mt5", "MetaTrader 5", "FX, CFDs, indices, metals", ("METAAPI_TOKEN",), True),
    Platform("moomoo", "moomoo", "US and HK stocks, options", (), True),
    # BTCC paused its Futures API on 13 Feb 2026; read-only bridge + practice desk in trading/btcc.py
    Platform("btcc", "BTCC", "crypto futures (USDT perpetuals)", ("BTCC_API_KEY", "BTCC_SECRET_KEY"), False),
    # Mitrade has no API or MT4/MT5; live bid/ask from its public site feed + practice desk in trading/mitrade.py
    Platform("mitrade", "Mitrade", "CFDs: forex, gold, oil, indices, crypto, shares", (), False),
    # Legal in Australia. Paper by default; real bets only via trading/betfair.py with BETFAIR_REAL_MONEY=on + confirm + money rules
    Platform("betfair", "Betfair Exchange (AU)", "sports, racing, politics (exchange)", ("BETFAIR_APP_KEY", "BETFAIR_USERNAME", "BETFAIR_PASSWORD"), False),
)
BY_KEY = {p.key: p for p in PLATFORMS}
SECRET_NAMES = tuple(k for p in PLATFORMS for k in p.keys if not k.endswith(("LOGIN", "SERVER", "ACCOUNT_ID")))
NATIVE_MT5 = ("MT5_LOGIN", "MT5_PASSWORD", "MT5_SERVER")


def get_json(url: str, headers: dict | None = None) -> dict:
    req = urllib.request.Request(url, headers={"Accept": "application/json", **(headers or {})})
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:  # noqa: S310 (fixed https hosts)
        return json.loads(resp.read())


def requested_mode(platform: Platform, env=os.environ) -> str:
    default = "paper"
    if platform.key == "mt5" and env.get("METAAPI_TOKEN"):
        default = "demo"  # MetaApi orders are refused on anything but a demo account, so demo is the safe default
    return (env.get(f"{platform.key.upper()}_MODE") or default).strip().lower()


def resolve_mode(platform: Platform, env=os.environ) -> str:
    """The mode the connector will actually use: anything unsafe or unsupported falls back to paper."""
    mode = requested_mode(platform, env)
    if mode == "demo" and platform.demo:
        return "demo"
    return "paper"


class Connector:
    platform: Platform

    def __init__(self, env=os.environ, http=get_json, book: PaperBroker | None = None):
        self.env = env
        self.http = http
        self.mode = resolve_mode(self.platform, env)
        self.book = book or PaperBroker(cash=100_000.0, fee_bps=10.0)
        self.fills: list[dict] = []

    @property
    def missing(self) -> list[str]:
        return [k for k in self.platform.keys if not self.env.get(k)]

    @property
    def configured(self) -> bool:
        return not self.missing

    def quote(self, symbol: str) -> float:
        raise NotImplementedError

    def place_order(self, symbol: str, side: str, qty: float, live: bool = False) -> dict:
        """Paper by default; demo on the broker's practice account; real money always refused."""
        if live:  # there is no real-money path in this build, whatever LIVE_TRADING_ENABLED says

            raise LiveTradingLocked("Real-money orders are locked. Paper trade on live prices for 1-2 weeks first.")
        if side not in ("buy", "sell") or not qty > 0:
            raise ValueError("side must be buy or sell and qty must be positive")
        if self.mode == "demo":
            fill = self._demo_order(symbol, side, qty)
        else:
            price = self.quote(symbol)
            self.book.fill(symbol, qty if side == "buy" else -qty, price)
            fill = {"platform": self.platform.key, "mode": "paper", "symbol": symbol, "side": side, "qty": qty,
                    "price": price}
        self.fills.append(fill)
        return fill

    def _demo_order(self, symbol: str, side: str, qty: float) -> dict:
        raise LiveTradingLocked(f"{self.platform.name} has no demo account here; use paper mode.")


class PolymarketConnector(Connector):
    platform = BY_KEY["polymarket"]
    CLOB = "https://clob.polymarket.com"

    def quote(self, token_id: str) -> float:
        data = self.http(f"{self.CLOB}/midpoint?{urllib.parse.urlencode({'token_id': token_id})}")
        return float(data["mid"])


class EToroConnector(Connector):
    platform = BY_KEY["etoro"]

    def _headers(self) -> dict:
        if not self.configured:
            raise PermissionError(f"Set {', '.join(self.missing)} to read eToro prices.")
        return {"x-api-key": self.env["ETORO_API_KEY"], "x-user-key": self.env["ETORO_USER_KEY"],
                "x-request-id": str(uuid.uuid4())}

    @property
    def base(self) -> str:
        return self.env.get("ETORO_API_BASE", "https://public-api.etoro.com").rstrip("/")

    def quote(self, instrument_id: str) -> float:
        query = urllib.parse.urlencode({"instrumentIds": int(instrument_id)})
        data = self.http(f"{self.base}/api/v2/market-data/rates?{query}", self._headers())
        rate = data["results"][0]
        return (float(rate["bid"]) + float(rate["ask"])) / 2

    def demo_portfolio(self) -> dict:
        """Read-only view of the eToro virtual (demo) portfolio."""
        return self.http(f"{self.base}/api/v1/trading/info/demo/portfolio", self._headers())


class MT5Connector(Connector):
    platform = BY_KEY["mt5"]

    @property
    def cloud(self) -> bool:
        return metaapi.configured(self.env)

    @property
    def missing(self) -> list[str]:
        if self.cloud or all(self.env.get(k) for k in NATIVE_MT5):
            return []
        return list(self.platform.keys)

    def api(self) -> "metaapi.MetaApi":
        return metaapi.MetaApi(self.env)

    def _mt5(self):
        try:
            import MetaTrader5 as mt5  # Windows only, with the MetaTrader 5 terminal installed
        except ImportError as e:
            raise RuntimeError("Install the MetaTrader5 package on the Windows machine running the terminal.") from e
        if not mt5.initialize(login=int(self.env["MT5_LOGIN"]), password=self.env["MT5_PASSWORD"],
                              server=self.env["MT5_SERVER"]):
            raise RuntimeError(f"MetaTrader 5 would not connect: {mt5.last_error()}")
        return mt5

    def quote(self, symbol: str) -> float:
        if self.cloud:
            return self.api().price(symbol)
        tick = self._mt5().symbol_info_tick(symbol)
        return (tick.bid + tick.ask) / 2

    def _demo_order(self, symbol: str, side: str, qty: float) -> dict:
        if self.cloud:
            try:
                return self.api().demo_order(symbol, side, qty)
            except metaapi.NotDemo as e:
                raise LiveTradingLocked(str(e)) from e
        mt5 = self._mt5()
        account = mt5.account_info()
        if account is None or account.trade_mode != mt5.ACCOUNT_TRADE_MODE_DEMO:
            raise LiveTradingLocked("This MetaTrader 5 login is not a demo account. Refusing to send the order.")
        tick = mt5.symbol_info_tick(symbol)
        buy = side == "buy"
        result = mt5.order_send({"action": mt5.TRADE_ACTION_DEAL, "symbol": symbol, "volume": float(qty),
                                 "type": mt5.ORDER_TYPE_BUY if buy else mt5.ORDER_TYPE_SELL,
                                 "price": tick.ask if buy else tick.bid, "deviation": 20,
                                 "comment": "claude-trading-agents demo"})
        return {"platform": "mt5", "mode": "demo", "symbol": symbol, "side": side, "qty": qty,
                "price": getattr(result, "price", None), "retcode": getattr(result, "retcode", None)}


class MoomooConnector(Connector):
    platform = BY_KEY["moomoo"]

    def _addr(self):
        return self.env.get("MOOMOO_HOST", "127.0.0.1"), int(self.env.get("MOOMOO_PORT", 11111))

    def _moomoo(self):
        try:
            import moomoo  # talks to the local OpenD gateway
        except ImportError as e:
            raise RuntimeError("Install the moomoo package and run the OpenD gateway.") from e
        return moomoo

    def quote(self, code: str) -> float:
        mm = self._moomoo()
        host, port = self._addr()
        ctx = mm.OpenQuoteContext(host=host, port=port)
        try:
            ret, data = ctx.get_market_snapshot([code])
            if ret != mm.RET_OK:
                raise RuntimeError(f"moomoo quote failed: {data}")
            return float(data["last_price"][0])
        finally:
            ctx.close()

    def _demo_order(self, symbol: str, side: str, qty: float) -> dict:
        mm = self._moomoo()
        host, port = self._addr()
        price = self.quote(symbol)
        ctx = mm.OpenSecTradeContext(host=host, port=port)
        try:
            # TrdEnv.SIMULATE is moomoo's paper account. It is hard-coded: there is no path to REAL here.
            ret, data = ctx.place_order(price=price, qty=qty, code=symbol,
                                        trd_side=mm.TrdSide.BUY if side == "buy" else mm.TrdSide.SELL,
                                        trd_env=mm.TrdEnv.SIMULATE)
        finally:
            ctx.close()
        if ret != mm.RET_OK:
            raise RuntimeError(f"moomoo demo order failed: {data}")
        return {"platform": "moomoo", "mode": "demo", "symbol": symbol, "side": side, "qty": qty, "price": price}


class BtccConnector(Connector):
    """Paper only: BTCC has no demo API and its Futures API is paused. Live prices from public feeds."""
    platform = BY_KEY["btcc"]

    def quote(self, symbol: str) -> float:
        from . import live_market
        return live_market.fetch_price(symbol.upper().replace("USDT", ""))


class MitradeConnector(Connector):
    """Paper only: Mitrade has no trading API. Prices are Mitrade's own, from its public site feed."""
    platform = BY_KEY["mitrade"]

    def quote(self, symbol: str) -> float:
        from . import mitrade
        p = mitrade.price(symbol)
        if p is None:
            raise RuntimeError("no Mitrade price right now")
        return p


CONNECTORS = {c.platform.key: c for c in (PolymarketConnector, EToroConnector, MT5Connector, MoomooConnector,
                                          BtccConnector, MitradeConnector)}


def connect(key: str, env=os.environ, http=get_json) -> Connector:
    return CONNECTORS[key](env, http)


def status(env=os.environ) -> list[dict]:
    """One row per platform. Key names only, never values."""
    rows = []
    for p in PLATFORMS:
        missing = [k for k in p.keys if not env.get(k)]
        if p.key == "mt5" and all(env.get(k) for k in NATIVE_MT5):
            missing = []
        asked = requested_mode(p, env)
        mode = resolve_mode(p, env)
        note = None
        if asked == "live":
            note = "live was requested but real money is locked; running paper"
        elif asked not in MODES:
            note = f"unknown mode {asked!r}; running paper"
        elif asked == "demo" and mode != "demo":
            note = "no demo account on this platform; running paper"
        if p.key == "betfair" and note is None:
            note = "bridge on /polymarket; real money only with BETFAIR_REAL_MONEY=on, a typed confirm and the money rules"
        if p.key == "mitrade" and note is None:
            note = "no Mitrade API; Mitrade's live prices + practice desk on /mitrade"
        if p.key == "btcc" and note is None:
            note = "BTCC paused its Futures API on 13 Feb 2026; practice desk on /btcc"
        rows.append({"key": p.key, "name": p.name, "markets": p.markets, "mode": mode, "configured": not missing,
                     "needs_keys": bool(p.keys),
                     "missing": missing, "demo_supported": p.demo, "live_locked": not LIVE_TRADING_ENABLED,
                     "note": note})
    return rows


def main(argv=None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) == 3 and argv[0] == "quote":
        print(f"{argv[1]} {argv[2]}: {connect(argv[1]).quote(argv[2])}")
        return
    for row in status():
        keys = "ready" if row["configured"] else "missing " + ", ".join(row["missing"])
        print(f"{row['name']:<14} {row['mode']:<6} {keys}{'  (' + row['note'] + ')' if row['note'] else ''}")
    print("Real-money trading: locked.")


if __name__ == "__main__":
    main()


def validate_prediction_market_order(*a, **k):
    """Australian prediction-market rules (Polymarket paper only, Betfair live gated): see trading/betfair.py."""
    from .betfair import validate_prediction_market_order as check
    return check(*a, **k)
