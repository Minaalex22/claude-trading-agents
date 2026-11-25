"""Execution integrity: signals may use smoothed prices, fills never do (the Heikin Ashi trap).

A backtest that fills at a smoothed price (Heikin Ashi, an EMA, a bar's midpoint) or at the same close the signal
was read from grants itself price improvement the market never offered. The rule enforced here:

  SIGNAL = anything (Heikin Ashi, z-scores, EMAs)
  PRICE  = the real market price of the NEXT bar after the decision, plus spread and slippage

With EXEC_NEXT_BAR on (env, on by default) the desk queues each order it decides on a bar and fills it on the next
bar's real price, paying half the spread plus slippage against it. Our history files hold one real price per bar
(the close), so "next bar's price" is that bar's close; feeds with real opens can pass them in.

`FillAudit` watches every desk fill and counts the ones that break the rule: filled on the same bar the decision
was made on, or at a price outside what the bar really traded (a synthetic price).
"""

from __future__ import annotations

from dataclasses import dataclass, field

EXEC_NEXT_BAR = True
SPREAD_BPS = 2.0    # full bid/ask spread; a fill pays half of it
SLIPPAGE_BPS = 1.0  # on top of the broker's 1 bp fee


def friction() -> float:
    """Adverse price move per fill, as a fraction of price: half the spread plus slippage."""
    return (SPREAD_BPS / 2 + SLIPPAGE_BPS) / 10_000


def fill_price(qty: float, real_price: float) -> float:
    """What a market order really pays: the real price moved against you by half the spread plus slippage."""
    f = friction()
    return real_price * (1 + f) if qty > 0 else real_price * (1 - f)


@dataclass(frozen=True)
class DualBar:
    """One bar, two streams: real OHLC for fills, stops and P&L; Heikin Ashi for signals only."""
    real_open: float
    real_high: float
    real_low: float
    real_close: float
    ha_open: float
    ha_high: float
    ha_low: float
    ha_close: float


def heikin_ashi(bars: list[dict]) -> list[DualBar]:
    """Pair each real bar with its Heikin Ashi bar. Bars need open/high/low/close; a bar with only a close
    takes the previous close as its open (so a closes-only feed still gives a usable HA stream)."""
    out: list[DualBar] = []
    prev_close = prev_ha_open = prev_ha_close = None
    for b in bars:
        b = {str(k).lower(): v for k, v in b.items()}  # Open/Close exports work too
        c = float(b["close"])
        o = float(b.get("open", prev_close if prev_close is not None else c))
        h = float(b.get("high", max(o, c)))
        l = float(b.get("low", min(o, c)))
        ha_close = (o + h + l + c) / 4
        ha_open = (o + c) / 2 if prev_ha_open is None else (prev_ha_open + prev_ha_close) / 2
        out.append(DualBar(o, h, l, c, ha_open, max(h, ha_open, ha_close), min(l, ha_open, ha_close), ha_close))
        prev_close, prev_ha_open, prev_ha_close = c, ha_open, ha_close
    return out


def ha_step(state: dict, price: float) -> tuple[float, float]:
    """Incremental Heikin Ashi from a closes-only stream. Returns (ha_open, ha_close) for this bar."""
    prev = state.get("close", price)
    o, c = prev, price
    ha_close = (o + max(o, c) + min(o, c) + c) / 4
    ha_open = (o + c) / 2 if "ha_open" not in state else (state["ha_open"] + state["ha_close"]) / 2
    state.update(close=price, ha_open=ha_open, ha_close=ha_close)
    return ha_open, ha_close


@dataclass
class FillAudit:
    """Counts desk fills that break the real-price rule."""
    fills: int = 0
    same_bar: int = 0
    synthetic: int = 0
    examples: list = field(default_factory=list)

    def check(self, symbol: str, decided_tick: int, fill_tick: int, price: float, low: float, high: float,
              qty: float) -> None:
        self.fills += 1
        slack = max(abs(low), abs(high)) * (friction() + 1e-9)
        problem = None
        if fill_tick <= decided_tick:
            self.same_bar += 1
            problem = "filled on the bar the signal was read from"
        if not (low - slack <= price <= high + slack):
            self.synthetic += 1
            problem = f"price {price:.6g} outside the real bar [{low:.6g}, {high:.6g}]"
        if problem and len(self.examples) < 5:
            self.examples.append({"symbol": symbol, "qty": round(qty, 6), "tick": fill_tick, "problem": problem})

    def report(self) -> dict:
        bad = self.same_bar + self.synthetic
        return {"fills": self.fills, "same_bar_fills": self.same_bar, "synthetic_price_fills": self.synthetic,
                "clean": bad == 0, "examples": self.examples,
                "rule": "signal on any price; fill on the next bar's real price plus spread and slippage"}
