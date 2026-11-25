"""Event guard: no new risk into earnings or big economic news (blueprint function #3). PRACTICE ONLY.

mina's rules:
- Single-stock earnings: no new entries from 24 hours before the report to 1 hour after it.
- HIGH-impact economic news (CPI, FOMC, jobs report, RBA ...): no new entries from 45 minutes before to
  30 minutes after, and half size from 2 hours before.
Getting out is never blocked (hooks.py only checks orders that add risk).

Free calendars, refreshed every hour in the background:
- Earnings: Nasdaq's public earnings calendar (api.nasdaq.com/api/calendar/earnings?date=YYYY-MM-DD),
  today and the next few days, for the symbols we trade. Time of day: "before open" = 9:00 New York,
  "after close" = 16:30 New York, unknown = 9:00.
- Economic news: the weekly calendar feed (nfs.faireconomy.media/ff_calendar_thisweek.json), HIGH and MEDIUM.
If a calendar can't be reached the guard says so on the Audit page and lets trades through (it never guesses).
"""
from __future__ import annotations

import json
import threading
import time
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from . import sessions

USER_AGENT = "Mozilla/5.0 (compatible; ats-matrix-practice/1.0)"
REFRESH_SECONDS = 3600
# Earnings always loaded (mina): the big tech names move the US indices and the US arm even when not traded.
ALWAYS_WATCH = {"AAPL", "NVDA", "TSLA", "MSFT", "AMZN", "META", "GOOGL", "GOOG"}
EARNINGS_DAYS = 4                 # today + the next 3 days
EARN_BEFORE_MIN, EARN_AFTER_MIN = 24 * 60, 60
MACRO_BEFORE_MIN, MACRO_AFTER_MIN = 45, 30
HALF_SIZE_FROM_MIN = 120
NASDAQ = "https://api.nasdaq.com/api/calendar/earnings?date={day}"
MACRO_FEED = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"


@dataclass(frozen=True)
class ScheduledEvent:
    scope: str              # a symbol ("NVDA") for earnings, a currency ("USD", "AUD") for economic news
    name: str               # "NVDA earnings", "US CPI"
    at: datetime            # UTC
    impact: str             # "HIGH" | "MEDIUM"
    kind: str = "macro"     # "earnings" | "macro"

    def as_dict(self) -> dict:
        return {"scope": self.scope, "name": self.name, "at": self.at.strftime("%Y-%m-%d %H:%M UTC"),
                "impact": self.impact, "kind": self.kind}


def _currencies(symbol: str) -> set[str]:
    """Which currencies' news moves this symbol. US stocks and gold/oil/US indices move on USD news."""
    s = symbol.upper()
    if s in sessions.CRYPTO:
        return set()                           # crypto: earnings/macro guard off (trades 24/7, own drivers)
    if len(s) == 6 and s.isalpha() and s[:3] != s[3:]:
        return {s[:3], s[3:]}                  # forex pair, e.g. EURUSD -> EUR, USD
    if "USD" in s:
        return {"USD", s.replace("USD", "")}   # XAUUSD, BTCUSD style
    return {"USD"}                             # US shares and US indices


def check(symbol: str, events: list[ScheduledEvent], now: datetime | None = None) -> tuple[bool, float, str]:
    """(allowed, size multiplier, reason). The strictest event wins."""
    dt = now or datetime.now(timezone.utc)
    sym = symbol.upper()
    ccy = _currencies(sym)
    best = (True, 1.0, "PASS")
    for ev in events:
        mins = (ev.at - dt).total_seconds() / 60.0
        if ev.kind == "earnings":
            if ev.scope.upper() == sym and -EARN_AFTER_MIN <= mins <= EARN_BEFORE_MIN:
                return False, 0.0, f"earnings block: {ev.name} in {mins:.0f}m"
            continue
        if ev.impact.upper() != "HIGH" or ev.scope.upper() not in ccy:
            continue
        if -MACRO_AFTER_MIN <= mins <= MACRO_BEFORE_MIN:
            return False, 0.0, f"news block: {ev.name} in {mins:.0f}m"
        if MACRO_BEFORE_MIN < mins <= HALF_SIZE_FROM_MIN and best[1] > 0.5:
            best = (True, 0.5, f"half size: {ev.name} in {mins:.0f}m")
    return best


# ---------------------------------------------------------------- free calendars

