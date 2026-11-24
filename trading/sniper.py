"""Sniper mode (mina's batch 6): fewer, A+ desk entries, banked at the mean.

With SNIPER on (env, OFF by default since it made zero trades in testing) the desk opens new risk only when ALL of these hold:
1. **Conviction**: |desk score| >= `BAR` (0.82). An open position is held down to the normal 0.5 bar.
2. **Timeframe and hours**: 1h to 4h bars (`MIN_BAR`..`MAX_BAR` seconds), inside the London/New York overlap
   (`HOURS` UTC). 5m, 15m and daily bars open nothing in this mode.
3. **Three pillars**, Reversion-led calls only:
   - *Reversion extreme + sweep and reclaim*: within the last `SWEEP_BARS` bars price closed beyond the prior
     `SWEEP_LOOKBACK`-bar low (for a buy; high for a sell), and the latest close is back inside it (no buying
     while it's still falling).
   - *SMT divergence*: the sister market did not sweep its own low in the same bars (smt_filter.SISTERS);
     markets without a sister pass this pillar.
   - *News not against it*: the reading room's time-faded news doesn't oppose the trade.
Reversion agents use the mean-snap exit (trading/exits.py): stop 1.35R, bank 80% at +0.9R, the last 20% with
its stop at +0.10R and a 2.0R target. The daily loss cap (2%) and the macro-shock blocks stay on. Paper only.
"""

from __future__ import annotations

from . import smt_filter

SNIPER = False
BAR = 0.82
MIN_BAR, MAX_BAR = 3_600, 14_400
HOURS = (12, 17)
SWEEP_BARS, SWEEP_LOOKBACK = 3, 20
# mean-snap exit for Reversion agents
STOP_R, FIRST_TP_R, FIRST_TP_SHARE, RUNNER_STOP_R, RUNNER_TP_R = 1.35, 0.9, 0.8, 0.10, 2.0


def bar() -> float:
    """Sniper's opening bar: 0.82, or the desk's own threshold if that is set higher (one setting to raise)."""
    from . import desk
    return max(BAR, desk.SIGNAL_THRESHOLD)


def active(bar_seconds) -> bool:
    """Sniper mode needs real bars with a known length (backtests, live): the simulator has no clock."""
    return SNIPER and bar_seconds is not None


def swept_and_reclaimed(prices: list[float], side: float) -> bool:
    """Price closed beyond the prior SWEEP_LOOKBACK-bar extreme within the last SWEEP_BARS bars, and the
    latest close is back inside that extreme."""
    need = SWEEP_LOOKBACK + SWEEP_BARS + 1
    if len(prices) < need:
        return False
    ref = prices[-need:-SWEEP_BARS - 1]
    level = min(ref) if side > 0 else max(ref)
    recent = prices[-SWEEP_BARS - 1:-1]
    swept = any(p < level for p in recent) if side > 0 else any(p > level for p in recent)
    back = prices[-1] > level if side > 0 else prices[-1] < level
    return swept and back


def gate(symbol: str, style: str, side: float, score: float, hist: dict, view, bar_seconds, hour) -> str | None:
    """Why sniper mode doesn't take this new entry, or None."""
    if not active(bar_seconds):
        return None
    if abs(score) < bar():
        return f"sniper: score {abs(score):.2f} under {bar()}"
    if not MIN_BAR <= bar_seconds <= MAX_BAR:
        return "sniper: 1h-4h bars only"
    if hour is None or not HOURS[0] <= hour < HOURS[1]:
        return f"sniper: only {HOURS[0]}:00-{HOURS[1]}:00 UTC"
    if style != "reversion":
        return "sniper: reversion-led calls only"
    mine = list(hist.get(symbol, ()))
    if not swept_and_reclaimed(mine, side):
        return "sniper: no sweep and reclaim yet"
    sister = smt_filter.SISTERS.get(symbol)
    theirs = list(hist.get(sister, ())) if sister else []
    if theirs and swept_and_reclaimed(theirs, side) or (theirs and smt_filter.at_extreme(theirs, side)):
        return f"sniper: {sister} swept too (no SMT divergence)"
    if view is not None and getattr(view, "news", 0.0) * side < 0:
        return "sniper: news against it"
    return None
