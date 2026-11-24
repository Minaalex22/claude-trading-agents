"""The reading room: one shared read of every symbol's news and chart, which every trader checks before a trade.

Two readers, one job:

- **News**: each headline's *text* ("NVDA: Inflation above forecast") is read for which way it pushes the
  stock. The built-in reader understands the wire's wording with a small finance lexicon; it never peeks
  at the simulator's hidden event codes. With Claude switched on, Claude reads each new headline text
  and says bullish or bearish, how sure it is, and why. Every wording is read once and remembered.
- **Chart**: the price history is read on two time frames (about 20 and 60 bars). A chart is "up" only
  when both frames agree, and how far price has moved against its own noise sets how sure the read is.
  With Claude switched on, Claude also reads a compact chart of every symbol every few minutes and its
  read is blended in.

The room turns both into one view per symbol: a direction from -1 (down) to 1 (up). Traders use it as a
filter (trading/agents.py `Agent._confirm`): no new position against a confident view, and practice trades
open the way the view points. That is what lifts the win rate: fewer trades fighting the market's real lean.

**Cheaper reads** (mina's cost fix):
- *Free first*: when the built-in reader is already sure of a headline (|push| >= PREFILTER_SURE: routine
  beats, misses, upgrades, plunges), its read stands and Claude is never asked. Claude only gets the unsure ones.
- *One batch per cycle*: new headlines wait up to BATCH_SECONDS so one feed refresh becomes one Claude call.
- *Prompt caching*: the long fixed instructions are cached for an hour (they are read at a tenth of the price on
  every later call), and the token counts are kept in `status()["tokens"]`.
- *Same story, other words*: a headline whose keywords match one Claude already read (trading/news_feed.py
  `keywords`) reuses that read.

Claude runs on a background thread, so a slow or failed call never holds up a tick; until Claude answers,
the built-in reads stand. Without ANTHROPIC_API_KEY (or the `anthropic` package) only the built-in reads
run. Paper only: nothing here can place an order.
"""

import json
import math
import os
import re
import threading
import time
from collections import deque
from dataclasses import dataclass

CHART_BARS = 120
FAST, SLOW = 20, 60
NEWS_KEEP = 0.97  # per tick, simulator only (no clock): how long a headline keeps moving the view
# With a clock (live prices, backtests) news fades by time instead: half its push every NEWS_HALF_LIFE minutes,
# MACRO_HALF_LIFE for macro headlines (central banks, inflation, jobs, tariffs, war), which the market digests slower.
NEWS_HALF_LIFE, MACRO_HALF_LIFE = 90.0, 240.0
MACRO_WORDS = ("rate decision", "interest rate", "central bank", "fed ", "fomc", "ecb", "rba", "boj", "inflation",
               "cpi", "jobs", "payroll", "unemployment", "gdp", "tariff", "sanction", "war ", "escalat", "ceasefire",
               "stimulus", "election", "default")
NEWS_FLOOR = 0.03  # a faded read below this counts as no news
MACRO_BOOST = 1.25  # agents' slow-brain premortem weighs a fresh macro shock 1.25x
FRESH_MINS = 90  # news younger than this is "fresh" (mina: 90 minutes)
SHOCK = 0.35  # a macro read at least this strong, while fresh, is a macro shock
CHART_SHARE = 0.6  # the view: 60% chart, 40% news
CONFIDENT = 0.25  # a view at least this strong blocks trades against it
CLAUDE_CHART_EVERY = 1800  # ticks between Claude's chart reads, all symbols in one call (15 min at 0.5 s ticks)
CLAUDE_CHART_SHARE = 0.5  # how much of the chart read is Claude's, when Claude has read it
DEFAULT_MODEL = "claude-opus-5-5"
PREFILTER_SURE = 0.5  # the built-in read is trusted on its own at or above this strength (READING_ROOM_PREFILTER=off)
BATCH_SECONDS = 20.0  # new headlines wait this long so they go to Claude in one call
CACHE_TTL = "1h"  # feeds refresh every 5 minutes: a 5-minute cache would just miss