def _ny(day: datetime, hh: int, mm: int) -> datetime:
    """A New York wall-clock time on `day` -> UTC (US daylight-saving rules)."""
    from .timemachine import _ny_offset
    guess = datetime(day.year, day.month, day.day, hh, mm, tzinfo=timezone.utc)
    return guess - timedelta(seconds=_ny_offset(guess.timestamp() + 5 * 3600))


def parse_nasdaq(body: dict, day: datetime, symbols: set[str]) -> list[ScheduledEvent]:
    rows = ((body or {}).get("data") or {}).get("rows") or []
    out = []
    for r in rows:
        sym = str(r.get("symbol", "")).upper().strip()
        if sym not in symbols:
            continue
        t = str(r.get("time", "")).lower()
        at = _ny(day, 16, 30) if "after" in t else _ny(day, 9, 0)
        out.append(ScheduledEvent(sym, f"{sym} earnings", at, "HIGH", "earnings"))
    return out


def parse_macro(rows: list) -> list[ScheduledEvent]:
    out = []
    for r in rows or []:
        impact = str(r.get("impact", "")).upper()
        if impact not in ("HIGH", "MEDIUM"):
            continue
        try:
            at = datetime.fromisoformat(str(r["date"])).astimezone(timezone.utc)
        except (KeyError, ValueError):
            continue          # "All Day" / "Tentative" items have no usable time
        ccy = str(r.get("country", "")).upper()[:3]
        out.append(ScheduledEvent(ccy, f"{ccy} {str(r.get('title', ''))[:60]}", at, impact, "macro"))
    return out


def _get_json(url: str, timeout: float = 15.0):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 - fixed https hosts
        return json.loads(r.read(5_000_000))


class Calendar:
    """Holds the upcoming events. `refresh()` fetches both feeds; `start()` refreshes hourly in the background."""

    def __init__(self, symbols, get=None, clock=time.time):
        self.symbols = {s.upper() for s in symbols} | ALWAYS_WATCH
        self._get = get or (lambda url: _get_json(url))
        self._clock = clock
        self.events: list[ScheduledEvent] = []
        self.status = {"earnings": "not loaded", "macro": "not loaded", "updated": None}
        self._lock = threading.Lock()
        self._thread = None

    def refresh(self) -> None:
        now = datetime.fromtimestamp(self._clock(), timezone.utc)
        earn, macro, st = [], [], {}
        try:
            for d in range(EARNINGS_DAYS):
                day = now + timedelta(days=d)
                earn += parse_nasdaq(self._get(NASDAQ.format(day=day.strftime("%Y-%m-%d"))), day, self.symbols)
            st["earnings"] = f"ok ({len(earn)} reports in the next {EARNINGS_DAYS} days)"
        except Exception as e:  # noqa: BLE001
            st["earnings"] = f"unreachable ({type(e).__name__}): earnings guard is not blocking"
        try:
            macro = parse_macro(self._get(MACRO_FEED))
            st["macro"] = f"ok ({sum(e.impact == 'HIGH' for e in macro)} high-impact this week)"
        except Exception as e:  # noqa: BLE001
            st["macro"] = f"unreachable ({type(e).__name__}): news guard is not blocking"
        st["updated"] = now.strftime("%Y-%m-%d %H:%M UTC")
        with self._lock:
            # keep the last good list from a feed that failed this time
            old = self.events
            if not earn and "unreachable" in st["earnings"]:
                earn = [e for e in old if e.kind == "earnings"]
            if not macro and "unreachable" in st["macro"]:
                macro = [e for e in old if e.kind == "macro"]
            self.events = sorted(earn + macro, key=lambda e: e.at)
            self.status = st

    def start(self) -> None:
        if self._thread:
            return

        def loop():
            while True:
                self.refresh()
                time.sleep(REFRESH_SECONDS)
        self._thread = threading.Thread(target=loop, daemon=True, name="event-guard")
        self._thread.start()

    def snapshot(self) -> list[ScheduledEvent]:
        with self._lock:
            return list(self.events)

    def upcoming(self, now: datetime | None = None, limit: int = 30) -> list[dict]:
        dt = now or datetime.fromtimestamp(self._clock(), timezone.utc)
        return [e.as_dict() for e in self.snapshot() if e.at >= dt - timedelta(hours=1)][:limit]
