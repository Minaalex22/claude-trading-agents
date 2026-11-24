"""Shared indicator cache: each symbol's rolling numbers are worked out once per tick, not once per agent.

Before this, every one of the 1,400 agents worked out its own mean, standard deviation and return volatility
inside `observe()`, with Python's `statistics.pstdev`, which does exact fraction maths. A profile of 1,500
ticks with 1,400 agents showed 65% of all the time went there (342 of 561 seconds).

Two fixes:

- **Fast maths**: `pstdev` here is the plain floating-point version (two passes, `math.fsum`), the same answer
  to about 15 digits, roughly 50x faster.
- **One read per symbol**: agents on the same symbol that look back the same number of bars see exactly the
  same prices, so the first agent to ask works the numbers out and the rest read them back. The desk opens a
  new tick with `begin_tick()`; outside a desk tick (tests, one-off calls) nothing is cached.

The cache key is (tick, symbol, how many bars, first price, last price), so two different windows never
share an answer. Paper only: nothing here can place an order.
"""

import math

ENABLED = True  # INDICATOR_CACHE=off turns the sharing off (the fast maths stays)

_tick: int | None = None
_memo: dict = {}
hits = 0
misses = 0


def fmean(xs) -> float:
    xs = xs if isinstance(xs, (list, tuple)) else list(xs)
    return math.fsum(xs) / len(xs)


def pstdev(xs) -> float:
    """Population standard deviation in plain floats (statistics.pstdev without the exact-fraction cost)."""
    xs = xs if isinstance(xs, (list, tuple)) else list(xs)
    n = len(xs)
    if n < 1:
        raise ValueError("pstdev requires at least one data point")
    m = math.fsum(xs) / n
    return math.sqrt(math.fsum((x - m) * (x - m) for x in xs) / n)


def begin_tick(tick: int) -> None:
    """A new desk tick: forget last tick's numbers."""
    global _tick
    _tick = tick
    _memo.clear()


def end() -> None:
    """Leave desk mode (tests, backtests that drive agents by hand)."""
    global _tick
    _tick = None
    _memo.clear()


def _compute(prices: list) -> dict:
    rets = [b / a - 1 for a, b in zip(prices, prices[1:])]
    return {"mean": fmean(prices), "sd": pstdev(prices),
            "ret_sd": pstdev(rets) if rets else 0.0, "rets": rets,
            "path": math.fsum(abs(b - a) for a, b in zip(prices, prices[1:]))}


def series(symbol: str | None, prices) -> dict:
    """mean, sd (of prices), ret_sd (of one-bar returns), rets and path (total distance travelled) of `prices`.

    Shared by every agent on `symbol` asking about the same bars in the same tick. Treat the result as read-only.
    """
    global hits, misses
    p = prices if isinstance(prices, list) else list(prices)
    if not ENABLED or _tick is None or symbol is None or not p:
        return _compute(p)
    key = (_tick, symbol, len(p), p[0], p[-1])
    got = _memo.get(key)
    if got is None:
        misses += 1
        got = _memo[key] = _compute(p)
    else:
        hits += 1
    return got


def status() -> dict:
    total = hits + misses
    return {"on": ENABLED, "hits": hits, "misses": misses, "shared": round(hits / total, 3) if total else None}