# The built-in reader's finance lexicon: how the wire's phrases push a stock market. Positive is bullish.
PHRASES = {
    "too close to call": -0.6, "clear majority": 0.4, "tariffs": -0.8, "trade deal": 0.7, "sanctions": -0.5,
    "stimulus": 0.6, "escalates": -0.9, "ceasefire": 0.6, "tax cut": 0.5, "crackdown": -0.5,
}
# Data releases: the sign says whether a beat is good for stocks (hot inflation is bad, strong jobs good).
RELEASES = {
    "inflation": -0.7, "rate decision": -0.9, "jobs": 0.6, "gdp": 0.7, "unemployment": -0.6, "pmi": 0.5,
    "consumer confidence": 0.4, "retail sales": 0.4,
}
# Everyday market-news words, for real headlines (MARKET=live). Read only when the wire's own phrases
# above don't match. Whole words with their -s/-es/-d/-ed/-ing endings; matches add up (capped at -1..1).
MARKET_WORDS = {
    "beat": 0.6, "top estimates": 0.6, "record high": 0.5, "all-time high": 0.5, "surge": 0.6, "soar": 0.6,
    "jump": 0.5, "rally": 0.5, "rallie": 0.5, "climb": 0.3, "gain": 0.3, "upgrade": 0.5, "raise guidance": 0.7,
    "raise forecast": 0.6, "buyback": 0.4, "approval": 0.5, "approve": 0.5, "partnership": 0.3, "inflow": 0.4,
    "bullish": 0.4,
    "miss": -0.6, "plunge": -0.7, "tumble": -0.6, "slump": -0.6, "sink": -0.5, "fall": -0.3, "drop": -0.3,
    "downgrade": -0.5, "cut guidance": -0.7, "lower forecast": -0.6, "lawsuit": -0.4, "sue": -0.4,
    "probe": -0.4, "investigation": -0.4, "recall": -0.4, "hack": -0.7, "layoff": -0.3, "bankruptcy": -0.9,
    "ban": -0.5, "outflow": -0.4, "bearish": -0.4, "selloff": -0.5, "sell-off": -0.5, "crash": -0.8,
    "fraud": -0.8, "slide": -0.4, "scandal": -0.5, "doom": -0.5,
}


@dataclass
class View:
    direction: float = 0.0  # -1 down .. 1 up
    chart: float = 0.0
    news: float = 0.0
    claude_chart: float | None = None
    why: str = ""
    news_age_mins: float | None = None  # minutes since the last headline moved the read (ticks in the simulator)
    is_macro_shock: bool = False  # a fresh, strong macro headline is driving the news read

    @property
    def confident(self) -> bool:
        return abs(self.direction) >= CONFIDENT


def read_headline(text: str) -> float | None:
    """The built-in news reader: -1 bearish .. 1 bullish from the headline's words, None if it can't tell."""
    t = text.split(":", 1)[-1].lower()
    for phrase, push in PHRASES.items():
        if phrase in t:
            return push
    for release, push in RELEASES.items():
        if release in t:
            if "above" in t or "beat" in t or "hike" in t:
                return push
            if "below" in t or "miss" in t or "cut" in t:
                return -push
    hits = [push for word, push in MARKET_WORDS.items() if re.search(rf"(?<![a-z-]){re.escape(word)}(?:s|es|d|ed|ing)?(?![a-z])", t)]
    return max(-1.0, min(1.0, sum(hits))) if hits else None


def is_macro(text: str) -> bool:
    t = f" {text.split(':', 1)[-1].lower()} "
    return any(w in t for w in MACRO_WORDS)


def opposing_news(view, direction: float) -> bool:
    """Fresh (under FRESH_MINS) or macro-shock news pushing hard (SHOCK) against a trade in `direction`."""
    if view is None or not direction:
        return False
    against = view.news * (1.0 if direction > 0 else -1.0) <= -SHOCK
    fresh = view.news_age_mins is not None and view.news_age_mins < FRESH_MINS
    return against and (fresh or view.is_macro_shock)


