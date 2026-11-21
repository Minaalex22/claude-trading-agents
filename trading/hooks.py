"""Pre-order checks ("hooks") and the desk's own trade record.

Claude's agent program (the Claude Agent SDK) runs *hooks* before every action: small checks that can block
or change the action and say why. This is the same idea for the desk. Before an order goes to the risk
manager, every check here looks at it. A check can let it through, shrink it or block it, and its reason is
written into the order log. Orders that only cut risk (closing or shrinking a position) always pass: a check
can stop the desk taking on risk, never stop it getting out.

The checks:

- **against_the_room**: no new exposure against a confident reading-room view (news + chart).
- **symbol_paused**: Sentinel, Head of Risk, has flagged the symbol after an hour of losing trades: new risk
  there goes in at half size. (A full block was tried and cost profit: losing streaks often turn.)
- **losing_streak**: the owner's rule, as in the JARVIS risk gate: after 2 losing desk trades in a row in a symbol,
  no new risk there for 5 minutes (time to review the losses), then half size until a trade wins.
- **market_clock**: the time of day in the market's own time zone (trading/sessions.py): no new stock risk in the
  first 5 minutes after the New York open (the JARVIS rule), half size in the rest of the opening hour and on crypto weekends, 3/4 at midday and in Asia-only crypto hours.
- **chasing**: no chasing: new risk more than 1.5% away from the session average, in the direction of the move,
  goes in at half size (banks and funds buy near the day's average, not far above it).

`DeskJournal` is the desk's own trade record per symbol (the agents keep their own). A trade closes when the
desk's position goes flat or flips side. Sentinel and the Claude coach read it.

Every order also carries `why`: one short line saying which departments pushed it and what the news and the
chart said, so every decision can be explained afterwards. Paper only.
"""

from collections import deque
from dataclasses import dataclass, field

from . import sessions

STREAK = 2  # this many losing desk trades in a row in a symbol pauses it, then halves the next orders
PAUSE_SECONDS = 300  # the pause after a losing streak
RECENT = 50  # trades kept per symbol


@dataclass
class OrderCheck:
    symbol: str
    qty: float  # wanted change in position
    position: float  # position before the order
    price: float
    view: object = None  # reading_room.View or None
    paused: bool = False
    streak: int = 0  # losing desk trades in a row in this symbol
    cooling: bool = False  # inside the pause after a losing streak
    clock: object = None  # sessions.MarketClock or None (simulator)
    session_avg: float | None = None
    notes: list = field(default_factory=list)

    @property
    def adds_risk(self) -> bool:
        after = self.position + self.qty
        return abs(after) > abs(self.position) or (after and self.position and (after > 0) != (self.position > 0))


def against_the_room(c: OrderCheck) -> tuple[float, str | None]:
    if c.view is None or not getattr(c.view, "confident", False):
        return 1.0, None
    side = 1.0 if c.position + c.qty > 0 else -1.0
    if side * c.view.direction < 0:
        return 0.0, f"blocked: news and chart point the other way ({c.view.direction:+.2f})"
    return 1.0, None


def symbol_paused(c: OrderCheck) -> tuple[float, str | None]:
    return (0.5, "halved: Sentinel flagged this symbol after a losing hour") if c.paused else (1.0, None)


def losing_streak(c: OrderCheck) -> tuple[float, str | None]:
    if c.streak >= STREAK and c.cooling:
        return 0.0, f"paused: last {c.streak} desk trades here lost; 5-minute review before new risk"
    if c.streak >= STREAK:
        return 0.5, f"halved: last {c.streak} desk trades here lost"
    return 1.0, None


def market_clock(c: OrderCheck) -> tuple[float, str | None]:
    if c.clock is None:
        return 1.0, None
    size = c.clock.size(c.symbol)
    if size == 0.0:
        return 0.0, f"blocked: first 5 minutes after the New York open ({c.clock.ny_time})"
    if size < 1.0:
        return size, f"x{size:g}: {c.clock.phase(c.symbol).replace('_', ' ')} ({c.clock.ny_time})"
    return 1.0, None


