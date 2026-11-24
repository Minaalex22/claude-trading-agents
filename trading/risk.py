"""Hard risk limits. Agents can only propose; these rules have the final say.

Three stops sit above the per-order limits:
- the drawdown halt: equity falls `max_drawdown` below its peak, and only exits are allowed from then on;
- the daily loss stop: equity falls `max_daily_loss` below where the (UTC) day started, and only exits are
  allowed until the next day;
- the kill switch: a file named KILL in the events folder (or `kill_path`). While it exists the desk closes
  every position and opens nothing. Delete the file to trade again.
Symbols whose price feed is stale (`frozen`) can be exited but not added to.

Night Shift (trading/night_shift.py), when the engine gives it one: overnight in Melbourne no position may grow past
15% of the night's capsule, toxic setups are skipped, and if the capsule's stop trips the desk is EXIT_ONLY until 07:00.
"""

import time
from dataclasses import dataclass
from pathlib import Path

from .broker import PaperBroker


# Macro buckets (env BUCKET_CAP): markets that move as one bet. At most BUCKET_MAX of a bucket may hold a
# position at once, so a dollar spike or a Fed day can't stop out a dozen "independent" trades together.
BUCKET_CAP = True
BUCKET_MAX = 2
BUCKETS = {
    "usd_bloc": {"EUR", "GBP", "AUD", "JPY", "NZD", "CAD", "CHF", "EURUSD", "GBPUSD", "AUDUSD", "USDJPY", "NZDUSD",
                 "USDCAD", "USDCHF", "PAXG", "XAUUSD", "GOLD"},
    "us_tech_beta": {"SPY", "QQQ", "AAPL", "MSFT", "NVDA", "AMD", "META", "AMZN", "TSLA", "GOOGL", "GOOG", "NFLX"},
    "crypto_beta": {"BTC", "ETH", "SOL", "BTC-USD", "ETH-USD", "SOL-USD"},
}
BUCKET_OF = {sym: name for name, syms in BUCKETS.items() for sym in syms}


@dataclass
class RiskLimits:
    max_position_value: float = 20_000.0  # per symbol, absolute
    max_order_value: float = 5_000.0
    max_gross_exposure: float = 80_000.0
    max_drawdown: float = 0.10  # fraction of starting equity; breach halts trading
    max_daily_loss: float = 0.02  # fraction of the day's starting equity; breach stops new risk until tomorrow


class RiskManager:
    def __init__(self, limits: RiskLimits, broker: PaperBroker, kill_path: Path | None = None, clock=time.time,
                 night_shift=None):
        self.limits = limits
        self.broker = broker
        self.peak_equity = broker.starting_cash
        self.halted = False
        self.kill_path = kill_path
        self.killed = False
        self.clock = clock
        self.day: str | None = None
        self.day_start_equity = broker.starting_cash
        self.day_stopped = False
        self.frozen: set[str] = set()  # symbols with a stale price feed: exits only
        self.night_shift = night_shift  # trading/night_shift.NightShiftEngine, or None

    @property
    def night_active(self) -> bool:
        return self.night_shift is not None and self.night_shift.active

    @property
    def mode(self) -> str:
        """EXIT_ONLY while any stop is on (positions can close, nothing new opens), else NORMAL."""
        return "EXIT_ONLY" if self.stop_reason else "NORMAL"

    @property
    def stop_reason(self) -> str | None:
        if self.killed:
            return "kill switch on"
        if self.halted:
            return "halted: max drawdown breached"
        if self.day_stopped:
            return "daily loss limit hit"
        if self.night_active and self.night_shift.halted:
            return f"night shift stop: {self.night_shift.halt_reason}, exits only until 07:00"
        return None

    def update(self, prices: dict[str, float], now: float | None = None) -> None:
        if self.night_shift is not None:
            self.night_shift.update(now, [s for s, q in self.broker.positions.items() if q])
        equity = self.broker.equity(prices)
        self.peak_equity = max(self.peak_equity, equity)
        if not self.halted and equity < self.peak_equity * (1 - self.limits.max_drawdown):
            self.halted = True
        day = time.strftime("%Y-%m-%d", time.gmtime(self.clock()))
        if day != self.day:
            self.day, self.day_start_equity, self.day_stopped = day, equity, False
        if not self.day_stopped and equity < self.day_start_equity * (1 - self.limits.max_daily_loss):
            self.day_stopped = True
        self.killed = bool(self.kill_path and (self.kill_path.exists()
                                               or self.kill_path.with_name("STOP_TRADING").exists()))

    def approve(self, symbol: str, qty: float, prices: dict[str, float], audit=None) -> tuple[float, str | None]:
        """Return the allowed quantity (possibly reduced to 0) and a reason if changed.

        `audit` (night_shift.TokenOrAssetAudit) feeds Night Shift's toxicity gate overnight."""
        price = prices[symbol]
        current = self.broker.position(symbol)
        stop = self.stop_reason or ("price feed stale" if symbol in self.frozen else None)
        if stop:
            # Stopped, or no fresh price: only trades that reduce risk are allowed.
            if current == 0 or (qty > 0) == (current > 0):
                return 0.0, stop
            if abs(qty) > abs(current):
                return -current, f"{stop.split(':')[0]}: reduced to flatten"
            return qty, None
        reason = None
        bucket = BUCKET_OF.get(symbol) if BUCKET_CAP else None
        if bucket and current == 0 and qty:
            held = sum(1 for s, q in self.broker.positions.items() if abs(q) > 1e-9 and s != symbol and BUCKET_OF.get(s) == bucket)
            if held >= BUCKET_MAX:
                return 0.0, f"bucket cap: {held} {bucket} positions already open"
        max_order_qty = self.limits.max_order_value / price
        if abs(qty) > max_order_qty:
            qty, reason = max_order_qty * (1 if qty > 0 else -1), "order size capped"
        max_pos_qty = self.limits.max_position_value / price
        target = max(-max_pos_qty, min(max_pos_qty, current + qty))
        if target != current + qty:
            qty, reason = target - current, "position limit"
        gross_after = self.broker.gross_exposure(prices) - abs(current * price) + abs((current + qty) * price)
        if gross_after > self.limits.max_gross_exposure and abs(current + qty) > abs(current):
            return 0.0, "gross exposure limit"
        if self.night_active and abs(current + qty) > abs(current):
            return self._night_gate(symbol, qty, current, price, audit, reason)
        return qty, reason

    def _night_gate(self, symbol, qty, current, price, audit, reason):
        """Overnight: shrink new risk to the capsule's per-position cap, then the toxicity and stop checks."""
        from .night_shift import TokenOrAssetAudit, asset_class
        ns = self.night_shift
        cap_qty = ns.position_cap() / price
        side = 1 if current + qty > 0 else -1
        if (current > 0) != (side > 0) and current:
            current_same_side = 0.0  # a flip: the old side closes, the new side starts from flat
        else:
            current_same_side = abs(current)
        if current_same_side >= cap_qty:
            return (-current if current_same_side == 0.0 and current else 0.0), "night shift cap: position already at its limit"
        new_qty = min(abs(current + qty), cap_qty) * side - current
        if new_qty != qty:
            qty, reason = new_qty, "night shift cap"
        added = abs(current + qty) - current_same_side
        ok, why = ns.check_entry_allowed(audit or TokenOrAssetAudit(symbol, asset_class(symbol), 0.0, 0.0, 1.0),
                                         max(0.0, added * price - 1e-6))  # float slack
        if not ok:
            return (-current if current_same_side == 0.0 and current else 0.0), f"night shift: {why}"
        return qty, reason