def read_chart(prices: list[float]) -> float:
    """The built-in chart reader: -1 clear downtrend .. 1 clear uptrend, 0 when the time frames disagree."""
    if len(prices) < SLOW + 1:
        return 0.0
    rets = [math.log(b / a) for a, b in zip(prices[-SLOW - 1:], prices[-SLOW:])]
    vol = (sum(r * r for r in rets) / len(rets)) ** 0.5 or 1e-9
    fast = math.log(prices[-1] / prices[-FAST - 1]) / (vol * FAST ** 0.5)
    slow = math.log(prices[-1] / prices[-SLOW - 1]) / (vol * SLOW ** 0.5)
    if fast * slow <= 0:
        return 0.0  # the frames disagree: no clear trend
    return math.copysign(min(1.0, (abs(fast) + abs(slow)) / 4), fast)


class ReadingRoom:
    def __init__(self, symbols: list[str], claude=None):
        self.symbols = list(symbols)
        self.claude = claude  # a ClaudeReader, or None for the built-in reads only
        self.history = {s: deque(maxlen=CHART_BARS) for s in self.symbols}
        self.news = {s: 0.0 for s in self.symbols}
        self.views = {s: View() for s in self.symbols}
        self.ticks = 0
        self.now: float | None = None
        self.news_at: dict[str, float | None] = {s: None for s in self.symbols}  # when news last moved each read
        self.macro = {s: False for s in self.symbols}
        self.read = {"headlines": 0, "by_claude": 0, "unread": 0, "claude_charts": 0, "free": 0, "deduped": 0}
        self.prefilter = True  # the engine turns it off with READING_ROOM_PREFILTER=off
        from .ops_guard import HeadlineBatchDeduplicator
        self.deduper = HeadlineBatchDeduplicator()

    def update(self, prices: dict[str, float], headlines: list[dict] | None = None,
               now: float | None = None) -> dict[str, View]:
        self.ticks += 1
        dt = (now - self.now) / 60 if now is not None and self.now is not None and now > self.now else None
        clock = now if now is not None else float(self.ticks)
        self.now = now if now is not None else self.now
        for s in self.symbols:
            if now is None:
                self.news[s] *= NEWS_KEEP
            elif dt:
                self.news[s] *= 0.5 ** (dt / (MACRO_HALF_LIFE if self.macro[s] else NEWS_HALF_LIFE))
                if abs(self.news[s]) < NEWS_FLOOR:  # faded out: clear it and the macro flag
                    self.news[s] = 0.0
                    self.macro[s] = False
            if s in prices:
                self.history[s].append(prices[s])
        for h in headlines or ():
            s = h.get("symbol")
            if s not in self.news:
                continue
            push, by_claude = self._read_news(h["text"])
            self.read["headlines"] += 1
            if push is None:
                self.read["unread"] += 1
                continue
            self.read["by_claude"] += by_claude
            # Real headlines carry a weight: half on the first source, the rest once a second confirms it.
            self.news[s] = max(-1.0, min(1.0, self.news[s] + push * float(h.get("weight", 1.0))))
            self.news_at[s] = clock
            self.macro[s] = is_macro(h["text"])
        if self.claude and self.ticks % CLAUDE_CHART_EVERY == 0:
            self.claude.request_charts({s: list(self.history[s]) for s in self.symbols})
        claude_charts = self.claude.charts() if self.claude else {}
        for s in self.symbols:
            v = self.views[s]
            v.chart = read_chart(list(self.history[s]))
            v.news = self.news[s]
            at = self.news_at[s]
            v.news_age_mins = None if at is None else (clock - at) / (60 if now is not None else 1)
            v.is_macro_shock = bool(self.macro[s] and v.news_age_mins is not None and v.news_age_mins <= FRESH_MINS
                                    and abs(v.news) >= SHOCK)
            chart = v.chart
            c = claude_charts.get(s)
            if c is not None:
                v.claude_chart = c["direction"]
                v.why = " ".join(x for x in (c.get("pattern", ""), c.get("why", "")) if x and x != "none")
                chart = (1 - CLAUDE_CHART_SHARE) * chart + CLAUDE_CHART_SHARE * c["direction"]
            v.direction = max(-1.0, min(1.0, CHART_SHARE * chart + (1 - CHART_SHARE) * v.news))
        self.read["claude_charts"] = len(claude_charts)
        return self.views

    def _read_news(self, text: str) -> tuple[float | None, bool]:
        if self.claude and self.prefilter:
            built = read_headline(text)
            if built is not None and abs(built) >= PREFILTER_SURE:
                self.read["free"] += 1  # a routine headline: the word list is sure, no Claude tokens spent
                return built, False
        if self.claude:
            # mina's master pipeline: each story goes to Claude once per 2-hour bucket (trading/ops_guard.py).
            if not self.deduper.filter_unique_for_claude([{"text": text, "symbol": text.split(":", 1)[0]}]):
                self.read["deduped"] += 1
                return self.claude.headline(text) if self.claude.known(text) else read_headline(text), False
            read = self.claude.headline(text)
            if read is not None:
                return read, True
        return read_headline(text), False

    def snapshot(self) -> dict:
        return {"reader": self.claude.status() if self.claude else {"claude": False},
                "read": dict(self.read),
                "views": {s: {"direction": round(v.direction, 3), "chart": round(v.chart, 3),
                              "news": round(v.news, 3),
                              "news_age_mins": None if v.news_age_mins is None else round(v.news_age_mins, 1),
                              "is_macro_shock": v.is_macro_shock,
                              "claude_chart": None if v.claude_chart is None else round(v.claude_chart, 3),
                              "why": v.why} for s, v in self.views.items()}}


