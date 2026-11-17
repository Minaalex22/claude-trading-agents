"""Hard risk limits. Agents can only propose; these rules have the final say.

Three stops sit above the per-order limits:
- the drawdown halt: equity falls `max_drawdown` below its peak, and only exits are allowed from then on;
- the daily loss stop: equity falls `max_daily_loss` below where the (UTC) day started, and only exits are
  allowed until the next day;
- the kill switch: a file named KILL in the events folder (or `kill_path`). While it exists the desk closes
  every position and opens nothing. Delete the file to trade again.
Symbols whose price feed is stale (`frozen`) can be exited but not added to.
"""

import time
from dataclasses import dataclass
from pathlib import Path

from .broker import PaperBroker


@dataclass
class RiskLimits:
    max_position_value: float = 20_000.0  # per symbol, absolute
    max_order_value: float = 5_000.0
    max_gross_exposure: float = 80_000.0
    max_drawdown: float = 0.10  # fraction of starting equity; breach halts trading
    max_daily_loss: float = 0.02  # fraction of the day's starting equity; breach stops new risk until tomorrow


class RiskManager:
    def __init__(self, limits: RiskLimits, broker: PaperBroker, kill_path: Path | None = None, clock=time.time):
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

    @property
    def stop_reason(self) -> str | None:
        if self.killed:
            return "kill switch on"
        if self.halted:
            return "halted: max drawdown breached"
        if self.day_stopped:
            return "daily loss limit hit"
        return None

    def update(self, prices: dict[str, float]) -> None:
        equity = self.broker.equity(prices)
        self.peak_equity = max(self.peak_equity, equity)
        if not self.halted and equity < self.peak_equity * (1 - self.limits.max_drawdown):
            self.halted = True
        day = time.strftime("%Y-%m-%d", time.gmtime(self.clock()))
        if day != self.day:
            self.day, self.day_start_equity, self.day_stopped = day, equity, False
        if not self.day_stopped and equity < self.day_start_equity * (1 - self.limits.max_daily_loss):
            self.day_stopped = True
        self.killed = bool(self.kill_path and self.kill_path.exists())

    def approve(self, symbol: str, qty: float, prices: dict[str, float]) -> tuple[float, str | None]:
        """Return the allowed quantity (possibly reduced to 0) and a reason if changed."""
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
        return qty, reason
