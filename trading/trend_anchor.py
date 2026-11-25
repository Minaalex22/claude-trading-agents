"""Trend anchor: the desk trades with the long-term trend, and holds while it lasts.

Why (25 Nov 2026, thread "Make the desk profitable on real prices"): on 2020-2026 real daily prices the desk won 40%
of its trades with a profit factor of 0.32. Two causes stood out:

1. It traded against the big trend about half the time (68 of 125 trades were shorts, in years when gold, shares,
   USDJPY and crypto mostly went up), and those shorts lost most of the money.
2. It held only while the agents' conviction stayed above the 0.72 bar, so trades closed after a day or two:
   the average winner moved +1.2% and the average loser -2.0%. Long moves were never ridden. After 2023 the
   agents' daily conviction rarely reached 0.72, so the desk sat out years of trend.

The anchor is plain time-series momentum (price above or below its 200-day average), one of the most tested
edges there is (Moskowitz, Ooi and Pedersen 2012; decades of managed-futures returns). The rule is fixed from the
textbook, not fitted to our data:

- **Direction**: price above its 200-bar average means up, below means down. Daily bars only (see `bars_for`):
  faster bars would need months of warm-up, so live intraday desks leave it idle until they have the history.
- **Review**: the direction is re-read every bar. Reading it only monthly (Faber 2007) or weekly cuts the
  flip-flops around the average, but it did not hold up: monthly looked best on 2020-22 (PF 2.45) and then lost
  on 2023-26 (PF 0.84), weekly lost on both. Env TREND_ANCHOR_REVIEW=week|month keeps them for testing.
- **Veto**: a desk call against the anchor is dropped (no counter-trend trades).
- **Base**: with the trend, the desk holds at least BASE of its normal size even when the agents are quiet,
  so a trend is held until the average is crossed, not until conviction fades. Agents agreeing above BASE
  still size it up to the full position. Bucket sizing (a symbol's recent desk record) scales only that extra
  part, not the base.
- **Band**: the held size only rebalances when it is more than BAND away from the new target, so day-to-day
  changes in the stop width don't churn fees.

Env TREND_ANCHOR=off turns it off. Paper only; real money stays locked.
"""

from __future__ import annotations

ENABLED = True
LOOKBACK = 200  # bars (trading days) in the average
BASE = 0.75  # minimum share of the normal size held while the trend lasts (0.5 and 1.0 tested on 2020-22)
BAND = 0.25  # rebalance a held anchor position only when off target by more than this share
REVIEW = "day"  # how often the direction is re-read: "day" (default), "week" or "month" (Faber's rule)
EXTRA = 1.0  # how far the agents' conviction above BASE can size the position up (0 = the anchor alone)
DAILY = 20 * 3600  # bars at least this long count as daily


def bars_for(bar_seconds: float | None) -> int | None:
    """The look-back in bars, or None when the anchor doesn't apply (simulator, intraday bars)."""
    if not ENABLED or bar_seconds is None or bar_seconds < DAILY:
        return None
    return LOOKBACK


def direction(prices, bar_seconds: float | None) -> int:
    """+1 up, -1 down, 0 not enough history or anchor not active."""
    n = bars_for(bar_seconds)
    p = list(prices)
    if n is None or len(p) < n + 1:
        return 0
    avg = sum(p[-(n + 1):]) / (n + 1)
    return 1 if p[-1] > avg else -1 if p[-1] < avg else 0


def period(now: float | None) -> str | None:
    """The review period a bar falls in (direction changes only when this changes)."""
    if now is None:
        return None
    import time
    t = time.gmtime(now)
    if REVIEW == "day":
        return time.strftime("%Y-%m-%d", t)
    if REVIEW == "week":
        return time.strftime("%G-%V", t)
    return time.strftime("%Y-%m", t)


def apply(score: float, anchor: int) -> float:
    """The desk's score after the anchor: vetoed against the trend, at least BASE with it."""
    if not anchor:
        return score
    if score * anchor < 0:
        return float(anchor) * BASE
    return float(anchor) * max(abs(score), BASE)


def target(score: float, anchor: int, cap: float, extra_mult: float = 1.0) -> float:
    """Target position value: BASE x cap for the trend, plus the agents' conviction above BASE x extra_mult."""
    return anchor * (BASE + max(0.0, abs(score) - BASE) * extra_mult * EXTRA) * cap


def hold(current_value: float, target_value: float) -> bool:
    """True when an anchor position already on the right side is close enough to its target to leave alone."""
    if not current_value or current_value * target_value <= 0:
        return False
    return abs(target_value - current_value) <= BAND * abs(target_value)