# ---- Claude -------------------------------------------------------------------------------------------

NEWS_SYSTEM = (
    "You are the news desk of a paper-trading team. For each headline, say how it moves the stock or coin it "
    "names over the next hours: direction -1 (clearly bearish) to 1 (clearly bullish); the size is the strength, "
    "0 if it doesn't matter; and one short reason. Judge like an experienced macro trader: hot inflation and rate hikes are bearish, "
    "strong growth and trade deals bullish, a surprise matters more than a scheduled event. "
    "Think like a superforecaster: FIRST give the base rate (how often a headline like this really moves the "
    "price over the next hours, 0..1), THEN a pre-mortem (assume your read is wrong: why?), and only then the "
    "direction; a low base rate or a strong pre-mortem means a small direction. Examples: 'CPI hotter than "
    "forecast' -> base_rate 0.7, premortem 'already priced in after the leak', direction -0.6. 'CEO to speak at "
    "conference' -> base_rate 0.1, premortem 'routine appearance', direction 0.")
NEWS_GUIDE = (
    "\n\nDESK GUIDE (fixed; applies to every batch).\n"
    "You only see the headlines our free word-list reader could not settle, so expect ambiguous, macro and "
    "geopolitical stories rather than routine earnings beats and misses.\n"
    "Scale: 0.8 to 1.0 only for a large genuine surprise that changes the outlook (surprise rate decision, war "
    "breaking out or ending, a default, a major exchange hack). 0.4 to 0.7 for a clear surprise of normal size "
    "(CPI or payrolls well away from forecast, guidance cut, big regulatory decision). 0.1 to 0.3 for a mild "
    "tilt. 0 for opinion pieces, previews, lists, 'what to watch', interviews, price recaps of moves that "
    "already happened, and anything the headline does not tie to the named market.\n"
    "Asset rules: stocks and indices like growth, lower yields, trade deals and stimulus; they dislike hot "
    "inflation, hawkish central banks, tariffs, sanctions and war. Gold rises on fear, falling real yields and "
    "a weaker dollar. Oil rises on supply cuts, OPEC restraint and Middle East risk; falls on demand worries. "
    "The Australian and Canadian dollars follow risk appetite and commodities (iron ore, oil) and their own "
    "central banks. Bitcoin and other coins react to ETF flows, regulation, exchange failures and hacks, and "
    "to global risk appetite.\n"
    "Repeats: if two headlines in the batch tell the same story, give them the same direction; never count a "
    "story twice by making one of them stronger.\n"
    "Rumours: a single unnamed source, 'reportedly', 'could', 'considers' or 'in talks' halves the size.\n"
    "Old news: a headline about something announced days ago is mostly priced in; keep it small.\n"
    "Keep each reason under 15 words and plain. Answer every id you were given, in any order.")
