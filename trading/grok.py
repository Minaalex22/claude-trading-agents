"""Grok (xAI) as an extra brain: live X/Twitter crowd reads, a second opinion on /decision, a JARVIS fast brain.

Off until XAI_API_KEY is set on Render (Keys panel on /account shows it). Nothing here can place an order.

What Grok does in the system (only the parts that suit us, from the Grok gate reel and the coordinator's plan):
1. X PULSE: every GROK_REFRESH_MINS (default 30) Grok searches X for each market (x_search tool on the xAI
   Responses API) and returns {lean -1..+1, confidence 0..1, why, posts}. A few markets per round, cached, and a
   daily call cap (GROK_DAILY_CALLS, default 150) so the bill stays small.
2. SECOND OPINION: the cached pulse is a "grok" head on the fast gate (/decision). After 30 scored calls its vote is
   weighted by its own live hit rate (1 + 4 x (hit rate - 0.5), 0.25..2): if Grok stops matching the price, it
   fades on its own.
3. NEWS PIPELINE: when a headline goes to the deep read, the X crowd's lean on that market is added as one more
   inference tag and a small part of the lean (CROWD_SHARE).
4. JARVIS: "Grok" fast mode on /jarvis answers desk questions and can search X live when asked what people say.
5. MEASURED, NOT TRUSTED: every pulse is scored GROK_SCORE_HOURS later (default 4) against the real price. The
   /api/grok panel shows the hit rate, and the replay (tools/grok_replay.py or POST /api/grok/replay) asks Grok what
   X said on each past day and checks it against the next day's move.

Env: XAI_API_KEY (required), GROK_MODEL (default grok-4.7), GROK_FAST_MODEL (JARVIS, default = GROK_MODEL),
GROK_DAILY_CALLS, GROK_REFRESH_MINS, GROK_SCORE_HOURS, GROK=off to switch it all off with the key still set.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
import urllib.request
from collections import deque
from datetime import datetime, timedelta, timezone

XAI_URL = "https://api.x.ai/v1"
DEFAULT_MODEL = "grok-4.7"
DAILY_CALLS = 150
REFRESH_MINS = 30.0
SCORE_HOURS = 4.0
PER_ROUND = 3           # markets refreshed per round, so a round never bursts the cap
MIN_CONF = 0.55         # a pulse less sure than this never votes
CROWD_SHARE = 0.2       # how much of the deep read's lean the X crowd may move
FLAT_MOVE = 0.001       # price moves smaller than 0.1% don't count as a hit or a miss

# What the X search is asked for each market. Kept as plain names so posts match how people write.
NAMES = {"BTC": "Bitcoin BTC", "ETH": "Ethereum ETH", "SOL": "Solana SOL", "XAUUSD": "gold XAUUSD",
         "GOLD": "gold", "EURUSD": "EURUSD euro dollar", "GBPUSD": "GBPUSD pound", "USDJPY": "USDJPY yen",
         "AUDUSD": "AUDUSD Aussie dollar", "SPY": "S&P 500 SPY", "QQQ": "Nasdaq QQQ", "US30": "Dow Jones",
         "NAS100": "Nasdaq 100", "SPX500": "S&P 500"}

PULSE_PROMPT = """Search X for posts from the last {hours} hours about {name} ({symbol}) as a market.
Weigh traders, analysts, newswires and official accounts above anonymous hype; ignore bots, giveaways and paid promos.
Reply with JSON only, no other text:
{{"lean": number -1..1 (-1 strongly bearish, +1 strongly bullish, 0 mixed or nothing),
 "confidence": number 0..1 (how sure, low if few or low-quality posts),
 "why": "one short sentence",
 "posts": number of relevant posts you read,
 "tag": "observation" | "inference" | "unknown"}}"""

REPLAY_PROMPT = """Search X for posts about {name} ({symbol}) as a market written on {day} (UTC) only.
Do not use anything written after that day. Reply with JSON only:
{{"lean": number -1..1, "confidence": number 0..1, "why": "one short sentence", "posts": number}}"""

X_WORDS = re.compile(r"\b(x|twitter|tweet|tweets|people saying|crowd|sentiment|chatter|trending)\b", re.I)


def enabled(env=os.environ) -> bool:
    return bool(str(env.get("XAI_API_KEY", "")).strip()) and str(env.get("GROK", "on")).lower() not in (
        "off", "0", "false", "no")


class GrokLimit(RuntimeError):
    """The daily call cap is used up."""


class GrokClient:
    """xAI Responses API (POST /v1/responses). Plain urllib, so no new package is needed."""

    def __init__(self, api_key: str, model: str = DEFAULT_MODEL, base_url: str = XAI_URL, daily_calls: int = DAILY_CALLS,
                 timeout: float = 60.0, opener=None, clock=time.time):
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.daily_calls = daily_calls
        self.timeout = timeout
        self._open = opener or urllib.request.urlopen
        self._clock = clock
        self._lock = threading.Lock()
        self.day = ""
        self.calls_today = 0
        self.x_calls_today = 0
        self.errors = 0
        self.last_error = ""

    @classmethod
    def from_env(cls, env=os.environ, model_var: str = "GROK_MODEL") -> "GrokClient | None":
        if not enabled(env):
            return None
        model = env.get(model_var) or env.get("GROK_MODEL") or DEFAULT_MODEL
        return cls(env["XAI_API_KEY"].strip(), model, env.get("XAI_BASE_URL", XAI_URL),
                   int(env.get("GROK_DAILY_CALLS", DAILY_CALLS)))

    def _spend(self) -> None:
        today = datetime.fromtimestamp(self._clock(), timezone.utc).strftime("%Y-%m-%d")
        with self._lock:
            if today != self.day:
                self.day, self.calls_today, self.x_calls_today = today, 0, 0
            if self.calls_today >= self.daily_calls:
                raise GrokLimit(f"daily cap of {self.daily_calls} Grok calls reached")
            self.calls_today += 1

    def respond(self, prompt: str, system: str | None = None, x_search: dict | None = None, history=None,
                max_turns: int = 2, max_tokens: int = 600) -> dict:
        """One question. x_search = {} (any post) or {"from_date": "YYYY-MM-DD", "to_date": ...} turns X search on."""
        self._spend()
        items = []
        if system:
            items.append({"role": "system", "content": system})
        items += list(history or [])
        items.append({"role": "user", "content": prompt})
        body = {"model": self.model, "input": items, "max_output_tokens": max_tokens}
        if x_search is not None:
            body["tools"] = [{"type": "x_search", **x_search}]
            body["max_turns"] = max_turns
        req = urllib.request.Request(f"{self.base_url}/responses", data=json.dumps(body).encode("utf-8"), method="POST",
                                     headers={"Content-Type": "application/json",
                                              "Authorization": f"Bearer {self.api_key}"})
        try:
            with self._open(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except Exception as e:  # noqa: BLE001 - network or HTTP error: counted and shown, never crashes the desk
            self.errors += 1
            self.last_error = type(e).__name__ + (f" {e.code}" if hasattr(e, "code") else "")
            raise
        text, cites = [], []
        for item in data.get("output") or []:
            if item.get("type") != "message":
                continue
            for c in item.get("content") or []:
                if c.get("type") == "output_text":
                    text.append(c.get("text") or "")
                    for a in c.get("annotations") or []:
                        url = a.get("url") or (a.get("url_citation") or {}).get("url")
                        if url:
                            cites.append(url)
        usage = data.get("usage") or {}
        tool_use = usage.get("server_side_tool_usage_details") or {}
        if x_search is not None:
            with self._lock:
                self.x_calls_today += int(tool_use.get("x_search_calls") or 0)
        return {"text": "".join(text).strip(), "citations": cites[:10], "usage": usage,
                "x_posts": int(tool_use.get("x_posts_fetched") or 0)}

    def status(self) -> dict:
        return {"model": self.model, "calls_today": self.calls_today, "daily_cap": self.daily_calls,
                "x_searches_today": self.x_calls_today, "errors": self.errors, "last_error": self.last_error}


def parse_read(text: str) -> dict | None:
    """Pull the JSON read out of Grok's reply and clamp it. None if there is no usable JSON."""
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        return None
    try:
        raw = json.loads(m.group(0))
        lean = max(-1.0, min(1.0, float(raw.get("lean", 0))))
        conf = max(0.0, min(1.0, float(raw.get("confidence", 0))))
    except (ValueError, TypeError, AttributeError):
        return None
    tag = raw.get("tag") if raw.get("tag") in ("observation", "inference", "unknown") else (
        "unknown" if conf < MIN_CONF else "inference")
    return {"lean": round(lean, 3), "confidence": round(conf, 3), "why": str(raw.get("why") or "")[:200],
            "posts": int(raw.get("posts") or 0) if str(raw.get("posts") or "0").isdigit() else 0, "tag": tag}


