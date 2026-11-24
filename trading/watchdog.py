"""Dead-feed and stale-tick watchdog: no new trades on old prices, and JARVIS hears about it.

The risk manager already lets a symbol with no fresh price exit but not add (`RiskManager.frozen`, fed by
`LiveMarket.stale()`). What was missing is everything around that:

- **Bar-aware age**: a symbol is stale when its last real price is older than 2x its bar interval (more than
  2 hours on 1-hour bars), never less than the live feed's own STALE_SECONDS.
- **Stuck feeds**: a coin's price that hasn't changed at all for FLAT_SECONDS is treated as stale too. Crypto
  trades around the clock, so a flat line means the feed is frozen, not the market.
- **Dead news**: when no news feed has answered for NEWS_DEAD_POLLS polls in a row, the reading room's news view
  is decaying on silence, not on calm. That is flagged.
- **Slow feed**: a price poll that takes more than LATENCY_X times its own interval is flagged.
- **Alerts**: each problem is reported once when it starts and once when it clears, to the event log (the
  dashboard and JARVIS read it) and to the phone (trading/alerts.py), so a stall at 03:00 in Melbourne is not
  found at breakfast.

`check()` returns the symbols to block. Blocking only ever stops new risk: exits always go through.
Paper only: nothing here can place an order.
"""

import time

BAR_MULT = 2.0  # stale after this many bar intervals without a real price
MIN_STALE_SECONDS = 600.0  # never stricter than the live feed's own staleness rule
FLAT_SECONDS = 1800.0  # a coin price unchanged this long = a frozen feed
NEWS_DEAD_POLLS = 3  # news polls in a row with no feed answering
LATENCY_X = 3.0  # a price poll slower than this many poll intervals is a slow feed
CRYPTO = {"BTC", "ETH", "SOL", "XRP", "DOGE", "ADA", "AVAX", "LINK", "LTC", "BNB"}


def max_age(bar_seconds: float | None) -> float:
    return max(MIN_STALE_SECONDS, BAR_MULT * (bar_seconds or 0.0))


class FeedWatchdog:
    def __init__(self, symbols, alert=None, log=None, clock=time.time):
        self.symbols = list(symbols)
        self.alert = alert  # callable(text), e.g. Alerts.send
        self.log = log  # EventLog
        self.clock = clock
        self.problems: dict[str, str] = {}  # key -> what is wrong, while it lasts
        self.flat_since: dict[str, tuple[float, float]] = {}  # symbol -> (price, since)
        self.news_dead_polls = 0
        self._news_polls_seen = 0
        self.raised = 0
        self.cleared = 0

    def check(self, prices: dict, updated: dict | None = None, bar_seconds: float | None = None,
              news_status: dict | None = None, poll_seconds: float | None = None,
              last_poll_seconds: float | None = None) -> set[str]:
        """Look at every feed; returns the symbols whose prices are too old (or stuck) to trade on."""
        now = self.clock()
        limit = max_age(bar_seconds)
        found: dict[str, str] = {}
        stale: set[str] = set()
        for s in self.symbols:
            when = (updated or {}).get(s)
            if updated is not None and (when is None or now - when > limit):
                age = "never priced" if when is None else f"last price {(now - when) / 60:.0f} min old"
                found[f"price:{s}"] = f"{s} price feed stale ({age}): new trades blocked, exits allowed"
                stale.add(s)
                continue
            p = prices.get(s)
            if s in CRYPTO and p is not None:
                last = self.flat_since.get(s)
                if last is None or last[0] != p:
                    self.flat_since[s] = (p, now)
                elif now - last[1] >= FLAT_SECONDS:
                    found[f"price:{s}"] = (f"{s} price stuck at {p:g} for {(now - last[1]) / 60:.0f} min "
                                           "(frozen feed): new trades blocked, exits allowed")
                    stale.add(s)
        if news_status:
            polls = int(news_status.get("polls", 0) or 0)
            if polls != self._news_polls_seen:  # judge each news poll once
                self._news_polls_seen = polls
                ok = int(news_status.get("feeds_ok", 0) or 0)
                self.news_dead_polls = 0 if ok else self.news_dead_polls + 1
            if self.news_dead_polls >= NEWS_DEAD_POLLS:
                found["news"] = (f"news feeds silent for {self.news_dead_polls} polls: the news view is fading on "
                                 "silence, not calm")
        if poll_seconds and last_poll_seconds and last_poll_seconds > LATENCY_X * poll_seconds:
            found["latency"] = f"price feed slow: last poll took {last_poll_seconds:.0f}s (every {poll_seconds:.0f}s)"
        self._report(found)
        return stale

    def _report(self, found: dict[str, str]) -> None:
        for key, text in found.items():
            if key not in self.problems:
                self.raised += 1
                self._say("raised", key, text)
        for key in [k for k in self.problems if k not in found]:
            self.cleared += 1
            self._say("cleared", key, self.problems[key].split(":")[0] + ": back to normal")
        self.problems = found

    def _say(self, what: str, key: str, text: str) -> None:
        if self.log is not None:
            self.log.emit("watchdog", event=what, key=key, text=text, problems=len(self.problems))
        if self.alert is not None:
            try:
                self.alert(("Feed watchdog: " if what == "raised" else "Feed watchdog, fixed: ") + text)
            except Exception:  # an alert that fails must never stop the desk
                pass

    def status(self) -> dict:
        return {"problems": sorted(self.problems.values()), "raised": self.raised, "cleared": self.cleared,
                "news_dead_polls": self.news_dead_polls}