CHART_SYSTEM = (
    "You are the chart reader of a paper-trading team. For each symbol you get its recent closes (oldest "
    "first, sampled evenly) and its volatility. Read the chart like a seasoned technician: trend on the short "
    "and long view, higher highs and lows, breakouts, exhaustion. Give direction -1 (clear downtrend) to 1 "
    "(clear uptrend), 0 when there is no clear trend, the nearest support and resistance prices, the pattern you "
    "see (e.g. breakout, double top, range, flag, none) and one short reason. Be calibrated: most charts are "
    "noise, so most directions should be small. FIRST say the market type (trending or ranging: a trend reading "
    "in a ranging chart is the most common losing trade on our desk), THEN a pre-mortem (assume the read fails: "
    "why?), then the direction.")

NEWS_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["reads"], "properties": {
    "reads": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                                         "required": ["id", "base_rate", "premortem", "direction", "why"],
                                         "properties": {"id": {"type": "integer"}, "base_rate": {"type": "number"},
                                                        "premortem": {"type": "string"},
                                                        "direction": {"type": "number"},
                                                        "why": {"type": "string"}}}}}}
CHART_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["reads"], "properties": {
    "reads": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                                         "required": ["symbol", "market_type", "premortem", "direction",
                                                      "support", "resistance", "pattern", "why"],
                                         "properties": {"symbol": {"type": "string"},
                                                        "market_type": {"type": "string",
                                                                        "enum": ["trending", "ranging"]},
                                                        "premortem": {"type": "string"},
                                                        "direction": {"type": "number"},
                                                        "support": {"type": "number"},
                                                        "resistance": {"type": "number"},
                                                        "pattern": {"type": "string"},
                                                        "why": {"type": "string"}}}}}}


BREAKER_SECONDS = 600


def validate(data, schema: dict) -> str | None:
    """Check a Claude answer against its schema and our business rules. Returns the problem in words, or None."""
    if not isinstance(data, dict) or "_unparsed" in data:
        return "the answer was not a JSON object"
    reads = data.get("reads")
    if not isinstance(reads, list):
        return "missing the 'reads' list"
    item = schema["properties"]["reads"]["items"]
    for i, r in enumerate(reads):
        if not isinstance(r, dict):
            return f"read {i} is not an object"
        missing = [k for k in item["required"] if k not in r]
        if missing:
            return f"read {i} is missing {', '.join(missing)}"
        for k, spec in item["properties"].items():
            if spec.get("type") == "number":
                v = r.get(k)
                if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
                    return f"read {i}: {k} must be a finite number"
        if not -1.0 <= r["direction"] <= 1.0:
            return f"read {i}: direction {r['direction']} is outside -1..1"
        if "base_rate" in r and not 0.0 <= r["base_rate"] <= 1.0:
            return f"read {i}: base_rate {r['base_rate']} is outside 0..1"
    return None


def _sample(prices: list[float], points: int = 30) -> list[float]:
    if len(prices) <= points:
        return [round(p, 3) for p in prices]
    step = (len(prices) - 1) / (points - 1)
    return [round(prices[round(i * step)], 3) for i in range(points)]