def chasing(c: OrderCheck) -> tuple[float, str | None]:
    if c.clock is None or not c.session_avg:
        return 1.0, None
    gap = c.price / c.session_avg - 1
    side = 1.0 if c.position + c.qty > 0 else -1.0
    if side * gap > sessions.CHASE:
        return 0.5, f"halved: {100 * gap:+.1f}% from the session average, chasing the move"
    return 1.0, None


HOOKS = (symbol_paused, against_the_room, losing_streak, market_clock, chasing)


def run_hooks(c: OrderCheck, hooks=HOOKS) -> tuple[float, list[str]]:
    """Return the allowed quantity and the reasons of every check that changed it."""
    if not c.adds_risk:
        return c.qty, []
    scale, reasons = 1.0, []
    for hook in hooks:
        s, reason = hook(c)
        if reason:
            reasons.append(f"{hook.__name__}: {reason}")
        scale *= s
        if scale == 0:
            break
    if scale == 1.0:
        return c.qty, reasons
    # Shrink only the part that adds risk: a flip may still close the old side.
    after = c.position + c.qty
    if c.position and (after > 0) != (c.position > 0) and after:
        return -c.position + after * scale, reasons
    return c.qty * scale, reasons


class DeskJournal:
    """The desk's own closed trades per symbol."""

    def __init__(self):
        self.entry: dict[str, float] = {}
        self.trades: dict[str, deque] = {}
        self.closed = 0

    def fill(self, symbol: str, before: float, qty: float, price: float, tick: int, at: float | None = None) -> dict | None:
        after = before + qty
        closed = None
        if before and (not after or (after > 0) != (before > 0)):
            entry = self.entry.get(symbol, price)
            move = (price / entry - 1) * (1 if before > 0 else -1)
            closed = {"tick": tick, "at": at, "won": move > 0, "move": move}
            self.trades.setdefault(symbol, deque(maxlen=RECENT)).append(closed)
            self.closed += 1
        if after and (not before or (after > 0) != (before > 0)):
            self.entry[symbol] = price
        elif after and abs(after) > abs(before):
            self.entry[symbol] = (self.entry.get(symbol, price) * abs(before) + price * abs(qty)) / abs(after)
        elif not after:
            self.entry.pop(symbol, None)
        return closed

    def streak(self, symbol: str) -> int:
        n = 0
        for t in reversed(self.trades.get(symbol, ())):
            if t["won"]:
                break
            n += 1
        return n

    def cooling(self, symbol: str, tick: int, now: float | None, pause_ticks: int) -> bool:
        """True while inside the pause after the losing trade that made the streak."""
        ts = self.trades.get(symbol)
        if not ts or ts[-1]["won"]:
            return False
        last = ts[-1]
        if now is not None and last.get("at") is not None:
            return now - last["at"] < PAUSE_SECONDS
        return tick - last["tick"] < pause_ticks

    def since(self, tick: int) -> dict[str, list[dict]]:
        return {s: [t for t in ts if t["tick"] > tick] for s, ts in self.trades.items()}

    def summary(self, tick: int = 0) -> dict[str, dict]:
        out = {}
        for s, ts in self.since(tick).items():
            if ts:
                out[s] = {"trades": len(ts), "won": sum(t["won"] for t in ts),
                          "net_move": round(sum(t["move"] for t in ts), 5), "streak": self.streak(s)}
        return out


def why(v: dict, view, exposure: float) -> str:
    """One line: what pushed this order."""
    def avg(xs):
        return sum(xs) / len(xs) if xs else 0.0
    regime = avg(v.get("analyst", []))
    parts = [f"trend {avg(v.get('trend', [])):+.2f} ({len(v.get('trend', []))})",
             f"reversion {avg(v.get('reversion', [])):+.2f} ({len(v.get('reversion', []))})",
             f"research: {'trending' if regime > 0 else 'choppy'} market",
             f"news {avg(v.get('macro', [])):+.2f}", f"risk size {exposure:.0%}"]
    if view is not None:
        parts.append(f"room {view.direction:+.2f}" + (f" ({view.why})" if getattr(view, "why", "") else ""))
    return ", ".join(parts)
