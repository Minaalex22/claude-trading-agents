"""Cross-asset checks for the desk: sister-pair SMT and the parent-timeframe lock (mina's batch 5).

**SMT (sister pairs)**: correlated markets are checked together. "At an extreme" means the last price is a
new `EXTREME_BARS`-bar low (for a buy) or high (for a sell).
- A Reversion buy wants *divergence*: our market swept a low while its sister refused to. If both are making
  new lows together it is a falling knife: blocked. (Sells mirror this at highs.)
- A Trend entry wants *confirmation*: our market broke out and its sister broke the same way. A breakout
  the sister doesn't confirm is a likely fake-out: blocked.
Markets without a sister, or whose sister isn't trading in this run, pass.

**Parent lock**: the parent timeframe is `PARENT_X` bars of ours (15m -> 1h, 1h -> 4h).
- Reversion only buys a dip when the parent is flat or up (price above the parent's EMA-50, or the parent
  is trendless: efficiency ratio below `PARENT_FLAT_ER`, our stand-in for ADX < 22); sells mirror it.
- Trend only enters while the parent's volatility is expanding: the parent's recent range (last 5 parent bars) is more
  than `PARENT_EXPAND` x its 20-bar average.
Daily bars have no parent here, so the lock is off on daily data.

Switches: SMT_FILTER, PARENT_LOCK (env, on by default). Paper only.
"""

from __future__ import annotations

SMT_FILTER = True
PARENT_LOCK = True
EXTREME_BARS = 20
PARENT_X = 4
PARENT_EMA = 50
PARENT_FLAT_ER = 0.22
PARENT_EXPAND = 1.1
PARENT_ATR_BARS = 20

SISTERS = {
    "EURUSD": "GBPUSD", "GBPUSD": "EURUSD", "EUR": "GBP", "GBP": "EUR", "AUD": "EUR", "AUDUSD": "EURUSD",
    "SPY": "QQQ", "QQQ": "SPY", "BTC": "ETH", "ETH": "BTC", "SOL": "ETH",
    "PAXG": "EUR", "XAUUSD": "EURUSD", "AAPL": "MSFT", "MSFT": "AAPL", "NVDA": "AMD", "AMD": "NVDA",
    "META": "AMZN", "AMZN": "META", "TSLA": "NVDA",
}


def at_extreme(prices: list[float], side: float) -> bool:
    """A new EXTREME_BARS low (side > 0, where a buyer looks) or high (side < 0)."""
    if len(prices) <= EXTREME_BARS:
        return False
    window = prices[-EXTREME_BARS - 1:-1]
    return prices[-1] < min(window) if side > 0 else prices[-1] > max(window)


def broke_out(prices: list[float], side: float) -> bool:
    """A new EXTREME_BARS high for a buy, low for a sell (where a trend entry fires)."""
    return at_extreme(prices, -side)


def smt(symbol: str, style: str, side: float, hist: dict) -> str | None:
    """Why SMT blocks this entry, or None."""
    if not SMT_FILTER:
        return None
    sister = SISTERS.get(symbol)
    mine, theirs = list(hist.get(symbol, ())), list(hist.get(sister, ())) if sister else []
    if not sister or len(theirs) <= EXTREME_BARS:
        return None
    if style == "reversion":
        if at_extreme(mine, side) and at_extreme(theirs, side):
            return f"blocked: {symbol} and {sister} both at new {'lows' if side > 0 else 'highs'} (no SMT divergence)"
        return None
    if broke_out(mine, side) and not broke_out(theirs, side):
        return f"blocked: {sister} didn't confirm the {symbol} breakout (SMT fake-out)"
    return None


def parent_bars(prices: list[float]) -> list[float]:
    """Every PARENT_X-th close, ending on the latest."""
    return prices[::-1][::PARENT_X][::-1]


def _ema(xs: list[float], n: int) -> float:
    k, e = 2 / (n + 1), xs[0]
    for x in xs[1:]:
        e += k * (x - e)
    return e


def parent(symbol: str, style: str, side: float, prices: list[float], bar_seconds: float | None) -> str | None:
    """Why the parent timeframe blocks this entry, or None."""
    if not PARENT_LOCK or bar_seconds is None or bar_seconds >= 86_400:
        return None
    p = parent_bars(prices)
    if style == "reversion":
        if len(p) < PARENT_EMA:
            return None
        ema = _ema(p[-PARENT_EMA * 2:], PARENT_EMA)
        seg = p[-14:]
        path = sum(abs(b - a) for a, b in zip(seg, seg[1:])) or 1e-12
        er = abs(seg[-1] - seg[0]) / path
        against = (p[-1] < ema) if side > 0 else (p[-1] > ema)
        if against and er >= PARENT_FLAT_ER:
            return f"blocked: parent timeframe trending {'down' if side > 0 else 'up'} (no catching a falling knife)"
        return None
    if len(p) < PARENT_ATR_BARS + 2:
        return None
    ranges = [abs(b - a) for a, b in zip(p, p[1:])]
    if len(ranges) < PARENT_ATR_BARS + 5:
        return None
    avg = sum(ranges[-PARENT_ATR_BARS - 5:-5]) / PARENT_ATR_BARS
    now = sum(ranges[-5:]) / 5  # the parent's recent ATR against its 20-bar average
    if avg and now <= PARENT_EXPAND * avg:
        return "blocked: parent timeframe volatility isn't expanding (late or fake breakout)"
    return None