def market_name(symbol: str) -> str:
    return NAMES.get(symbol.upper(), symbol)


class XPulse:
    """Grok's live read of X for each market, refreshed in rounds and scored against the real price later."""

    def __init__(self, client: GrokClient | None, refresh_mins: float = REFRESH_MINS, score_hours: float = SCORE_HOURS,
                 per_round: int = PER_ROUND):
        self.client = client
        self.refresh_s = refresh_mins * 60
        self.score_s = score_hours * 3600
        self.per_round = per_round
        self.pulses: dict[str, dict] = {}
        self.pending: deque[dict] = deque(maxlen=2000)   # pulses waiting to be scored
        self.scored: deque[dict] = deque(maxlen=500)
        self.replay: dict = {"state": "not run"}
        self._lock = threading.Lock()

    @classmethod
    def from_env(cls, env=os.environ) -> "XPulse":
        return cls(GrokClient.from_env(env), float(env.get("GROK_REFRESH_MINS", REFRESH_MINS)),
                   float(env.get("GROK_SCORE_HOURS", SCORE_HOURS)))

    @property
    def on(self) -> bool:
        return self.client is not None

    def read(self, symbol: str, now: float, price: float | None = None) -> dict | None:
        """Ask Grok about one market now. Returns the pulse, or None on an error or the daily cap."""
        if not self.client:
            return None
        hours = max(1, round(self.refresh_s / 3600 * 4))
        try:
            r = self.client.respond(PULSE_PROMPT.format(hours=hours, name=market_name(symbol), symbol=symbol), x_search={})
        except Exception:  # noqa: BLE001 - errors are counted on the client
            return None
        p = parse_read(r["text"])
        if p is None:
            return None
        p.update(symbol=symbol, at=now, price=price, citations=r["citations"][:3], x_posts=r["x_posts"])
        with self._lock:
            self.pulses[symbol] = p
            if price:
                self.pending.append(dict(p))
        return p

    def due(self, symbols, now: float) -> list[str]:
        """Markets whose pulse is missing or older than the refresh time, oldest first, a few per round."""
        with self._lock:
            ages = [(self.pulses.get(s, {}).get("at") or 0, s) for s in symbols]
        return [s for at, s in sorted(ages) if now - at >= self.refresh_s][: self.per_round]

    def round(self, prices: dict, now: float) -> int:
        """One refresh round (called by the dashboard's background loop). Returns how many markets were read."""
        self.score(prices, now)
        n = 0
        for sym in self.due(list(prices), now):
            if self.read(sym, now, prices.get(sym)) is not None:
                n += 1
        return n

    def lean(self, symbol: str, now: float | None = None, max_age_s: float | None = None) -> float | None:
        """The pulse's vote: lean x confidence when sure enough and fresh enough, else None (abstain)."""
        p = self.pulses.get(symbol)
        if not p or p["confidence"] < MIN_CONF:
            return None
        if now is not None and now - p["at"] > (max_age_s or self.refresh_s * 3):
            return None
        return round(p["lean"] * p["confidence"], 3)

    def weight(self) -> float:
        """Self-weighting from the live score, same formula as the fast gate's heads."""
        st = self.stats()
        if st["checked"] < 30:
            return 1.0
        return max(0.25, min(2.0, 1 + 4 * (st["hit_rate"] - 0.5)))

    def votes(self, symbols, now: float | None = None) -> dict:
        """The /decision head's input for each market: lean x confidence x self-weight (None = abstain)."""
        w = self.weight()
        out = {}
        for s in symbols:
            v = self.lean(s, now)
            out[s] = None if v is None else max(-1.0, min(1.0, round(v * w, 3)))
        return out

    def score(self, prices: dict, now: float) -> None:
        """Check pulses older than the score time against today's price: did the market go Grok's way?"""
        with self._lock:
            while self.pending and now - self.pending[0]["at"] >= self.score_s:
                p = self.pending.popleft()
                px = prices.get(p["symbol"])
                if not px or not p.get("price") or p["confidence"] < MIN_CONF or abs(p["lean"]) < 0.1:
                    continue
                move = px / p["price"] - 1
                if abs(move) < FLAT_MOVE:
                    continue
                self.scored.append({"symbol": p["symbol"], "lean": p["lean"], "confidence": p["confidence"],
                                    "move": round(move, 5), "hit": (move > 0) == (p["lean"] > 0), "at": p["at"]})

    def stats(self) -> dict:
        rows = list(self.scored)
        hits = sum(r["hit"] for r in rows)
        sure = [r for r in rows if r["confidence"] >= 0.75]
        return {"checked": len(rows), "hits": hits, "hit_rate": round(hits / len(rows), 3) if rows else None,
                "sure_checked": len(sure),
                "sure_hit_rate": round(sum(r["hit"] for r in sure) / len(sure), 3) if sure else None,
                "waiting": len(self.pending), "score_hours": self.score_s / 3600}

    def snapshot(self) -> dict:
        with self._lock:
            pulses = sorted(self.pulses.values(), key=lambda p: -p["at"])
        return {"on": self.on, "client": self.client.status() if self.client else None,
                "pulses": pulses[:40], "live_score": self.stats(), "weight": round(self.weight(), 3), "replay": self.replay,
                "how": "Set XAI_API_KEY on Render to switch Grok on." if not self.on else
                       "Grok reads X for each market every few minutes; its calls are scored against the price later."}


