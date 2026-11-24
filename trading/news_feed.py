"""Real news for paper trading on live prices: free RSS headlines, read by the reading room.

MARKET=live turns this on in place of the simulated wire (LIVE_NEWS=off turns it off). A background
thread polls public RSS feeds every POLL_SECONDS (no keys, no accounts):
- per symbol: Yahoo Finance's headline feed and a Google News search for the company name;
- general: 17 feeds from CNBC, MarketWatch, Yahoo Finance, Seeking Alpha, Nasdaq, CoinDesk, Cointelegraph, Decrypt
  Bitcoin Magazine and BTCC, matched to symbols by ticker or company name.

Each headline is matched to one symbol, de-duplicated (the same story from ten sites counts once), and
dropped if it is older than MAX_AGE_SECONDS.

**Same story, different words** (mina's cost fix): exact wording isn't enough. "Nvidia beats Q3 earnings
estimates" and "NVDA smashes Q3 profit forecast" are one story. Each headline is boiled down to its keywords
(synonyms folded: smashes/tops/beats -> beat, profit/EPS -> earnings, forecast/estimates -> estimate; the
company's own name and filler words dropped). Two headlines on the same symbol within STORY_WINDOW (2 hours)
whose keywords overlap at least STORY_OVERLAP are the same story: the second one only counts as a second
source, and never costs a second read. `story_key()` gives the (symbol, 2-hour bucket, top 3 keywords) key.

**Two-source check** (as in the JARVIS validation department): a story counts at half strength when the first
source reports it, and the other half arrives once a second, different source carries the same story. A
rumour from one site can't move the view on its own. `tick()` hands the desk only headlines it
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
STORY_WINDOW = 2 * 3600  # headlines further apart than this are separate stories
STORY_OVERLAP = 0.5  # keyword overlap (shared / all) that makes two headlines one story
MAX_AGE_SECONDS = 6 * 3600
SEEN_MAX = 5000
LATEST = 8
USER_AGENT = "Mozilla/5.0 (paper-trading news reader)"

NAMES = {
    "AAPL": ["Apple"], "MSFT": ["Microsoft"], "NVDA": ["Nvidia"], "AMZN": ["Amazon"],
    "GOOG": ["Alphabet", "Google"], "GOOGL": ["Alphabet", "Google"], "TSLA": ["Tesla"],
    "META": ["Meta Platforms", "Facebook"], "AMD": ["AMD", "Advanced Micro Devices"],
    "BTC": ["Bitcoin"], "ETH": ["Ethereum", "Ether"], "SOL": ["Solana"],
    # the Whole Matrix desk's assets
    "GOLD": ["gold price", "gold"], "WTI": ["oil price", "crude oil", "crude", "OPEC"], "NDX": ["Nasdaq"],
    "AUDUSD": ["Australian dollar", "Aussie dollar", "RBA"], "USDCAD": ["Canadian dollar", "loonie", "Bank of Canada"],
}
# Yahoo's headline feed for symbols whose ticker isn't the symbol itself
YAHOO_RSS = {"GOLD": "GC=F", "WTI": "CL=F", "NDX": "^NDX", "AUDUSD": "AUDUSD=X", "USDCAD": "CAD=X"}
CRYPTO = {"BTC", "ETH", "SOL"}
GENERAL_FEEDS = [
    # CNBC: top news, markets, investing, economy, technology, finance
    "https://www.cnbc.com/id/100003114/device/rss/rss.html",
    "https://www.cnbc.com/id/20409666/device/rss/rss.html",
    "https://www.cnbc.com/id/15839069/device/rss/rss.html",
    "https://www.cnbc.com/id/20910258/device/rss/rss.html",
    "https://www.cnbc.com/id/19854910/device/rss/rss.html",
    "https://www.cnbc.com/id/10000664/device/rss/rss.html",
    # MarketWatch: top stories, market pulse, real-time headlines
    "https://feeds.content.dowjones.io/public/rss/mw_topstories",
    "https://feeds.content.dowjones.io/public/rss/mw_marketpulse",
    "https://feeds.content.dowjones.io/public/rss/mw_realtimeheadlines",
    # Yahoo Finance top stories, Seeking Alpha market currents, Nasdaq markets
    "https://finance.yahoo.com/news/rssindex",
    "https://seekingalpha.com/market_currents.xml",
    "https://www.nasdaq.com/feed/rssoutbound?category=Markets",
    # Crypto: CoinDesk, Cointelegraph, Decrypt, Bitcoin Magazine
    "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "https://cointelegraph.com/rss",
    "https://decrypt.co/feed",
    "https://bitcoinmagazine.com/.rss/full/",
    # BTCC exchange news (listings, announcements), via Google News
    "https://news.google.com/rss/search?q=%22BTCC%22+exchange&hl=en-US&gl=US&ceid=US:en",
]


def symbol_feeds(symbol: str) -> list[str]:
    name = NAMES.get(symbol, [symbol])[0]
    kind = "crypto" if symbol in CRYPTO else "" if symbol in YAHOO_RSS else "stock"
    query = urllib.parse.quote(f'"{name}" {kind}'.strip())
    feeds = [f"https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en"]
    yahoo = YAHOO_RSS.get(symbol) or (f"{symbol}-USD" if symbol in CRYPTO else symbol)
    feeds.append(f"https://feeds.finance.yahoo.com/rss/2.0/headline?s={urllib.parse.quote(yahoo)}&region=US&lang=en-US")
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


# Words that say the same thing in a headline, folded to one keyword.
SYNONYMS = {
    "beat": "beat", "beats": "beat", "top": "beat", "tops": "beat", "topped": "beat", "smash": "beat",
    "smashes": "beat", "smashed": "beat", "crush": "beat", "crushes": "beat", "exceed": "beat", "exceeds": "beat",
    "surpass": "beat", "surpasses": "beat", "outpace": "beat", "outpaces": "beat",
    "miss": "miss", "misses": "miss", "missed": "miss", "short": "miss", "disappoint": "miss", "disappoints": "miss",
    "earnings": "earnings", "earning": "earnings", "profit": "earnings", "profits": "earnings", "eps": "earnings",
    "income": "earnings", "results": "earnings", "quarterly": "earnings",
    "estimate": "estimate", "estimates": "estimate", "forecast": "estimate", "forecasts": "estimate",
    "expectations": "estimate", "expected": "estimate", "consensus": "estimate", "views": "estimate",
    "revenue": "revenue", "revenues": "revenue", "sales": "revenue",
    "raise": "raise", "raises": "raise", "raised": "raise", "hike": "raise", "hikes": "raise", "lifts": "raise",
    "cut": "cut", "cuts": "cut", "lowers": "cut", "slashes": "cut", "trims": "cut",
    "surge": "up", "surges": "up", "soar": "up", "soars": "up", "jump": "up", "jumps": "up", "rally": "up",
    "rallies": "up", "climbs": "up", "gains": "up", "rises": "up", "spikes": "up",
    "plunge": "down", "plunges": "down", "tumble": "down", "tumbles": "down", "slump": "down", "slumps": "down",
    "sink": "down", "sinks": "down", "drop": "down", "drops": "down", "falls": "down", "slides": "down",
    "shares": "stock", "stock": "stock", "stocks": "stock",
}
FILLER = set("a an the and or of to in on for at by with as its it is are was be from after over amid than "
             "this that new says said report reports reported per vs while into up down out about more just "
             "inc corp co ltd plc company stock".split()) - {"up", "down"}
EVENT_WORDS = ("beat", "miss", "raise", "cut", "up", "down", "earnings", "revenue", "estimate")


def keywords(title: str, symbol: str | None = None) -> frozenset[str]:
    """The words that carry a headline's story, synonyms folded, filler and the company's own name dropped."""
    text = title.rsplit(" - ", 1)[0].lower()
    own = {w.lower() for name in ([symbol] + NAMES.get(symbol, []) if symbol else []) for w in name.split()}
    out = set()
    for w in re.findall(r"[a-z0-9$%.]+", text):
        w = w.strip(".$")
        if not w or w in own or w in FILLER:
            continue
        w = SYNONYMS.get(w, w)
        if w not in FILLER:
            out.add(w)
    return frozenset(out)


def same_story(a: frozenset, b: frozenset) -> bool:
    if not a or not b:
        return False
    return len(a & b) / len(a | b) >= STORY_OVERLAP


def story_key(symbol: str, when: float, title: str) -> str:
    """(symbol, 2-hour bucket, top 3 keywords): the cheap hash of a story. Event words rank first."""
    words = sorted(keywords(title, symbol), key=lambda w: (EVENT_WORDS.index(w) if w in EVENT_WORDS else 99, w))
    return f"{symbol}|{int(when // STORY_WINDOW)}|{'-'.join(words[:3])}"


def _source(title: str, url: str) -> str:
    """Who reported it: the publisher Google News names after the dash, otherwise the feed's site."""
    host = urllib.parse.urlsplit(url).netloc
    if "news.google." in host and " - " in title:
        return title.rsplit(" - ", 1)[1].strip().lower()
    return host


