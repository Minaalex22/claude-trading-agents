"""The market clock: what time it is where the money is, not where we are.

Prices don't move the same way all day. The agents read the clock in each market's own time zone (New York
for US stocks, London and New York for the crypto day), never Australian time, and act on what traders and
banks know about each part of the day:

US stocks (New York time, 9:30 to 16:00, Monday to Friday):
- **Opening hour** (9:30 to 10:30): overnight orders, news and stop runs hit at once. The first move is often
  a fake: price spikes, then gives it back. Trend and breakout traders trust it less, and new risk goes in at
  half size until the hour is over.
- **Midday** (11:30 to 14:00): the big desks are at lunch, volume dries up, moves are choppy. New risk at 3/4.
- **Power hour** (15:00 to 16:00): funds and banks place their big orders into the close (they trade against the
  day's average price and the closing auction). Trends that hold here are more trustworthy.
- **Closed**: nights, weekends. Prices are stale and the risk manager allows exits only.

Crypto (around the clock, by UTC):
- **London and New York both open**: the deepest, most honest part of the day. Full size.
- **Asia only**: thinner books. New risk at 3/4.
- **Weekend**: banks and most funds are away, liquidity is thin and moves are easy to push. Half size.

**Session average**: banks and funds measure their fills against the day's average price. A trend trader
buying far above today's average is chasing; that new risk is halved.

The simulator has no real clock, so none of this applies there; live prices and backtests use real time.
"""

from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

NEW_YORK = ZoneInfo("America/New_York")
LONDON = ZoneInfo("Europe/London")
CRYPTO = {"BTC", "ETH", "SOL"}
CHASE = 0.015  # 1.5% from the session average counts as chasing

STOCK_SIZE = {"opening_hour": 0.5, "morning": 1.0, "midday": 0.75, "afternoon": 1.0, "power_hour": 1.0, "closed": 1.0}
CRYPTO_SIZE = {"overlap": 1.0, "london": 1.0, "new_york": 1.0, "asia": 0.75, "weekend": 0.5}
TREND_TRUST = {"opening_hour": 0.3, "midday": 0.8, "power_hour": 1.2}  # how much trend/breakout signals count


@dataclass(frozen=True)
class MarketClock:
    ts: float
    stock_phase: str
    crypto_phase: str
    ny_time: str
    session_day: str  # New York date: the session that session averages belong to

    def phase(self, symbol: str) -> str:
        return self.crypto_phase if symbol in CRYPTO else self.stock_phase

    def size(self, symbol: str) -> float:
        return CRYPTO_SIZE[self.crypto_phase] if symbol in CRYPTO else STOCK_SIZE[self.stock_phase]

    def trend_trust(self, symbol: str) -> float:
        return 1.0 if symbol in CRYPTO else TREND_TRUST.get(self.stock_phase, 1.0)

    def snapshot(self) -> dict:
        return {"ny_time": self.ny_time, "stocks": self.stock_phase, "crypto": self.crypto_phase}


def _stock_phase(ny: datetime) -> str:
    if ny.weekday() >= 5:
        return "closed"
    m = ny.hour * 60 + ny.minute
    if m < 9 * 60 + 30 or m >= 16 * 60:
        return "closed"
    if m < 10 * 60 + 30:
        return "opening_hour"
    if m < 11 * 60 + 30:
        return "morning"
    if m < 14 * 60:
        return "midday"
    if m < 15 * 60:
        return "afternoon"
    return "power_hour"


def _crypto_phase(ts: float) -> str:
    utc = datetime.fromtimestamp(ts, ZoneInfo("UTC"))
    if utc.weekday() >= 5:
        return "weekend"
    london, ny = datetime.fromtimestamp(ts, LONDON), datetime.fromtimestamp(ts, NEW_YORK)
    london_open = 8 <= london.hour < 17
    ny_open = (ny.hour, ny.minute) >= (9, 30) and ny.hour < 17
    if london_open and ny_open:
        return "overlap"
    if london_open:
        return "london"
    if ny_open:
        return "new_york"
    return "asia"


def clock(ts: float | None) -> MarketClock | None:
    if ts is None:
        return None
    ny = datetime.fromtimestamp(ts, NEW_YORK)
    return MarketClock(ts, _stock_phase(ny), _crypto_phase(ts), ny.strftime("%a %H:%M New York"), ny.strftime("%Y-%m-%d"))


class SessionAverages:
    """Each symbol's average price since its session began (New York day for stocks, UTC day for crypto)."""

    def __init__(self):
        self.day: dict[str, str] = {}
        self.sum: dict[str, float] = {}
        self.n: dict[str, int] = {}

    def update(self, c: MarketClock, prices: dict[str, float]) -> None:
        utc_day = datetime.fromtimestamp(c.ts, ZoneInfo("UTC")).strftime("%Y-%m-%d")
        for s, p in prices.items():
            if s not in CRYPTO and c.stock_phase == "closed":
                continue
            day = utc_day if s in CRYPTO else c.session_day
            if self.day.get(s) != day:
                self.day[s], self.sum[s], self.n[s] = day, 0.0, 0
            self.sum[s] += p
            self.n[s] += 1

    def get(self, symbol: str) -> float | None:
        n = self.n.get(symbol)
        return self.sum[symbol] / n if n else None
