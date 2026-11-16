"""Real news for paper trading on live prices: free RSS headlines, read by the reading room.

MARKET=live turns this on in place of the simulated wire (LIVE_NEWS=off turns it off). A background
thread polls public RSS feeds every POLL_SECONDS (no keys, no accounts):
- per symbol: Yahoo Finance's headline feed and a Google News search for the company name;
- general: CNBC, MarketWatch, CoinDesk and Cointelegraph, matched to symbols by ticker or company name.

Each headline is matched to one symbol, de-duplicated by its wording (the same story from ten sites
counts once), and dropped if it is older than MAX_AGE_SECONDS. `tick()` hands the desk only headlines it
hasn't seen yet, in the same shape as the simulated wire, so the reading room reads their text: Claude when
ANTHROPIC_API_KEY is set, otherwise the built-in finance lexicon. The academy's analysts don't read them:
their courses use the simulator's event codes, which real headlines don't have.

This module only reads headlines. It cannot place an order.
"""

import re
import threading
import time
import urllib.parse
import urllib.request
from collections import deque
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree

POLL_SECONDS = 300.0
MAX_AGE_SECONDS = 6 * 3600
SEEN_MAX = 5000
LATEST = 8
USER_AGENT = "Mozilla/5.0 (paper-trading news reader)"

NAMES = {
    "AAPL": ["Apple"], "MSFT": ["Microsoft"], "NVDA": ["Nvidia"], "AMZN": ["Amazon"],
    "GOOG": ["Alphabet", "Google"], "GOOGL": ["Alphabet", "Google"], "TSLA": ["Tesla"],
    "META": ["Meta Platforms", "Facebook"], "AMD": ["AMD", "Advanced Micro Devices"],
    "BTC": ["Bitcoin"], "ETH": ["Ethereum", "Ether"], "SOL": ["Solana"],
}
CRYPTO = {"BTC", "ETH", "SOL"}
GENERAL_FEEDS = [
    "https://www.cnbc.com/id/100003114/device/rss/rss.html",
    "https://feeds.content.dowjones.io/public/rss/mw_topstories",
    "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "https://cointelegraph.com/rss",
]


def symbol_feeds(symbol: str) -> list[str]:
    name = NAMES.get(symbol, [symbol])[0]
    query = urllib.parse.quote(f'"{name}" {"crypto" if symbol in CRYPTO else "stock"}')
    feeds = [f"https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en"]
    yahoo = f"{symbol}-USD" if symbol in CRYPTO else symbol
    feeds.append(f"https://feeds.finance.yahoo.com/rss/2.0/headline?s={yahoo}&region=US&lang=en-US")
    return feeds


def _get(url: str, timeout: float = 10.0) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (fixed https URLs only)
        return resp.read(2_000_000)


def parse_rss(xml: bytes) -> list[tuple[str, float | None]]:
    """(title, published unix time or None) for each item of an RSS feed."""
    items = []
    for item in ElementTree.fromstring(xml).iter("item"):
        title = (item.findtext("title") or "").strip()
        if not title:
            continue
        when = None
        try:
            when = parsedate_to_datetime(item.findtext("pubDate") or "").timestamp()
        except (TypeError, ValueError):
            pass
        items.append((title, when))
    return items


def _key(title: str) -> str:
    # Google News appends " - Publisher"; the same story then dedupes across sites.
    return re.sub(r"[^a-z0-9 ]", "", title.rsplit(" - ", 1)[0].lower()).strip()


def match_symbol(title: str, symbols: list[str]) -> str | None:
    """The one symbol a general headline is about, by ticker ($NVDA, NVDA) or company name; None if not one."""
    found = set()
    for s in symbols:
        words = [s] + NAMES.get(s, [])
        if any(re.search(rf"(?<![A-Za-z]){re.escape(w)}(?![A-Za-z])", title) for w in words):
            found.add(s)
    return found.pop() if len(found) == 1 else None


class LiveNewsFeed:
    """Drop-in for the simulated NewsWire: tick() returns new headline dicts, `latest` the newest few."""

    def __init__(self, symbols: list[str], poll_seconds: float = POLL_SECONDS, get=_get,
                 start_thread: bool = True, clock=time.time):
        self.symbols = list(symbols)
        self.poll_seconds = poll_seconds
        self.get = get
        self.clock = clock
        self.latest: list[dict] = []
        self.seen: dict[str, None] = {}  # insertion-ordered set of headline keys, bounded
        self.queue: deque[dict] = deque(maxlen=500)
        self.polls = 0
        self.feeds_ok = 0
        self.feeds_failed = 0
        self.last_error = ""
        self._lock = threading.Lock()
        if start_thread:
            threading.Thread(target=self._run, name="live-news", daemon=True).start()

    def sources(self) -> list[tuple[str, str | None]]:
        """(feed url, its symbol or None for a general feed)."""
        return [(url, s) for s in self.symbols for url in symbol_feeds(s)] + [(u, None) for u in GENERAL_FEEDS]

    def poll_once(self) -> int:
        ok = failed = added = 0
        now = self.clock()
        for url, symbol in self.sources():
            try:
                items = parse_rss(self.get(url))
                ok += 1
            except Exception as e:  # one dead feed never stops the others
                failed += 1
                self.last_error = f"{urllib.parse.urlsplit(url).netloc}: {type(e).__name__}: {e}"[:200]
                continue
            for title, when in items:
                if when is not None and now - when > MAX_AGE_SECONDS:
                    continue
                s = symbol or match_symbol(title, self.symbols)
                key = _key(title)
                if s is None or not key or key in self.seen:
                    continue
                self.seen[key] = None
                if len(self.seen) > SEEN_MAX:
                    del self.seen[next(iter(self.seen))]
                with self._lock:
                    self.queue.append({"symbol": s, "kind": "live", "event": None, "value": None,
                                       "text": f"{s}: {title}", "source": urllib.parse.urlsplit(url).netloc})
                added += 1
        self.polls += 1
        self.feeds_ok, self.feeds_failed = ok, failed
        return added

    def _run(self) -> None:
        while True:
            try:
                self.poll_once()
            except Exception as e:  # keep the thread alive whatever a feed sends
                self.last_error = f"{type(e).__name__}: {e}"[:200]
            time.sleep(self.poll_seconds)

    def tick(self, drift: dict[str, float], tick: int) -> list[dict]:
        with self._lock:
            out = [dict(h, tick=tick) for h in self.queue]
            self.queue.clear()
        self.latest = (out[::-1] + self.latest)[:LATEST]
        return out

    def status(self) -> dict:
        return {"news": "live", "sources": len(self.sources()), "feeds_ok": self.feeds_ok,
                "feeds_failed": self.feeds_failed, "headlines_seen": len(self.seen), "polls": self.polls,
                "last_error": self.last_error}
