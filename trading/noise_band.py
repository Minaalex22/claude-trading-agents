"""Noise band: intraday momentum that trades only when price breaks out of its usual daily "noise".

Source: Zarattini, Aziz and Barbon, "Beat the Market: An Effective Intraday Momentum Strategy for SPY" (2024).
mina asked for it by name on 25 Nov 2026 ("add strategy called noise band").

The idea: every day price wanders some distance from the open just by noise. How far it usually wanders depends on
the time of day (a little after 10 minutes, a lot more by 3 pm). So for each time of day we keep the average
absolute move from the open over the last LOOKBACK (14) sessions. That average, around the open, is the noise band:

    upper = max(open, previous close) x (1 + sigma(t))
    lower = min(open, previous close) x (1 - sigma(t))

- **Entry** (checked every CHECK_MINUTES, 30, after the open): price above the upper band = buy, below the lower
  band = sell. A break of the usual noise means real demand or supply has arrived and tends to carry on.
- **Stop**: a long closes when price falls back below max(upper band, session average price); a short when it rises
  above min(lower band, session average price). The session average is a time-weighted VWAP stand-in (the desk has
  no volume feed).
- **Flat by the close**: every position closes at the session's last check. No overnight risk.
- **Size**: volatility targeting. Leverage = TARGET_VOL (2% a day) / last 14 daily moves' volatility, capped at
  LEV_CAP (4, as in the paper). The desk turns that into a share of its position cap (`size`).

Sessions per market (`SESSIONS`), fixed before any test, not fitted:
  US shares and indices: New York 09:30 to 16:00 (the paper's own session)
  gold and forex: New York 03:00 (London 08:00 open) to 16:30 (before the 17:00 roll, when spreads blow out)
  crypto: the UTC day, 00:00 to 24:00

RESULTS (25 Nov 2026, tools/noise_band_backtest.py, real 30-minute bars, after costs):
  Strategy alone, paper rules (band 1.0), broker costs: loses everywhere. SPX PF 0.91, gold 0.85, EURUSD 0.84
  (2025-26), Bitcoin 0.77 (2022-26, 4,783 trades, 20% wins: a 24h session whipsaws all day).
  Wider band (2.0): SPX PF 1.36 (80 trades), gold 1.11, EURUSD 0.91, Bitcoin 0.93.
  Whole paper desk, SPX + gold + EURUSD, Jan 2025 - May 2026, 100 agents:
    before (no noise band)      60 trades, 26.7% wins, PF 1.10, +$44
    noise band on gold          162 trades, 43.8% wins, PF 1.74, +$1,834   <- default
    noise band on SPX           103 trades, 40.8% wins, PF 1.50, +$439
    noise band on SPX + gold    193 trades, 43.5% wins, PF 0.91, -$415
    paper band 1.0, SPX + gold  145 trades, 31.0% wins, PF 0.29, -$2,124
  Caveats: only 16 months of intraday data; the 2.0 band and the gold-only default were picked after seeing these
  numbers, so treat them as a hypothesis the live paper desk must confirm, not proof.

Paper/demo only: real money stays behind the live unlock gate like everything else. Env NOISE_BAND=off turns it
off (default on, mina's rule: leave strategies switched on and report how they do); NOISE_BAND_MARKETS picks the
markets (default "gold"; "all" or e.g. "indices,gold"), NOISE_BAND_MULT the band width (default 2.0). On the desk it only runs on
intraday bars (under a day); daily-bar desks never see it.
"""

from __future__ import annotations

import math
import statistics
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .night_shift import asset_class
from .sessions import _ny

ENABLED = True
LOOKBACK = 14  # sessions in the noise average and the volatility estimate (paper: 14)
MIN_SESSIONS = 10  # need at least this many past sessions at a time of day before trading it
CHECK_MINUTES = 30  # entries and stops are checked every 30 minutes after the open (paper: HH:00 / HH:30)
DEFAULT_MULT = 2.0  # see the backtest note below: the paper's 1.0 lost on every market we tested
BAND_MULT = DEFAULT_MULT  # sigma multiplier (paper: 1.0; env NOISE_BAND_MULT)
TARGET_VOL = 0.02  # daily volatility target for sizing (paper: 2%)
LEV_CAP = 4.0  # most leverage the sizing may ask for (paper: 4)
DEFAULT_MARKETS = "gold"  # the only market where it helped the whole desk (see RESULTS below)
MARKETS = DEFAULT_MARKETS  # env NOISE_BAND_MARKETS: "all" or a comma list of indices,stocks,gold,forex,crypto

