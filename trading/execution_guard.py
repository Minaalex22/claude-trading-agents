"""Passive limit entries for Scalper (mean-reversion) trades: rest at the touch instead of crossing the spread.

On 1h and 4h mean-reversion entries, buying at the ask and selling at the bid with market orders eats 10-25% of
the edge over hundreds of trades. A fade has no hurry: price is stretched and expected to come back, so the
desk can wait for it.

When the desk's new exposure on a symbol is led by the Reversion department (the Scalper desk), the order is
parked as a post-only limit at the touch (TOUCH_BPS better than the last price) for up to MAX_BARS bars:

- **filled** when a later price trades at or through the limit: the fill is at the limit price;
- **cancelled** when price runs away from it by more than RUNAWAY_ATR x the symbol's 1R (the move happened
  without us: no chasing), or when MAX_BARS bars pass without a fill.

Trend and breakout entries, exits, trims and kill-switch flattening never wait: they go at once as before.

Paper and demo only: the parked order fills in the paper broker. The real-money mirror (trading/live_orders.py)
still only ever proposes a trade for the owner to approve, after a paper fill, exactly as before.
"""

from dataclasses import dataclass

TOUCH_BPS = 2.0  # how far inside the last price the limit rests (the touch, half a typical spread)
MAX_BARS = 2  # bars a limit may rest before it is cancelled
RUNAWAY_ATR = 0.35  # price moving this many 1R away from the limit cancels it
ENABLED = True  # PASSIVE_ENTRIES=off sends every entry at once

# Broker quote check (mina, 24 Nov 2026): before new risk, the broker's live mid must be within DRIFT_MAX of the
# price the desk decided on. A bigger gap means one of the two feeds is wrong or late, so the entry waits.
# Live desk only, using Mitrade's public quotes (trading/mitrade.py). Exits are never blocked. BROKER_DRIFT=off.
DRIFT_MAX = 0.0015  # 0.15%
DRIFT_CHECK = True
BROKER_NAMES = {"BTC": "BTCUSD", "ETH": "ETHUSD", "GOLD": "XAUUSD", "XAU": "XAUUSD", "SILVER": "XAGUSD",
                "XAG": "XAGUSD", "SPX": "US500", "SPY": "US500", "NDX": "NAS100", "QQQ": "NAS100", "DJI": "US30",
                "OIL": "USOIL", "WTI": "USOIL", "BRENT": "UKOIL", "ASX": "AUS200", "NIKKEI": "JPN225"}


def broker_symbol(symbol: str) -> str:
    s = str(symbol).upper().replace("-", "").replace("/", "").replace("=X", "")
    return BROKER_NAMES.get(s, s[:-4] + "USD" if s.endswith("USDT") else s)


def broker_mids(symbols, quotes: dict) -> dict[str, float]:
    """Desk symbol -> broker mid, for the symbols the broker quotes."""
    out = {}
    for sym in symbols:
        q = quotes.get(broker_symbol(sym))
        if q and q.get("mid"):
            out[sym] = float(q["mid"])
    return out


def quote_drift(desk_price: float, broker_mid: float) -> tuple[bool, float]:
    """(within DRIFT_MAX, gap in %)."""
    gap = abs(broker_mid - desk_price) / max(1e-12, abs(desk_price))
    return gap <= DRIFT_MAX, round(gap * 100, 4)


@dataclass
class Parked:
    symbol: str
    qty: float
    limit: float
    placed_at: float  # price when parked
    atr: float  # the symbol's 1R in price, for the runaway test
    bars: int = 0
    why: object = None
    score: float = 0.0
    notes: list | None = None


def scalper_led(votes: dict, qty: float) -> bool:
    """Is this new exposure led by the Reversion (Scalper) department rather than Trend?"""
    rev, trend = votes.get("reversion") or [], votes.get("trend") or []
    r = sum(rev) / len(rev) if rev else 0.0
    t = sum(trend) / len(trend) if trend else 0.0
    side = 1.0 if qty > 0 else -1.0
    return side * r > 0 and abs(r) >= abs(t)


class PassiveBook:
    def __init__(self):
        self.parked: dict[str, Parked] = {}
        self.stats = {"placed": 0, "filled": 0, "cancelled_runaway": 0, "cancelled_time": 0, "saved_usd": 0.0}

    def place(self, symbol: str, qty: float, price: float, atr: float, **extra) -> Parked:
        side = 1.0 if qty > 0 else -1.0
        order = Parked(symbol, qty, price * (1 - side * TOUCH_BPS / 10_000), price, atr, **extra)
        self.parked[symbol] = order
        self.stats["placed"] += 1
        return order

    def step(self, prices: dict[str, float]) -> tuple[list[Parked], list[tuple[Parked, str]]]:
        """One bar later: (orders filled at their limit, (orders cancelled, why))."""
        filled, cancelled = [], []
        for symbol, o in list(self.parked.items()):
            p = prices.get(symbol)
            if p is None:
                continue
            o.bars += 1
            side = 1.0 if o.qty > 0 else -1.0
            if side * (p - o.limit) <= 0:  # traded at or through the limit
                filled.append(o)
                self.stats["filled"] += 1
                self.stats["saved_usd"] += abs(o.qty) * abs(o.placed_at - o.limit)
                del self.parked[symbol]
            elif o.atr and side * (p - o.limit) > RUNAWAY_ATR * o.atr:
                cancelled.append((o, f"price ran {side * (p - o.limit) / o.atr:.2f}R away"))
                self.stats["cancelled_runaway"] += 1
                del self.parked[symbol]
            elif o.bars >= MAX_BARS:
                cancelled.append((o, f"no fill in {MAX_BARS} bars"))
                self.stats["cancelled_time"] += 1
                del self.parked[symbol]
        return filled, cancelled

    def cancel(self, symbol: str) -> Parked | None:
        return self.parked.pop(symbol, None)

    def status(self) -> dict:
        s = dict(self.stats)
        s["saved_usd"] = round(s["saved_usd"], 2)
        s["resting"] = len(self.parked)
        done = s["filled"] + s["cancelled_runaway"] + s["cancelled_time"]
        s["fill_rate"] = round(s["filled"] / done, 3) if done else None
        return s
