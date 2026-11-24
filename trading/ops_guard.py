"""Operational guard: stale feeds, rollover spreads, constant-risk sizing and headline batch dedup in one place.

mina's drop-in module (24 Nov 2026), fitted to our system:

- `check_feed_and_rollover_health`: one PASS/FAIL for a symbol: (1) its last bar is older than 2x its bar
  interval, (2) the NY-to-Asia rollover blackout 21:55-23:05 UTC (07:55-09:05 AEDT; brokers widen FX, gold and CFD
  spreads there), (3) the Friday close shield 20:30-22:00 UTC (just before Saturday morning in Melbourne) and
  (4) a spread wider than 18% of ATR. (The first draft's rollover test `t >= 21:55 or t <= 23:05` was always
  true and would have blocked every trade; here it is `21:55 <= t <= 23:05`.)
- `rollover_blocked`: the time part alone, used by the desk's pre-order checks (trading/hooks.py `rollover`).
  Crypto has no rollover and US stocks are closed then, so only forex, gold, indices and oil are shielded.
- `compute_volatility_parity_notional`: constant-R sizing. $5k of BTC or TSLA carries 3-4x the daily risk of
  $5k of EUR/USD or gold; sized by the stop distance, a 1R loss costs the same dollars on every market. The
  flat $5k order / $20k position limits stay on top as ceilings (trading/risk.py `RiskManager.risk_cap`).
- `HeadlineBatchDeduplicator`: one batch per 5-minute cycle with each story once, keyed by (symbol, 2-hour
  bucket, top 3 keywords) using the news feed's synonym folding (trading/news_feed.py), so "beats" and
  "smashes" are the same story.

Blocks only ever stop new risk; exits always go through. Paper only: nothing here can place an order.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, time, timezone

ROLLOVER = (time(21, 55), time(23, 5))  # UTC: NY close -> Asia open, spreads blow out
FRIDAY_CLOSE = (time(20, 30), time(22, 0))  # UTC Friday: thin books before the weekend close
SHIELDED = {"forex", "gold", "indices", "oil"}
OIL = {"WTI", "BRENT", "USOIL", "UKOIL", "CL"}
MAX_SPREAD_TO_ATR = 0.18
RISK_PCT = 0.0075  # 0.75% of equity at risk per trade (1R)


def shield_class(symbol: str) -> str:
    from .night_shift import asset_class
    return "oil" if symbol.upper() in OIL else asset_class(symbol)


def rollover_blocked(symbol: str, now_utc: datetime | float | None) -> str | None:
    """The reason new risk on `symbol` is blocked at this time, or None. No clock (the simulator): None."""
    if now_utc is None or shield_class(symbol) not in SHIELDED:
        return None
    dt = datetime.fromtimestamp(now_utc, timezone.utc) if isinstance(now_utc, (int, float)) else now_utc
    t = dt.astimezone(timezone.utc).time()
    if ROLLOVER[0] <= t <= ROLLOVER[1]:
        return f"NY-to-Asia rollover {t:%H:%M} UTC: spreads widen, no new orders until 23:05 UTC"
    if dt.astimezone(timezone.utc).weekday() == 4 and FRIDAY_CLOSE[0] <= t <= FRIDAY_CLOSE[1]:
        return f"Friday close {t:%H:%M} UTC: thin weekend books, no new orders"
    return None


@dataclass
class TickHealth:
    symbol: str
    timeframe: str  # "1h", "4h", "15m", ...
    last_bar_utc: datetime
    bid: float
    ask: float
    atr_price: float


def _bar_seconds(timeframe: str) -> float:
    tf = timeframe.lower().strip()
    unit = {"m": 60, "h": 3600, "d": 86_400}.get(tf[-1:], 3600)
    try:
        return float(tf[:-1] or 1) * unit
    except ValueError:
        return 3600.0


def check_feed_and_rollover_health(health: TickHealth, now_utc: datetime | None = None,
                                   max_spread_to_atr: float = MAX_SPREAD_TO_ATR) -> tuple[bool, str]:
    dt = now_utc or datetime.now(timezone.utc)
    age = (dt - health.last_bar_utc).total_seconds()
    if age > 2 * _bar_seconds(health.timeframe) + 300:  # 2 bars plus 5 minutes of feed slack
        return False, f"stale_feed_block ({health.symbol} last bar {age / 60:.0f}m old)"
    why = rollover_blocked(health.symbol, dt)
    if why:
        return False, f"rollover_spread_blackout_utc ({why})"
    spread = max(0.0, health.ask - health.bid)
    if health.atr_price > 0 and spread / health.atr_price > max_spread_to_atr:
        return False, f"wide_spread_block (spread/atr={spread / health.atr_price:.2%})"
    return True, "PASS"


def compute_volatility_parity_notional(account_equity: float, entry_price: float, stop_price: float,
                                       risk_pct: float = RISK_PCT, arm_heat_mult: float = 1.0,
                                       max_order_cap: float = 5000.0) -> float:
    """Dollars to put on so that hitting the stop loses `risk_pct` of equity (times Orbit's arm heat)."""
    stop_dist_pct = abs(entry_price - stop_price) / max(1e-8, entry_price)
    if stop_dist_pct <= 1e-5:
        return 0.0
    raw = account_equity * risk_pct * arm_heat_mult / stop_dist_pct
    return round(min(max_order_cap, raw), 2)


class HeadlineBatchDeduplicator:
    """Each story once per batch and per 2-hour bucket, before one batched prompt goes to the reading room."""

    def __init__(self, keep: int = 5000) -> None:
        self._seen: dict[str, None] = {}
        self.keep = keep
        self.dropped = 0

    def signature(self, item: dict, now_utc: datetime | None = None) -> str:
        from .news_feed import story_key
        dt = now_utc or datetime.now(timezone.utc)
        sym = str(item.get("symbol") or "MACRO").upper()
        title = str(item.get("title") or item.get("text", "")).split(":", 1)[-1]
        key = story_key(sym, dt.timestamp(), title)
        if key.endswith("|"):  # nothing but filler words: fall back to the wording itself
            key += title.strip().lower()
        return hashlib.sha1(key.encode()).hexdigest()[:12]

    def filter_unique_for_claude(self, items: list[dict], now_utc: datetime | None = None) -> list[dict]:
        out = []
        for item in items:
            sig = self.signature(item, now_utc)
            if sig in self._seen:
                self.dropped += 1
                continue
            self._seen[sig] = None
            if len(self._seen) > self.keep:
                del self._seen[next(iter(self._seen))]
            out.append(item)
        return out