# market -> (clock, open minute, close minute) in that clock's wall time. "ny" = New York with its daylight saving
# (trading/sessions.py, no tz database needed: Windows has none without the tzdata package).
SESSIONS = {
    "indices": ("ny", 9 * 60 + 30, 16 * 60),
    "stocks": ("ny", 9 * 60 + 30, 16 * 60),
    "gold": ("ny", 3 * 60, 16 * 60 + 30),
    "forex": ("ny", 3 * 60, 16 * 60 + 30),
    "crypto": ("utc", 0, 24 * 60),
}


def _local(zone: str, ts: float) -> datetime:
    return _ny(ts) if zone == "ny" else datetime.fromtimestamp(ts, timezone.utc)


INDEX_SYMBOLS = {"SPX", "SPY", "US500", "QQQ", "NDX", "NAS100", "DIA", "US30", "IWM"}


def market_of(symbol: str) -> str:
    return "indices" if symbol.upper() in INDEX_SYMBOLS else asset_class(symbol)


def allowed(symbol: str) -> bool:
    if not ENABLED:
        return False
    if MARKETS in ("", "all"):
        return True
    return market_of(symbol) in {m.strip() for m in MARKETS.split(",")}


def session_clock(symbol: str, ts: float) -> tuple[str, int, int] | None:
    """(session day, minutes since the open, session length) for a bar ending at `ts`, or None outside the session.

    A bar ending exactly at the open belongs to no session (it is the last pre-open price)."""
    zone, start, end = SESSIONS[market_of(symbol)]
    local = _local(zone, ts)
    if local.weekday() >= 5 and market_of(symbol) != "crypto":
        return None
    minute = local.hour * 60 + local.minute + local.second / 60
    if end == 24 * 60 and minute == 0:  # a 24h session: midnight closes yesterday's session
        prev = _local(zone, ts - 1)
        return prev.strftime("%Y-%m-%d"), end - start, end - start
    if not start < minute <= end:
        return None
    return local.strftime("%Y-%m-%d"), int(round(minute - start)), end - start


@dataclass
class NoiseBand:
    """One symbol's noise band. Feed it every bar with `update(ts, price)`; it returns the wanted direction."""

    symbol: str
    moves: dict[int, deque] = field(default_factory=dict)  # minutes since open -> past sessions' |move| from open
    day: str | None = None
    open: float | None = None
    prev_close: float | None = None
    last_price: float | None = None
    twap_sum: float = 0.0
    twap_n: int = 0
    today: dict[int, float] = field(default_factory=dict)
    closes: deque = field(default_factory=lambda: deque(maxlen=LOOKBACK + 1))  # session closes, for sizing
    position: int = 0  # -1, 0, +1
    last_pre_open: float | None = None
    upper: float | None = None
    lower: float | None = None
    sessions_seen: int = 0

    def _roll(self) -> None:
        """Close the session book: today's moves join the noise history, its last price is the close."""
        if self.day is None:
            return
        for k, v in self.today.items():
            self.moves.setdefault(k, deque(maxlen=LOOKBACK)).append(v)
        if self.last_price is not None:
            self.prev_close = self.last_price
            self.closes.append(self.last_price)
        self.sessions_seen += 1
        self.day, self.open, self.today, self.twap_sum, self.twap_n = None, None, {}, 0.0, 0
        self.position, self.upper, self.lower = 0, None, None

    def sigma(self, minute: int) -> float | None:
        past = self.moves.get(minute)
        if not past or len(past) < MIN_SESSIONS:
            return None
        return sum(past) / len(past)

    def leverage(self) -> float:
        """Volatility-targeted leverage from the last LOOKBACK session-to-session moves (paper: 2% / vol, cap 4)."""
        c = list(self.closes)
        rets = [b / a - 1 for a, b in zip(c, c[1:]) if a]
        if len(rets) < 5:
            return 1.0
        vol = statistics.pstdev(rets)
        return LEV_CAP if vol <= 0 else min(LEV_CAP, TARGET_VOL / vol)

    def size(self) -> float:
        """Share of the desk's position cap the noise band wants (0..1), from the volatility-targeted leverage."""
        return min(1.0, self.leverage() / LEV_CAP)

    def update(self, ts: float, price: float) -> int | None:
        """Feed one bar (its end time, its close). Returns +1 / -1 / 0 inside the session, None outside it or
        while the band is still warming up (then the desk ignores the noise band for this bar)."""
        clock = session_clock(self.symbol, ts)
        if clock is None:
            if self.day is not None:
                self._roll()  # first bar after the close: the session is over
            self.last_pre_open = price
            return None
        day, minute, length = clock
        if day != self.day:
            if self.day is not None:
                self._roll()
            self.day = day
            # The open: the last price before the session started (or this bar's price if there was none).
            self.open = self.last_pre_open if self.last_pre_open is not None else price
            if self.prev_close is None:
                self.prev_close = self.open
        self.last_price = price
        self.last_pre_open = None
        self.twap_sum += price
        self.twap_n += 1
        move = abs(price / self.open - 1) if self.open else 0.0
        bucket = minute - minute % CHECK_MINUTES
        self.today[bucket] = move
        if minute >= length:
            self.position = 0  # flat by the close
            self._roll()
            self.last_pre_open = price  # 24h sessions: this close is also the next session's open
            return 0
        if minute % CHECK_MINUTES:
            return self.position if self.upper is not None else None  # between checks: keep what we have
        s = self.sigma(bucket)
        if s is None:
            return None
        self.upper = max(self.open, self.prev_close) * (1 + BAND_MULT * s)
        self.lower = min(self.open, self.prev_close) * (1 - BAND_MULT * s)
        twap = self.twap_sum / self.twap_n
        if self.position > 0 and price < max(self.upper, twap):
            self.position = 0
        elif self.position < 0 and price > min(self.lower, twap):
            self.position = 0
        if self.position == 0:
            if price > max(self.upper, twap):
                self.position = 1
            elif price < min(self.lower, twap):
                self.position = -1
        return self.position

    def snapshot(self) -> dict:
        return {"symbol": self.symbol, "market": market_of(self.symbol), "position": self.position,
                "upper": round(self.upper, 6) if self.upper else None,
                "lower": round(self.lower, 6) if self.lower else None,
                "open": self.open, "size": round(self.size(), 3), "sessions": self.sessions_seen,
                "ready": self.sessions_seen >= MIN_SESSIONS}