# ---------------------------------------------------------------- replay: did X's mood call the next day?

def replay(client: GrokClient, closes: dict[str, list[tuple[float, float]]], days: int = 20,
           max_calls: int = 60, sleep_s: float = 0.0) -> dict:
    """For each market and each of the last `days` days, ask Grok what X said that day only, then compare its lean
    with the next day's close-to-close move. closes = {symbol: [(unix time, close), ...]} daily bars, oldest first.
    Returns hit rates overall, for sure reads (conf >= 0.75) and per market. Costs one call per market-day."""
    rows, calls = [], 0
    for sym, series in closes.items():
        series = sorted(series)[-(days + 1):]
        for (t0, c0), (_t1, c1) in zip(series, series[1:]):
            if calls >= max_calls:
                break
            day = datetime.fromtimestamp(t0, timezone.utc).date()
            calls += 1
            try:
                r = client.respond(REPLAY_PROMPT.format(name=market_name(sym), symbol=sym, day=day.isoformat()),
                                   x_search={"from_date": day.isoformat(),
                                             "to_date": (day + timedelta(days=1)).isoformat()})
            except Exception:  # noqa: BLE001
                continue
            p = parse_read(r["text"])
            if sleep_s:
                time.sleep(sleep_s)
            if p is None or abs(p["lean"]) < 0.1 or p["confidence"] < MIN_CONF:
                continue
            move = c1 / c0 - 1
            if abs(move) < FLAT_MOVE:
                continue
            rows.append({"symbol": sym, "day": day.isoformat(), "lean": p["lean"], "confidence": p["confidence"],
                         "move": round(move, 5), "hit": (move > 0) == (p["lean"] > 0)})
    return summarise_replay(rows, calls)


