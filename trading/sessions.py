"""The market clock: what time it is where the money is, not where we are.

Prices don't move the same way all day. The agents read the clock in each market's own time zone (New York
for US stocks, London and New York for the crypto day), never Australian time, and act on what traders and
banks know about each part of the day:

US stocks (New York time, 9:30 to 16:00, Monday to Friday):
- **Opening hour** (9:30 to 10:30): overnight orders, news and stop runs hit at once. The first 15 minutes are the
  banks setting up (no new risk at all), and the first move is often
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

**Session rhythm** (mina's rule for the around-the-clock markets): Asia is usually calm and ranging, London
breaks it (stop runs and breakouts of the Asia range), New York finishes the move. So in Asia the reversion
traders are trusted more and trend/breakout traders less; at London trend and breakout traders are trusted
more and fading is trusted less; in the London-New York overlap the trend carries on; once London has closed,
New York is finishing the move, so new trend entries count a little less.

**The banks' set-up** (mina's rule): the first `OPEN_MINUTES` after London opens (8:00 London) and after New
York opens (9:30 New York) are the banks setting up, and the first move is usually fake. `OPEN_FAKE` picks how
the agents treat it: "wait" (no trend or breakout entries, new risk at half size) or "fade" (trend signals
count little and reversion is trusted more, to bet against the fake move). Both were tested on real BTC, ETH
and SOL bars; see the module's numbers in the PR.

**Session average**: banks and funds measure their fills against the day's average price. A trend trader
buying far above today's average is chasing; that new risk is halved.

The simulator has no real clock, so none of this applies there; live prices and backtests use real time.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

# New York and London time from their daylight-saving rules, so the clock needs no time-zone database (Windows
# and slim Docker images ship none). US: 2 a.m. local on the second Sunday of March to the first Sunday of
# November. UK: 1 a.m. UTC on the last Sunday of March to the last Sunday of October.


def _sunday(year: int, month: int, nth: int) -> int:
    """Day of month of the nth Sunday (nth=-1: the last one)."""
    if nth > 0:
        first = datetime(year, month, 1).weekday()
        return 1 + (6 - first) % 7 + 7 * (nth - 1)
    last = (datetime(year + month // 12, month % 12 + 1, 1) - timedelta(days=1))
    return last.day - (last.weekday() + 1) % 7


def _ny(ts: float) -> datetime:
    utc = datetime.fromtimestamp(ts, timezone.utc)
    y = utc.year
    start = datetime(y, 3, _sunday(y, 3, 2), 7, tzinfo=timezone.utc)  # 2 a.m. EST
    end = datetime(y, 11, _sunday(y, 11, 1), 6, tzinfo=timezone.utc)  # 2 a.m. EDT
    return utc.astimezone(timezone(timedelta(hours=-4 if start <= utc < end else -5)))


def _london(ts: float) -> datetime:
    utc = datetime.fromtimestamp(ts, timezone.utc)
    y = utc.year
    start = datetime(y, 3, _sunday(y, 3, -1), 1, tzinfo=timezone.utc)
    end = datetime(y, 10, _sunday(y, 10, -1), 1, tzinfo=timezone.utc)
    return utc.astimezone(timezone(timedelta(hours=1 if start <= utc < end else 0)))

CRYPTO = {"BTC", "ETH", "SOL"}
CHASE = 0.015  # 1.5% from the session average counts as chasing

STOCK_SIZE = {"opening_bell": 0.0, "opening_hour": 0.5, "morning": 1.0, "midday": 0.75, "afternoon": 1.0, "power_hour": 1.0, "closed": 1.0}
CRYPTO_SIZE = {"overlap": 1.0, "london": 1.0, "new_york": 1.0, "asia": 0.75, "weekend": 0.5,
               "london_open": 0.5, "ny_open": 0.5}
OPEN_MINUTES = 30
STOCK_SETUP_MINUTES = 15  # US stocks: no new risk in the first 15 minutes (was 5), then the opening-hour rules
OPEN_FAKE = "wait"  # "wait", "fade" or "off"
OPEN_RHYTHM = {"wait": (0.0, 1.0), "fade": (0.3, 1.4)}
# Session rhythm for 24-hour markets: (trend trust, reversion trust) per phase. Off by default (RHYTHM_ON,
# env SESSION_RHYTHM=on): on two weeks of real BTC, ETH and SOL bars it did not raise the win rate.
RHYTHM_ON = False
RHYTHM = {"asia": (0.6, 1.2), "london": (1.2, 0.6), "overlap": (1.1, 0.8), "new_york": (0.8, 1.0),
          "weekend": (0.8, 1.0)}
TREND_TRUST = {"opening_bell": 0.3, "opening_hour": 0.3, "midday": 0.8, "power_hour": 1.2}  # how much trend/breakout signals count


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

    def _rhythm(self) -> tuple[float, float]:
        if self.crypto_phase in ("london_open", "ny_open"):
            return OPEN_RHYTHM.get(OPEN_FAKE, (1.0, 1.0))
        return RHYTHM.get(self.crypto_phase, (1.0, 1.0)) if RHYTHM_ON else (1.0, 1.0)

    def trend_trust(self, symbol: str) -> float:
        if symbol in CRYPTO:
            return self._rhythm()[0]
        return TREND_TRUST.get(self.stock_phase, 1.0)

    def reversion_trust(self, symbol: str) -> float:
        """How far to trust a fade: more in calm Asia, less when London is breaking the range."""
        return self._rhythm()[1] if symbol in CRYPTO else 1.0

    def snapshot(self) -> dict:
        return {"ny_time": self.ny_time, "stocks": self.stock_phase, "crypto": self.crypto_phase}


def _stock_phase(ny: datetime) -> str:
    if ny.weekday() >= 5:
        return "closed"
    m = ny.hour * 60 + ny.minute
    if m < 9 * 60 + 30 or m >= 16 * 60:
        return "closed"
    if m < 9 * 60 + 30 + STOCK_SETUP_MINUTES:
        return "opening_bell"  # mina's rule: the first 15 minutes are the banks' set-up, no new risk
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
    utc = datetime.fromtimestamp(ts, timezone.utc)
    if utc.weekday() >= 5:
        return "weekend"
    london, ny = _london(ts), _ny(ts)
    london_open = 8 <= london.hour < 17
    ny_open = (ny.hour, ny.minute) >= (9, 30) and ny.hour < 17
    if OPEN_FAKE != "off":
        if ny_open and ny.hour * 60 + ny.minute < 9 * 60 + 30 + OPEN_MINUTES:
            return "ny_open"  # the banks' set-up after New York opens
        if london_open and london.hour * 60 + london.minute < 8 * 60 + OPEN_MINUTES:
            return "london_open"  # the banks' set-up after London opens
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
    ny = _ny(ts)
    return MarketClock(ts, _stock_phase(ny), _crypto_phase(ts), ny.strftime("%a %H:%M New York"), ny.strftime("%Y-%m-%d"))


class SessionAverages:
    """Each symbol's average price since its session began (New York day for stocks, UTC day for crypto)."""

    def __init__(self):
        self.day: dict[str, str] = {}
        self.sum: dict[str, float] = {}
        self.n: dict[str, int] = {}

    def update(self, c: MarketClock, prices: dict[str, float]) -> None:
        utc_day = datetime.fromtimestamp(c.ts, timezone.utc).strftime("%Y-%m-%d")
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