class Book:
    """The desk's noise bands, one per symbol, made on first use."""

    def __init__(self):
        self.bands: dict[str, NoiseBand] = {}

    def update(self, symbol: str, ts: float, price: float) -> int | None:
        if not allowed(symbol):
            return None
        band = self.bands.get(symbol)
        if band is None:
            band = self.bands[symbol] = NoiseBand(symbol)
        return band.update(ts, price)

    def size(self, symbol: str) -> float:
        band = self.bands.get(symbol)
        return band.size() if band else 0.0

    def snapshot(self) -> dict:
        return {s: b.snapshot() for s, b in self.bands.items()}


# ---------------------------------------------------------------------------------------------------- replay
def replay(bars: list[tuple[float, float]], symbol: str, cost_bps: float = 3.0, sized: bool = True,
           start_cash: float = 100_000.0) -> dict:
    """Trade one symbol's bars (bar end time, close) with the noise band alone, filling at each bar's close.

    `cost_bps` is charged on every fill (spread + fees + slippage). Returns the closed trades and their stats."""
    band = NoiseBand(symbol)
    cash, qty, entry, side, lev_at = start_cash, 0.0, 0.0, 0, 1.0
    trades, equity_curve = [], []
    peak, max_dd = start_cash, 0.0
    for ts, px in bars:
        want = band.update(ts, px)
        want = 0 if want is None else want
        if side and want != side:
            gross = qty * (px - entry)
            cost = abs(qty) * (entry + px) * cost_bps / 10_000
            cash += gross - cost
            trades.append({"t": int(ts), "side": "l" if side > 0 else "s", "pnl": round(gross - cost, 2),
                           "ret": round((px / entry - 1) * side - 2 * cost_bps / 10_000, 6),
                           "lev": round(lev_at, 2)})
            qty, side = 0.0, 0
        if want and not side:
            lev_at = band.leverage() if sized else 1.0
            qty = want * cash * lev_at / px
            entry, side = px, want
        eq = cash + (qty * (px - entry) if side else 0.0)
        peak = max(peak, eq)
        max_dd = max(max_dd, 1 - eq / peak if peak else 0.0)
        equity_curve.append(eq)
    return {"symbol": symbol, "trades": trades, "end_equity": round(cash, 2), "max_dd": round(max_dd, 4),
            **stats(trades)}


def stats(trades: list[dict]) -> dict:
    wins = [t["pnl"] for t in trades if t["pnl"] > 0]
    losses = [-t["pnl"] for t in trades if t["pnl"] <= 0]
    n = len(trades)
    pf = (sum(wins) / sum(losses)) if losses and sum(losses) else (math.inf if wins else 0.0)
    payoff = ((sum(wins) / len(wins)) / (sum(losses) / len(losses))) if wins and losses and sum(losses) else None
    return {"n": n, "win_rate": round(len(wins) / n, 4) if n else 0.0,
            "profit_factor": round(pf, 3) if pf != math.inf else None,
            "payoff": round(payoff, 3) if payoff else None, "net": round(sum(t["pnl"] for t in trades), 2)}