def summarise_replay(rows: list[dict], calls: int) -> dict:
    def rate(rs):
        return round(sum(r["hit"] for r in rs) / len(rs), 3) if rs else None

    by_sym = {}
    for r in rows:
        by_sym.setdefault(r["symbol"], []).append(r)
    sure = [r for r in rows if r["confidence"] >= 0.75]
    # "Edge" P&L: 1 unit with Grok's lean each day, so a good hit rate on tiny moves can't hide big misses.
    pnl = sum(r["move"] if r["lean"] > 0 else -r["move"] for r in rows)
    return {"state": "done", "calls": calls, "votes": len(rows), "hit_rate": rate(rows), "sure_votes": len(sure),
            "sure_hit_rate": rate(sure), "follow_pnl_pct": round(pnl * 100, 2),
            "by_symbol": {s: {"votes": len(rs), "hit_rate": rate(rs)} for s, rs in sorted(by_sym.items())},
            "verdict": verdict(rate(rows), len(rows)), "rows": rows[-60:]}


def verdict(hit_rate: float | None, n: int) -> str:
    if hit_rate is None or n < 20:
        return "not enough votes yet to tell"
    if hit_rate >= 0.55:
        return "helps: keep the grok head on"
    if hit_rate <= 0.45:
        return "points the wrong way: the fast gate will shrink its weight"
    return "coin flip: no edge so far"


# ---------------------------------------------------------------- JARVIS fast brain

class GrokMini:
    """Mini-JARVIS on Grok. Same desk snapshot as the Claude fast mode; searches X live when the question asks
    what people are saying."""

    name = "mini-grok"

    def __init__(self, client: GrokClient):
        self.client = client
        self.model = client.model

    def answer(self, question: str, ctx: dict, history=None) -> str:
        from . import jarvis_mini, project_knowledge

        system = (jarvis_mini.SYSTEM_PROMPT + "\n\nPROJECT KNOWLEDGE\n" + project_knowledge.search_text(question)
                  + "\n\nLIVE DESK SNAPSHOT\n" + jarvis_mini._compact(ctx))
        hist = [{"role": m["role"], "content": m["content"] if isinstance(m.get("content"), str) else str(m.get("content"))}
                for m in list(history or [])[-jarvis_mini.HISTORY_TURNS * 2:] if m.get("role") in ("user", "assistant")]
        try:
            r = self.client.respond(question, system=system, history=hist,
                                    x_search={} if X_WORDS.search(question) else None, max_tokens=jarvis_mini.MAX_TOKENS)
        except GrokLimit:
            return "Grok has used today's call cap. Switch fast mode to Claude or ask me tomorrow."
        except Exception:  # noqa: BLE001
            return "I couldn't reach Grok just now. Try again in a minute."
        return r["text"] or "I don't have an answer for that yet."
