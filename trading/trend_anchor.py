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

- **Direction**: price above its 200-day average means up, below means down. On daily bars it reads the bars
  themselves. Intraday desks (live 1h/15m) read a separate daily-close history: at boot the engine backfills
  ~250 daily closes from Yahoo (`backfill`), and each new UTC day appends the previous day's last price, so the
  anchor is active from the first bar instead of after 200 days. Env TREND_ANCHOR_INTRADAY=off keeps intraday
  desks without it. Validated on daily bars only; on intraday desks it is new (paper only).
- **Review**: the direction is re-read every bar. Reading it only monthly (Faber 2007) or weekly cuts the
  flip-flops around the average, but it did not hold up: monthly looked best on 2020-22 (PF 2.45) and then lost
  on 2023-26 (PF 0.84), weekly lost on both. Env TREND_ANCHOR_REVIEW=week|month keeps them for testing.
- **Veto**: a desk call against the anchor is dropped (no counter-trend trades).
- **Base**: with the trend, the desk holds at least BASE of its normal size even when the agents are quiet,
  so a trend is held until the average is crossed, not until conviction fades. Agents agreeing above BASE
  still size it up to the full position. Bucket sizing (a symbol's recent desk record) scales only that extra
  part, not the base.
- **Forex** (mina, 25 Nov): forex majors ranged through 2023-26 and chopped every trend rule up. FX_MODE (env
  TREND_ANCHOR_FX): "majors" (default, mina's plan) benches EURUSD/GBPUSD/AUDUSD on daily bars and keeps USDJPY;
  "off" benches all forex; "adx" lets a new forex trend trade open only when ADX(14) >= 25; "keep" trades all.
  Replay, realized PF 2020-22 / 2023-26: keep 2.06 / 1.22, adx 1.95 / 1.08, off 1.55 / 2.62, majors 1.93 / 1.61.
  "majors" beat "off" on 2020-22, so it was the pick between the two benching options; benching at all was
  decided after seeing 2023-26 (forex made money in 2020-22), so the 1.61 is not a fully clean unseen number.
  Later the same day mina asked to test benching USDJPY too (it won 22% of its 2023-26 trades, PF 0.21): with
  RSI(2) on, 2020-22 went PF 2.58 -> 2.49 (USDJPY made +$750 there) and 2023-26 PF 1.72 -> 2.56, win rate 44.6% ->
  53.8%. Default is now "off". Like the majors, this was decided after seeing 2023-26.
- **mina's win-rate ideas tested and not used** (25 Nov, realized 2020-22 / 2023-26, win rate and PF):
  pullback-only entries (close within 1 ATR of the 20-EMA: 37.5% PF 2.82 / 29.1% PF 1.02; RSI(2) <= 15: 40.2% 2.42
  / 31.9% 1.12; either: 36.4% 2.21 / 32.9% 1.29) missed the start of trends and then bought dips that kept
  falling. A 25% trim at +2R with a 2.75x ATR trail: 34.7% 1.01 / 37.7% 0.90 (the trail shook out of every trend).
  The trim alone: 49.5% 2.41 / 48.9% 1.47 (more wins, but payoff fell to 1.54, under the 1.85 unlock bar).
  Agents' extra confidence off (EXTRA 0): 46.4% 2.58 / 45.3% 1.39; halved: 46.4% 2.59 / 43.2% 1.74. Baseline:
  46.4% 2.58 / 44.6% 1.72. Knobs stay in for testing, all off: TREND_ANCHOR_PULLBACK, _PB_ATR, _TRIM_R, _TRIM,
  _TRAIL_ATR.
- **Exits tested and not used**: a two-stage exit (2x ATR stop, 30% off at +1.5R, 2.5-2.75x ATR chandelier,
  close under the 50/20 EMA) did worse than holding to the 200-day cross on gold and shares in both periods
  (monthly PF 1.20 vs 1.26 in 2020-22, 0.92 vs 1.32 in 2023-26).
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
FX_MODE = "off"  # forex: "off" (default since 25 Nov: all forex benched, USDJPY too), "majors" (EURUSD/GBPUSD/
# AUDUSD benched, USDJPY kept), "adx" (new trend entries need ADX >= FX_ADX) or "keep"
FX_KEEP = {"USDJPY"}  # forex pairs "majors" mode still trades
FX_ADX, ADX_N = 26.0, 14  # mina: ADX >= 26 before forex majors may trade again ("adx" mode)
INTRADAY = True  # intraday desks use the daily-close history (backfilled at boot)
BACKFILL_DAYS = "1y"  # Yahoo range for the boot backfill (about 250 trading days)
DAILY = 20 * 3600  # bars at least this long count as daily
# mina's win-rate ideas (25 Nov 2026), test knobs; defaults are off unless the replay showed they help.
PULLBACK = "off"  # new anchor exposure only on a pullback: "ema" (close within PB_ATR x ATR of the 20-EMA),
# "rsi2" (RSI(2) <= 15 long / >= 85 short), "either", or "off"
PB_ATR = 1.0
TRIM_R, TRIM = 0.0, 0.25  # trim TRIM of the anchor hold once price is TRIM_R x R in profit (0 = off)
TRAIL_ATR = 0.0  # chandelier trail on the anchor hold: exit when close is TRAIL_ATR x ATR off the best close (0 = off)


def ema(prices, n: int = 20) -> float:
    p = list(prices)
    k = 2 / (n + 1)
    e = p[0]
    for x in p[1:]:
        e += k * (x - e)
    return e


def atr(prices, n: int = 14) -> float:
    """Close-only ATR (our free daily history has no highs and lows): the mean absolute close move."""
    p = list(prices)[-(n + 1):]
    moves = [abs(b - a) for a, b in zip(p, p[1:])]
    return sum(moves) / len(moves) if moves else 0.0


def rsi2(prices) -> float:
    p = list(prices)[-3:]
    if len(p) < 3:
        return 50.0
    up = sum(max(b - a, 0) for a, b in zip(p, p[1:]))
    down = sum(max(a - b, 0) for a, b in zip(p, p[1:]))
    return 100.0 if not down else 100 - 100 / (1 + up / down)


def pulled_back(anchor: int, prices) -> bool:
    """True when a new trend entry is allowed under PULLBACK (always True when off)."""
    if PULLBACK == "off" or not anchor:
        return True
    p = list(prices)
    if len(p) < 21:
        return True
    near = anchor * (p[-1] - ema(p[-60:])) <= PB_ATR * atr(p)
    r = rsi2(p)
    dip = r <= 15 if anchor > 0 else r >= 85
    return {"ema": near, "rsi2": dip, "either": near or dip}.get(PULLBACK, True)


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


def backfill(symbols, get=None) -> dict[str, list[float]]:
    """Daily closes for each symbol from Yahoo (oldest first). Symbols that fail are left out: their anchor
    then warms up on its own."""
    from . import backtest
    out = {}
    for s in symbols:
        try:
            bars = backtest.fetch_history([s], "1d", BACKFILL_DAYS, get=get).get(s, [])
        except Exception:  # noqa: BLE001 - no network or no such ticker: the desk runs without it
            continue
        if bars:
            out[s] = [c for _, c in bars]
    return out


def is_fx(symbol: str) -> bool:
    from .markets.catalog import BY_SYMBOL
    p = BY_SYMBOL.get(symbol.upper())
    return p is not None and p.kind == "forex"


def adx(prices, n: int = ADX_N) -> float:
    """Wilder's ADX (0-100) from closes only: our free daily history has no highs and lows, so the
    directional moves are the up and down closes and the true range is the absolute close move."""
    p = list(prices)
    pdm = mdm = tr = 0.0
    value = None
    for i in range(1, len(p)):
        up, down = max(p[i] - p[i - 1], 0.0), max(p[i - 1] - p[i], 0.0)
        pdm += up - pdm / n
        mdm += down - mdm / n
        tr += abs(p[i] - p[i - 1]) - tr / n
        if i > n and tr > 0:
            dx = abs(pdm - mdm) / (pdm + mdm) if pdm + mdm else 0.0
            value = dx if value is None else value + (dx - value) / n
    return 100.0 * (value or 0.0)


def fx_gate(symbol: str, anchor: int, position: float, prices) -> int:
    """The anchor after the forex rule. Forex majors ranged through 2023-26 and every trend rule lost there."""
    if not anchor or FX_MODE == "keep" or not is_fx(symbol):
        return anchor
    if FX_MODE == "off" or (FX_MODE == "majors" and symbol.upper() not in FX_KEEP):
        return 0
    if FX_MODE == "majors":
        return anchor
    if position * anchor > 0:
        return anchor  # already riding the trend: keep holding it
    return anchor if adx(prices) >= FX_ADX else 0


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


def period_day(now: float) -> str:
    import time
    return time.strftime("%Y-%m-%d", time.gmtime(now))


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
