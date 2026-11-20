"""Simulated cross-asset market for the Whole Matrix desk. Paper mode only.

One tick = one minute. Each pair shares a common factor plus a mean-reverting
spread, so the pairs are genuinely cointegrated (what statistical arbitrage
needs). On top of that:
- a market-wide volatility regime switches between calm and stormy;
- each factor's drift switches between trending and ranging phases;
- every asset has order-book depth (USD within 2% of mid) for slippage.

USDCAD loads negatively on its factor, so the oil pair has a negative hedge
ratio and the stat-arb code must handle signs generally.
"""

import math
import random
from dataclasses import dataclass

# asset: (start price, order-book depth within 2% in USD)
ASSETS = {
    "GOLD": (2400.0, 5_000_000),
    "AUDUSD": (0.66, 20_000_000),
    "NDX": (20_000.0, 10_000_000),
    "BTC": (70_000.0, 3_000_000),
    "WTI": (75.0, 4_000_000),
    "USDCAD": (1.37, 20_000_000),
}
# (asset a, asset b, sign of b's loading on the shared factor)
PAIRS = (("GOLD", "AUDUSD", 1), ("NDX", "BTC", 1), ("WTI", "USDCAD", -1))

CALM_VOL, STORMY_VOL = 0.0006, 0.0018  # per-minute volatility of the factor
VOL_SWITCH = 1 / 3000  # chance per minute that the market-wide vol regime flips
TREND_SWITCH = 1 / 2500  # chance per minute that a factor's trend phase changes
TREND_DRIFT = 0.00012  # per-minute drift while trending
SPREAD_REVERSION = 0.004  # OU mean reversion of each pair's spread, per minute
SPREAD_VOL = 0.0004


@dataclass
class Quote:
    price: float
    depth_usdc: float


class CrossAssetMarket:
    def __init__(self, seed: int = 0):
        self.rng = random.Random(seed + 3)
        self.stormy = False
        self.ticks = 0
        self.factor = {i: 0.0 for i in range(len(PAIRS))}
        self.drift = {i: 0.0 for i in range(len(PAIRS))}
        self.spread = {i: 0.0 for i in range(len(PAIRS))}
        self.noise = {a: 0.0 for a in ASSETS}

    @property
    def vol(self) -> float:
        return STORMY_VOL if self.stormy else CALM_VOL

    def tick(self) -> dict[str, Quote]:
        rng = self.rng
        self.ticks += 1
        if rng.random() < VOL_SWITCH:
            self.stormy = not self.stormy
        quotes = {}
        for i, (a, b, sign) in enumerate(PAIRS):
            if rng.random() < TREND_SWITCH:
                self.drift[i] = rng.choice((0.0, 0.0, TREND_DRIFT, -TREND_DRIFT))
            self.factor[i] += self.drift[i] + rng.gauss(0, self.vol)
            self.spread[i] += -SPREAD_REVERSION * self.spread[i] + rng.gauss(0, SPREAD_VOL * (2 if self.stormy else 1))
            f, s = self.factor[i], self.spread[i]
            for asset, log_move in ((a, f + 0.5 * s), (b, sign * (f - 0.5 * s))):
                # Small idiosyncratic noise so prices are not perfectly collinear.
                self.noise[asset] = 0.9 * self.noise[asset] + rng.gauss(0, self.vol * 0.15)
                start, depth = ASSETS[asset]
                depth_now = depth * (0.6 if self.stormy else 1.0)  # books thin out in stormy markets
                quotes[asset] = Quote(start * math.exp(log_move + self.noise[asset]), depth_now)
        return quotes


# ---- real prices (MARKET=live) ------------------------------------------------------------------------------
# Yahoo Finance tickers for the six assets: gold and oil futures, the Nasdaq-100, Bitcoin and two currency pairs.
YAHOO = {"GOLD": "GC=F", "AUDUSD": "AUDUSD=X", "NDX": "^NDX", "BTC": "BTC-USD", "WTI": "CL=F", "USDCAD": "CAD=X"}


def _yahoo(ticker: str, interval: str, range_: str) -> list[tuple[float, float]]:
    import json
    import urllib.parse
    import urllib.request
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(ticker)}"
           f"?interval={interval}&range={range_}")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (paper-trading price reader)"})
    with urllib.request.urlopen(req, timeout=15) as resp:  # noqa: S310 (fixed https host)
        r = json.loads(resp.read())["chart"]["result"][0]
    closes = r["indicators"]["quote"][0]["close"]
    return [(float(t), float(c)) for t, c in zip(r["timestamp"], closes) if c]


class LiveCrossMarket:
    """Drop-in for CrossAssetMarket with real prices: tick() returns the latest quote of every asset.

    One tick is one minute, as in the simulator. A background thread polls Yahoo every POLL_SECONDS; a failed
    fetch keeps the last price. `history()` gives a few days of real one-minute bars, aligned across assets by
    minute (an asset whose market is closed keeps its last close), for the desk's warm-up. Order-book depth isn't
    in this data, so each asset keeps the simulator's depth figure for sizing.
    """

    POLL_SECONDS = 60.0

    def __init__(self, fetch=_yahoo, start_thread: bool = True):
        import threading
        self.fetch = fetch
        self.prices: dict[str, float] = {}
        self.errors = 0
        self.stormy = False
        self._lock = threading.Lock()
        if start_thread:
            threading.Thread(target=self._run, name="live-xmarket", daemon=True).start()

    def poll_once(self) -> None:
        for asset, ticker in YAHOO.items():
            try:
                bars = self.fetch(ticker, "1m", "1d")
                if bars:
                    with self._lock:
                        self.prices[asset] = bars[-1][1]
            except Exception:  # keep the last price
                self.errors += 1

    def _run(self) -> None:
        import time as _t
        while True:
            self.poll_once()
            _t.sleep(self.POLL_SECONDS)

    def history(self, range_: str = "5d") -> list[dict[str, Quote]]:
        series = {}
        for asset, ticker in YAHOO.items():
            try:
                series[asset] = sorted(self.fetch(ticker, "1m", range_))
            except Exception:
                self.errors += 1
        if len(series) < len(YAHOO) or not all(series.values()):
            return []
        start = max(s[0][0] for s in series.values())
        end = max(s[-1][0] for s in series.values())
        idx = {a: 0 for a in series}
        out, t = [], start - start % 60
        while t <= end:
            row = {}
            for a, s in series.items():
                while idx[a] + 1 < len(s) and s[idx[a] + 1][0] <= t:
                    idx[a] += 1
                row[a] = Quote(s[idx[a]][1], ASSETS[a][1])
            out.append(row)
            t += 60
        with self._lock:
            for a, s in series.items():
                self.prices.setdefault(a, s[-1][1])
        return out

    def tick(self) -> dict[str, Quote]:
        with self._lock:
            return {a: Quote(p, ASSETS[a][1]) for a, p in self.prices.items()}