def match_symbol(title: str, symbols: list[str]) -> str | None:
    """The one symbol a general headline is about, by ticker ($NVDA, NVDA) or company name; None if not one."""
    found = set()
    for s in symbols:
        words = [s] + NAMES.get(s, [])
        # Names written all lowercase (gold, crude) match in any case; company names match exactly (Apple, not apple).
        if any(re.search(rf"(?<![A-Za-z]){re.escape(w)}(?![A-Za-z])", title, re.I if w.islower() else 0) for w in words):
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
        self.seen: dict[str, set] = {}  # headline key -> sources that carried it, insertion-ordered, bounded
        self.stories: dict[str, deque] = {}  # symbol -> recent (time, keywords, key) to catch reworded repeats
        self.merged = 0  # reworded repeats folded into a story already seen (reads saved)
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
                if s is None or not key:
                    continue
                key = self._story(s, key, title, when if when is not None else now)
                source = _source(title, url)
                sources = self.seen.get(key)
                if sources is None:
                    self.seen[key] = {source}
                    if len(self.seen) > SEEN_MAX:
                        del self.seen[next(iter(self.seen))]
                    weight, confirmed = 0.5, False  # one source so far: half strength
                elif source not in sources and len(sources) == 1:
                    sources.add(source)
                    weight, confirmed = 0.5, True  # a second source: the other half
                else:
                    continue
                with self._lock:
                    self.queue.append({"symbol": s, "kind": "live", "event": None, "value": None,
                                       "text": f"{s}: {title}", "source": source, "weight": weight,
                                       "confirmed": confirmed})
                added += 1
        self.polls += 1
        self.feeds_ok, self.feeds_failed = ok, failed
        return added

    def _story(self, symbol: str, key: str, title: str, when: float) -> str:
        """The key of the story this headline belongs to: its own, or an earlier one told in other words."""
        if key in self.seen:
            return key
        words = keywords(title, symbol)
        recent = self.stories.setdefault(symbol, deque(maxlen=200))
        for t, w, k in recent:
            if abs(when - t) <= STORY_WINDOW and k in self.seen and same_story(words, w):
                self.merged += 1
                return k
        recent.append((when, words, key))
        return key

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
                "feeds_failed": self.feeds_failed, "headlines_seen": len(self.seen),
                "confirmed": sum(1 for v in self.seen.values() if len(v) > 1), "polls": self.polls,
                "reworded_merged": self.merged,
                "last_error": self.last_error}
