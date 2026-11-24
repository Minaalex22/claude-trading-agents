"""Live prices for paper trading: the same desk and agents, trading real market prices with paper money.

MARKET=live swaps the simulated market for this feed. A background thread polls public price endpoints
(no keys, no accounts): Coinbase for BTC and ETH (Kraken as backup), Yahoo Finance's chart endpoint for
stocks (a second Yahoo host as backup; US stocks are delayed a little and only move during market hours).
Every tick returns the latest known prices, so the desk never waits on the network. If a feed fails, the
error is counted and the last price stands.

Two guards keep bad data away from the desk:
- **Spikes**: a price more than MAX_JUMP away from the last good one is held back until the next poll
  confirms it (within CONFIRM_BAND), so a single bad print can't trigger trades.
- **Stale prices**: a symbol whose price is older than STALE_SECONDS (feed down, or the stock market is
  closed) is listed by `stale()`. The risk manager lets the desk exit it but not add to it.

This module only reads prices. It has no way to place an order: every fill still goes to the paper
broker, and real money stays locked (trading/platforms.py).
"""

import json
import threading
import time
import urllib.request

CRYPTO = {"BTC": "BTC-USD", "ETH": "ETH-USD", "SOL": "SOL-USD"}
KRAKEN = {"BTC": "XBTUSD", "ETH": "ETHUSD", "SOL": "SOLUSD"}
POLL_SECONDS = 15.0
MAX_JUMP = 0.08  # a move bigger than 8% in one poll needs a second reading
CONFIRM_BAND = 0.02  # the second reading must land within 2% of the first
STALE_SECONDS = 600.0
USER_AGENT = "Mozilla/5.0 (paper-trading price reader)"


def _get_json(url: str, timeout: float = 10.0) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (fixed https URLs only)
        return json.loads(resp.read().decode())


def _crypto(symbol: str, get) -> tuple[float, float]:
    try:
        data = get(f"https://api.coinbase.com/v2/prices/{CRYPTO[symbol]}/spot")
        return float(data["data"]["amount"]), time.time()
    except Exception:
        if symbol not in KRAKEN:
            raise
        data = get(f"https://api.kraken.com/0/public/Ticker?pair={KRAKEN[symbol]}")
        return float(next(iter(data["result"].values()))["c"][0]), time.time()


def _stock(symbol: str, get) -> tuple[float, float]:
    error = None
    for host in ("query1", "query2"):
        try:
            meta = get(f"https://{host}.finance.yahoo.com/v8/finance/chart/{symbol}?interval=1m&range=1d")[
                "chart"]["result"][0]["meta"]
            return float(meta["regularMarketPrice"]), float(meta.get("regularMarketTime") or time.time())
        except Exception as e:
            error = e
    raise error


def fetch_quote(symbol: str, get=_get_json) -> tuple[float, float]:
    """(price, when the price was set as a unix time). Crypto trades around the clock, so its time is now."""
    return _crypto(symbol, get) if symbol in CRYPTO else _stock(symbol, get)


def fetch_price(symbol: str, get=_get_json) -> float:
    return fetch_quote(symbol, get)[0]


class LiveMarket:
    """Drop-in for SimulatedMarket: tick() returns {symbol: price}. `drift` is empty (no hidden truth live)."""

    def __init__(self, symbols: list[str], poll_seconds: float = POLL_SECONDS, fetch=fetch_quote,
                 start_thread: bool = True, wait_first: float = 20.0, clock=time.time):
        self.symbols = list(symbols)
        self.poll_seconds = poll_seconds
        self.last_poll_seconds: float | None = None
        self.fetch = fetch
        self.clock = clock
        self.prices: dict[str, float] = {}
        self.updated: dict[str, float] = {}  # symbol -> when its price was set
        self.pending: dict[str, float] = {}  # symbol -> a spike waiting for a second reading
        self.drift: dict[str, float] = {}
        self.errors = 0
        self.spikes = 0
        self.last_error = ""
        self.polls = 0
        self._lock = threading.Lock()
        self.poll_once()
        if start_thread:
            threading.Thread(target=self._run, name="live-prices", daemon=True).start()
            deadline = time.time() + wait_first
            while len(self.prices) < len(self.symbols) and time.time() < deadline:
                time.sleep(0.5)

    def poll_once(self) -> None:
        started = time.time()
        self._poll_once()
        self.last_poll_seconds = time.time() - started  # the feed watchdog flags a slow feed (trading/watchdog.py)

    def updated_at(self) -> dict[str, float]:
        with self._lock:
            return dict(self.updated)

    def _poll_once(self) -> None:
        for s in self.symbols:
            try:
                quote = self.fetch(s)
                price, when = quote if isinstance(quote, tuple) else (quote, self.clock())
                if price > 0 and price == price and price != float("inf"):
                    with self._lock:
                        self._accept(s, float(price), float(when))
            except Exception as e:  # a failed fetch keeps the last price
                with self._lock:
                    self.errors += 1
                    self.last_error = f"{s}: {type(e).__name__}: {e}"[:200]
        self.polls += 1

    def _accept(self, s: str, price: float, when: float) -> None:
        last = self.prices.get(s)
        if last is not None and abs(price / last - 1) > MAX_JUMP:
            first = self.pending.get(s)
            if first is None or abs(price / first - 1) > CONFIRM_BAND:
                self.pending[s] = price  # hold back until the next reading agrees
                self.spikes += 1
                return
        self.pending.pop(s, None)
        self.prices[s] = price
        self.updated[s] = when

    def stale(self) -> set[str]:
        """Symbols with no fresh price: never priced, feed down, or market closed."""
        now = self.clock()
        with self._lock:
            return {s for s in self.symbols if now - self.updated.get(s, float("-inf")) > STALE_SECONDS}

    def _run(self) -> None:
        while True:
            time.sleep(self.poll_seconds)
            self.poll_once()

    def tick(self) -> dict[str, float]:
        with self._lock:
            return dict(self.prices)

    def status(self) -> dict:
        with self._lock:
            status = {"market": "live", "symbols": len(self.symbols), "priced": len(self.prices),
                      "polls": self.polls, "errors": self.errors, "spikes_held": self.spikes,
                      "last_error": self.last_error}
        status["stale"] = sorted(self.stale())
        return status