class ClaudeReader:
    """Claude reads headline texts and charts on a background thread. Answers are cached; ticks never wait."""

    MAX_BATCH = 40

    def __init__(self, client, model: str = DEFAULT_MODEL, batch_seconds: float = BATCH_SECONDS):
        self.client = client
        self.model = model
        self.batch_seconds = batch_seconds
        self.cache: dict[str, float] = {}
        self.by_words: dict[tuple, float] = {}  # (symbol, keywords) -> read: reworded repeats reuse it
        self._first_pending = 0.0  # when the oldest waiting headline arrived
        self.tokens = {"input": 0, "cache_read": 0, "cache_write": 0, "output": 0}
        self.reused = 0
        self._pending_words: dict[str, frozenset] = {}
        self.reasons: dict[str, str] = {}
        self._pending: list[str] = []
        self._charts: dict[str, dict] = {}
        self._chart_job: dict[str, list[float]] | None = None
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self.calls = 0
        self.errors = 0
        self.last_error = ""
        self.breaker_until = 0.0
        self.breaker_trips = 0
        self._thread = threading.Thread(target=self._run, name="claude-reader", daemon=True)
        self._thread.start()

    # Called from the tick loop: never blocks.
    def headline(self, text: str) -> float | None:
        key = text.split(":", 1)[-1].strip()  # the wording, not the ticker: "Inflation above forecast"
        words = _words(text)
        with self._lock:
            if key in self.cache:
                return self.cache[key]
            if len(words) >= 2 and words in self.by_words:
                self.reused += 1  # the same story in other words: Claude already read it
                self.cache[key] = self.by_words[words]
                return self.cache[key]
            if key not in self._pending:
                if not self._pending:
                    self._first_pending = time.time()
                self._pending.append(key)
                self._pending_words[key] = words
                self._wake.set()
        return None

    def known(self, text: str) -> bool:
        """Has Claude already read this wording or the same story in other words?"""
        key = text.split(":", 1)[-1].strip()
        with self._lock:
            return key in self.cache or _words(text) in self.by_words

    def request_charts(self, histories: dict[str, list[float]]) -> None:
        with self._lock:
            self._chart_job = histories
        self._wake.set()

    def charts(self) -> dict[str, dict]:
        with self._lock:
            return dict(self._charts)

    def status(self) -> dict:
        with self._lock:
            return {"claude": True, "model": self.model, "headlines_known": len(self.cache),
                    "tokens": dict(self.tokens), "reused_reworded": self.reused,
                    "waiting": len(self._pending), "calls": self.calls, "errors": self.errors,
                    "last_error": self.last_error, "breaker_trips": self.breaker_trips,
                    "breaker_open": time.time() < self.breaker_until}

    # Background thread.
    def _run(self) -> None:
        while True:
            self._wake.wait(timeout=5)
            self._wake.clear()
            with self._lock:
                wait = self._first_pending + self.batch_seconds - time.time()
                if self._pending and len(self._pending) < self.MAX_BATCH and wait > 0 and not self._chart_job:
                    pending_wait = min(wait, 5.0)
                else:
                    pending_wait = 0.0
            if pending_wait > 0:
                time.sleep(pending_wait)  # let the rest of this feed refresh arrive: one call, not fifteen
                self._wake.set()
                continue
            with self._lock:
                batch, self._pending = self._pending[:self.MAX_BATCH], self._pending[self.MAX_BATCH:]
                charts, self._chart_job = self._chart_job, None
                if self._pending:
                    self._first_pending = 0.0  # the overflow goes straight out in the next call
                    self._wake.set()
            if batch:
                self._safely(self._read_news, batch)
            if charts:
                self._safely(self._read_charts, charts)

    def _safely(self, fn, arg) -> None:
        try:
            fn(arg)
        except Exception as e:  # a failed read must never stop the desk; built-in reads stand in
            with self._lock:
                self.errors += 1
                self.last_error = f"{type(e).__name__}: {e}"[:200]
                if fn == self._read_news:
                    for key in arg:  # remember as unreadable so it isn't retried forever
                        self.cache.setdefault(key, read_headline(key) or 0.0)

    def _ask(self, system: str, content: str, schema: dict) -> dict:
        """Ask Claude, check the answer before the desk uses it, and give Claude one chance to fix a bad one.

        From the agent-reliability guide mina shared: a validator between the model and the desk returns the
        exact error once; a second failure in a row trips the circuit breaker, and the built-in reads stand in
        until `BREAKER_SECONDS` have passed.
        """
        if time.time() < self.breaker_until:
            raise RuntimeError("circuit breaker open: built-in reads in use")
        data = self._call(system, content, schema)
        problem = validate(data, schema)
        if problem:
            data = self._call(system, f"{content}\n\nYour last answer was rejected: {problem}. Answer again.", schema)
            problem = validate(data, schema)
            if problem:
                with self._lock:
                    self.breaker_until = time.time() + BREAKER_SECONDS
                    self.breaker_trips += 1
                raise RuntimeError(f"invalid answer twice ({problem}): circuit breaker tripped")
        return data

    def _call(self, system: str, content: str, schema: dict) -> dict:
        response = self.client.beta.messages.create(
            model=self.model, max_tokens=4096,
            # The fixed instructions are cached: later calls read them at a tenth of the price.
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral", "ttl": CACHE_TTL}}],
            messages=[{"role": "user", "content": content}],
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": schema}},
            betas=["server-side-fallback-2026-07-01"], fallbacks="default")
        usage = getattr(response, "usage", None)
        with self._lock:
            self.calls += 1
            if usage is not None:
                for k, attr in (("input", "input_tokens"), ("cache_read", "cache_read_input_tokens"),
                                ("cache_write", "cache_creation_input_tokens"), ("output", "output_tokens")):
                    self.tokens[k] += int(getattr(usage, attr, 0) or 0)
        if response.stop_reason == "refusal":
            raise RuntimeError("refused")
        text = next(b.text for b in response.content if b.type == "text")
        try:
            return json.loads(text)
        except ValueError:
            return {"_unparsed": text[:80]}

    def _read_news(self, batch: list[str]) -> None:
        lines = "\n".join(f"{i}. {t}" for i, t in enumerate(batch))
        data = self._ask(NEWS_SYSTEM + NEWS_GUIDE, f"Headlines:\n{lines}", NEWS_SCHEMA)
        with self._lock:
            for r in data.get("reads", []):
                if 0 <= r.get("id", -1) < len(batch):
                    key = batch[r["id"]]
                    self.cache[key] = max(-1.0, min(1.0, float(r["direction"])))
                    self.reasons[key] = str(r.get("why", ""))[:160]
                    words = self._pending_words.pop(key, frozenset())
                    if len(words) >= 2:
                        self.by_words[words] = self.cache[key]
            for key in batch:
                self.cache.setdefault(key, read_headline(key) or 0.0)
                self._pending_words.pop(key, None)

    def _read_charts(self, histories: dict[str, list[float]]) -> None:
        rows = []
        for s, prices in histories.items():
            if len(prices) < 10:
                continue
            rets = [b / a - 1 for a, b in zip(prices, prices[1:])]
            vol = (sum(r * r for r in rets) / len(rets)) ** 0.5
            rows.append({"symbol": s, "closes": _sample(prices), "bars": len(prices),
                         "volatility_per_bar": round(vol, 5)})
        if not rows:
            return
        data = self._ask(CHART_SYSTEM, json.dumps({"charts": rows}), CHART_SCHEMA)
        with self._lock:
            for r in data.get("reads", []):
                if r.get("symbol") in histories:
                    self._charts[r["symbol"]] = {"direction": max(-1.0, min(1.0, float(r["direction"]))),
                                                 "support": r.get("support"), "resistance": r.get("resistance"),
                                                 "pattern": str(r.get("pattern", ""))[:40],
                                                 "why": str(r.get("why", ""))[:160]}


def _words(text: str) -> frozenset:
    """A headline's story keywords (ticker prefix dropped), so reworded repeats share one read."""
    from .news_feed import keywords
    symbol, _, rest = text.partition(":") if ":" in text else ("", "", text)
    return keywords(rest or text, symbol.strip() or None)


def make_reader(env=os.environ) -> ClaudeReader | None:
    """Claude when switched on (READING_ROOM_CLAUDE not "off"), a key is set and the SDK is installed."""
    if env.get("READING_ROOM_CLAUDE", "on").lower() in ("off", "0", "false", "no"):
        return None
    if not env.get("ANTHROPIC_API_KEY"):
        return None
    try:
        import anthropic
    except ImportError:
        return None
    return ClaudeReader(anthropic.Anthropic(timeout=60.0, max_retries=2), env.get("READING_ROOM_MODEL", DEFAULT_MODEL))
