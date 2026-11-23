"""Polymarket platform: real markets (read only) and a paper betting book.

- MarketFeed reads Polymarket's public Gamma API (no key, no account, GET only). If it can't be
  reached, the page shows a built-in sample so it still works, and says so.
- lean() is the agents' view of one market: a fair-odds estimate from plain, checkable rules
  (long-shot bias, momentum, spread, time to close). It is a view to score, not advice.
- PaperBook places pretend bets at the real prices, with Polymarket's taker fee, sells at the real
  bid, and pays out when the real market resolves. It also records the agents' leans and scores
  them (Brier score) once markets resolve, so we can see if the agents are any good.

Nothing here can send an order to Polymarket. Polymarket is blocked in Australia, so real money
stays locked (LIVE_TRADING_ENABLED is False in trading/platforms.py and every bet refuses live=True).
"""

from __future__ import annotations

import json
import os
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

GAMMA = "https://gamma-api.polymarket.com"
USER_AGENT = "ats-matrix-paper/1.0"
CACHE_SECONDS = 60
START_CASH = 10_000.0
MAX_BET_PCT = 0.05  # one bet is at most 5% of paper equity
MAX_OPEN = 25
MIN_BET = 1.0
DEFAULT_FEE_RATE = 0.0  # most Polymarket markets charge no fee; the API says when one does
PICK_LIMIT = 300  # agent leans kept for scoring

CATEGORY_WORDS = {  # matched on " text " with spaces around, so " war" doesn't hit "Warriors"
    "crypto": ("bitcoin", " btc", "ethereum", " eth ", "solana", "crypto", " xrp", "dogecoin", " doge", "memecoin", "stablecoin"),
    "politics": ("election", "president", "trump", "senate", "congress", "prime minister", " party", " vote", "governor",
                 "democrat", "republican", "parliament", "mayor", "nominee", "nomination", "biden", "harris"),
    "sports": (" nba", " nfl", " nhl", " mlb", "fifa", "uefa", "premier league", "champions league", " vs. ", " vs ",
               "match winner", " cup", "grand prix", " f1 ", "tennis", "us open", "australian open", "super bowl", " ufc",
               " afl", "cricket", "game 1", "game 2", "game 3", "dota", "spread:", " o/u ", "league of legends", "counter-strike"),
    "economy": (" fed ", " fed?", "interest rate", "inflation", " cpi", " gdp", "recession", "rate cut", "unemployment",
                "s&p", "nasdaq", " dow ", "stock", "earnings", " ipo", " oil", " gold ", "market cap", "tariff"),
    "tech": ("openai", " ai ", "nvidia", "apple", "google", "alphabet", "tesla", "spacex", "microsoft", " ceo", " gpt",
             "anthropic", "elon musk"),
    "world": (" war ", " war?", "ceasefire", "russia", "ukraine", "china", "israel", "iran", "nato", " un "),
}
CATEGORIES = ("all", *CATEGORY_WORDS, "other")


class Refused(PermissionError):
    """A bet the paper book will not take, with a plain reason."""


# ---------------------------------------------------------------- reading markets

def _get_json(url: str, timeout: float = 8.0):
    req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (fixed https host)
        return json.loads(resp.read() or b"null")


def _f(v, default: float | None = None) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _list(v) -> list:
    if isinstance(v, list):
        return v
    try:
        out = json.loads(v or "[]")
        return out if isinstance(out, list) else []
    except (TypeError, ValueError):
        return []


def category_of(text: str) -> str:
    t = f" {text.lower().replace(',', ' ').replace('?', ' ? ')} "
    for cat, words in CATEGORY_WORDS.items():
        if any(w in t for w in words):
            return cat
    return "other"


def parse_market(raw: dict) -> dict | None:
    """One Gamma market -> the fields the page and the book need. Only Yes/No style two-outcome markets."""
    names, prices = _list(raw.get("outcomes")), [_f(p) for p in _list(raw.get("outcomePrices"))]
    if len(names) != 2 or len(prices) != 2 or None in prices:
        return None
    yes = prices[0]
    bid, ask = _f(raw.get("bestBid")), _f(raw.get("bestAsk"))
    if bid is None or ask is None or not 0 <= bid <= ask <= 1:
        spread = _f(raw.get("spread"), 0.01) or 0.01
        bid, ask = max(0.0, yes - spread / 2), min(1.0, yes + spread / 2)
    events = raw.get("events") or [{}]
    ev = events[0] if isinstance(events[0], dict) else {}
    question = str(raw.get("question") or "")[:240]
    title = str(ev.get("title") or question)[:200]
    fee = raw.get("feeSchedule") if isinstance(raw.get("feeSchedule"), dict) else {}
    slug = str(ev.get("slug") or raw.get("slug") or "")
    return {
        "id": str(raw.get("id") or ""), "question": question, "event": title,
        "group": str(raw.get("groupItemTitle") or "")[:80],
        "category": category_of(f"{title} {question}"),
        "outcomes": [str(n)[:40] for n in names], "prices": [round(p, 4) for p in prices],
        "yes": round(yes, 4), "bid": round(bid, 4), "ask": round(ask, 4), "spread": round(ask - bid, 4),
        "last": _f(raw.get("lastTradePrice")), "change_1d": _f(raw.get("oneDayPriceChange"), 0.0) or 0.0,
        "change_1w": _f(raw.get("oneWeekPriceChange"), 0.0) or 0.0,
        "volume_24h": round(_f(raw.get("volume24hr"), 0.0) or 0.0), "volume": round(_f(raw.get("volumeNum") or raw.get("volume"), 0.0) or 0.0),
        "liquidity": round(_f(raw.get("liquidityNum") or raw.get("liquidity"), 0.0) or 0.0),
        "end": str(raw.get("endDate") or raw.get("endDateIso") or ""),
        "closed": bool(raw.get("closed")), "accepting": raw.get("acceptingOrders", True) is not False,
        "neg_risk": bool(raw.get("negRisk")),
        "fee_rate": (_f(fee.get("rate"), DEFAULT_FEE_RATE) or 0.0) if raw.get("feesEnabled") else 0.0,
        "url": f"https://polymarket.com/event/{urllib.parse.quote(slug)}" if slug else "https://polymarket.com",
    }


def resolved_outcome(m: dict) -> int | None:
    """0 if the first outcome (Yes) won, 1 if the second won, None while undecided."""
    if not m.get("closed"):
        return None
    p = m.get("prices") or []
    if len(p) == 2 and max(p) >= 0.99 and min(p) <= 0.01:
        return 0 if p[0] > p[1] else 1
    return None


def _days_left(end: str, now: float) -> float | None:
    try:
        t = datetime.fromisoformat(end.replace("Z", "+00:00"))
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        return (t.timestamp() - now) / 86400
    except (ValueError, AttributeError):
        return None


def lean(m: dict, now: float | None = None) -> dict:
    """The agents' view: fair YES odds from simple rules, the edge after costs, and the side to take (or skip)."""
    now = time.time() if now is None else now
    p, reasons, fair = m["yes"], [], m["yes"]
    # Long-shot bias: on prediction markets cheap long shots tend to be overpriced and heavy favourites
    # slightly underpriced. Move a little toward the favourite.
    if p < 0.15:
        fair -= p * 0.25
        reasons.append("long shot: these are usually overpriced")
    elif p > 0.85:
        fair += (1 - p) * 0.25
        reasons.append("strong favourite: usually a bit underpriced")
    # Momentum: a big 1-day move often keeps going a little as news spreads.
    ch = m.get("change_1d") or 0.0
    if abs(ch) >= 0.05 and 0.05 < p < 0.95:
        fair += max(-0.03, min(0.03, ch * 0.15))
        reasons.append(f"moved {ch * 100:+.0f}c today: follow a little")
    fair = min(0.99, max(0.01, fair))
    cost = m["spread"] / 2 + m.get("fee_rate", 0.0) * p * (1 - p)
    edge = fair - p
    side = "YES" if edge > 0 else "NO"
    net = abs(edge) - cost
    days = _days_left(m.get("end", ""), now)
    skip = None
    if m["liquidity"] < 5_000:
        skip = "too thin (under $5k liquidity)"
    elif m["spread"] > 0.05:
        skip = "spread too wide (over 5c)"
    elif not 0.03 <= p <= 0.97:
        skip = "price too close to 0 or 1"
    elif net <= 0.003:
        skip = "no edge after spread and fees"
    elif days is not None and days > 400:
        skip = "money locked for over a year"
    if days is not None and days > 90 and not skip:
        reasons.append(f"closes in {days:.0f} days: money tied up")
    conf = 0 if skip else min(100, round(net * 1000))
    return {"fair": round(fair, 4), "edge": round(edge, 4), "net": round(net, 4), "side": None if skip else side,
            "skip": skip, "confidence": conf, "reasons": reasons or ["price looks fair"],
            "days_left": None if days is None else round(days, 1)}


SAMPLE = [  # shown only when Polymarket can't be reached, so the page still works
    {"id": "sample-1", "question": "Will Bitcoin be above $100k on 31 Dec?", "outcomes": '["Yes","No"]', "outcomePrices": '["0.62","0.38"]',
     "bestBid": 0.61, "bestAsk": 0.63, "volume24hr": 1_250_000, "liquidity": 420_000, "endDate": "2026-12-31T23:59:00Z", "oneDayPriceChange": 0.04,
     "events": [{"title": "Bitcoin above ___ on December 31?", "slug": "bitcoin-above-on-december-31"}]},
    {"id": "sample-2", "question": "Fed rate cut at the December meeting?", "outcomes": '["Yes","No"]', "outcomePrices": '["0.71","0.29"]',
     "bestBid": 0.70, "bestAsk": 0.72, "volume24hr": 900_000, "liquidity": 610_000, "endDate": "2026-12-10T19:00:00Z", "oneDayPriceChange": 0.06,
     "events": [{"title": "Fed decision in December?", "slug": "fed-decision-in-december"}]},
    {"id": "sample-3", "question": "Will the Lakers win tonight?", "outcomes": '["Yes","No"]', "outcomePrices": '["0.45","0.55"]',
     "bestBid": 0.44, "bestAsk": 0.46, "volume24hr": 320_000, "liquidity": 85_000, "endDate": "2026-11-24T04:00:00Z", "oneDayPriceChange": -0.02,
     "events": [{"title": "NBA: Lakers vs. Celtics", "slug": "nba-lakers-celtics"}]},
    {"id": "sample-4", "question": "Will OpenAI release a new model this year?", "outcomes": '["Yes","No"]', "outcomePrices": '["0.08","0.92"]',
     "bestBid": 0.07, "bestAsk": 0.09, "volume24hr": 150_000, "liquidity": 60_000, "endDate": "2026-12-31T23:59:00Z", "oneDayPriceChange": 0.0,
     "events": [{"title": "OpenAI new model in 2026?", "slug": "openai-new-model-2026"}]},
    {"id": "sample-5", "question": "Ceasefire announced before January?", "outcomes": '["Yes","No"]', "outcomePrices": '["0.91","0.09"]',
     "bestBid": 0.90, "bestAsk": 0.92, "volume24hr": 540_000, "liquidity": 230_000, "endDate": "2026-12-31T23:59:00Z", "oneDayPriceChange": 0.01,
     "events": [{"title": "Ceasefire before 2027?", "slug": "ceasefire-before-2027"}]},
]


class MarketFeed:
    """Top Polymarket markets by 24h volume, cached for a minute. Read only."""

    def __init__(self, get=_get_json, now=time.time, limit: int = 120):
        self.get, self.now, self.limit = get, now, limit
        self.lock = threading.Lock()
        self.cache: dict = {"at": 0.0, "markets": [], "mode": "none", "error": None}
        self.by_id: dict[str, dict] = {}

    def _fetch(self) -> list[dict]:
        q = urllib.parse.urlencode({"limit": self.limit, "active": "true", "closed": "false",
                                    "order": "volume24hr", "ascending": "false"})
        raw = self.get(f"{GAMMA}/markets?{q}")
        return [m for m in (parse_market(r) for r in (raw or []) if isinstance(r, dict)) if m and m["id"]]

    def markets(self, force: bool = False) -> dict:
        with self.lock:
            if not force and self.cache["markets"] and self.now() - self.cache["at"] < CACHE_SECONDS:
                return self.cache
            try:
                got, mode, err = self._fetch(), "live", None
                if not got:
                    raise ValueError("Polymarket sent no markets")
            except Exception as e:  # noqa: BLE001 (network down or blocked: fall back, say why)
                got, mode, err = [parse_market(r) for r in SAMPLE], "sample", f"Polymarket not reachable ({type(e).__name__}); showing sample markets"
            now = self.now()
            for m in got:
                m["lean"] = lean(m, now)
                self.by_id[m["id"]] = m
            self.cache = {"at": now, "markets": got, "mode": mode, "error": err}
            return self.cache

    def market(self, market_id: str, fresh: bool = False) -> dict | None:
        """One market by id. fresh=True asks Polymarket again (used to settle bets)."""
        market_id = str(market_id)
        if fresh and not market_id.startswith("sample-") and market_id.isdigit():
            try:
                m = parse_market(self.get(f"{GAMMA}/markets/{market_id}"))
                if m:
                    with self.lock:
                        self.by_id[market_id] = m
                    return m
            except Exception:  # noqa: BLE001 (keep the last price we had)
                pass
        if market_id not in self.by_id:
            self.markets()
        return self.by_id.get(market_id)

    def view(self, query: str = "", category: str = "all", limit: int = 60) -> dict:
        c = self.markets()
        q = query.strip().lower()[:80]
        rows = [m for m in c["markets"]
                if (category in ("", "all") or m["category"] == category)
                and (not q or q in m["question"].lower() or q in m["event"].lower())]
        counts = {k: 0 for k in CATEGORIES}
        for m in c["markets"]:
            counts["all"] += 1
            counts[m["category"]] = counts.get(m["category"], 0) + 1
        picks = sorted((m for m in c["markets"] if m["lean"]["side"]), key=lambda m: -m["lean"]["net"])[:8]
        return {"mode": c["mode"], "error": c["error"], "fetched_at": round(c["at"]), "categories": counts,
                "markets": rows[:limit], "picks": picks, "source": GAMMA}


# ---------------------------------------------------------------- the paper book

def fee_per_share(price: float, rate: float) -> float:
    return rate * price * (1 - price)


class PaperBook:
    """Pretend bets on real Polymarket prices. Kept in $EVENTS_DIR/polymarket_paper.json."""

    def __init__(self, feed: MarketFeed, folder: str | Path | None = None, now=time.time):
        self.feed, self.now = feed, now
        self.path = Path(folder or os.environ.get("EVENTS_DIR") or ".") / "polymarket_paper.json"
        self.lock = threading.Lock()
        self.state = self._load()
        self.last_settle = 0.0

    def _load(self) -> dict:
        try:
            data = json.loads(self.path.read_text())
            if isinstance(data, dict) and "cash" in data:
                data.setdefault("open", []); data.setdefault("closed", []); data.setdefault("picks", {})
                data.setdefault("next_id", 1)
                return data
        except (OSError, ValueError):
            pass
        return {"cash": START_CASH, "start": START_CASH, "next_id": 1, "open": [], "closed": [], "picks": {}}

    def _save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.state))
            tmp.replace(self.path)
        except OSError:
            pass  # a read-only disk must not break the page

    @staticmethod
    def entry_price(m: dict, side: str) -> float:
        return m["ask"] if side == "YES" else round(1 - m["bid"], 4)

    @staticmethod
    def exit_price(m: dict, side: str) -> float:
        return m["bid"] if side == "YES" else round(1 - m["ask"], 4)

    def _mark(self, pos: dict) -> float:
        m = self.feed.by_id.get(pos["market_id"])
        return self.exit_price(m, pos["side"]) if m else pos["price"]

    def equity(self) -> float:
        return self.state["cash"] + sum(p["shares"] * self._mark(p) for p in self.state["open"])

    def bet(self, market_id: str, side: str, usd: float, live: bool = False) -> dict:
        if live:
            raise Refused("Real money is locked. Polymarket is blocked in Australia, so this is paper only.")
        side = str(side).upper()
        if side not in ("YES", "NO"):
            raise Refused("Pick YES or NO.")
        usd = float(usd)
        if not usd == usd or usd < MIN_BET:  # also catches NaN
            raise Refused(f"Bet at least ${MIN_BET:.0f}.")
        m = self.feed.market(market_id)
        if not m:
            raise Refused("Unknown market. Refresh the list.")
        if m["closed"] or not m["accepting"]:
            raise Refused("This market is closed.")
        price = self.entry_price(m, side)
        if not 0.01 <= price <= 0.99:
            raise Refused("Price is too close to 0 or 1 to be worth it.")
        with self.lock:
            st = self.state
            cap = round(self.equity() * MAX_BET_PCT, 2)
            if usd > cap:
                raise Refused(f"Max bet is 5% of paper money: ${cap:,.2f}.")
            if usd > st["cash"]:
                raise Refused(f"Not enough paper cash (${st['cash']:,.2f}).")
            if len(st["open"]) >= MAX_OPEN:
                raise Refused(f"Already {MAX_OPEN} open bets. Close one first.")
            fee = fee_per_share(price, m["fee_rate"])
            shares = usd / (price + fee)
            pos = {"id": st["next_id"], "market_id": m["id"], "question": m["question"], "event": m["event"],
                   "url": m["url"], "side": side, "outcome": m["outcomes"][0 if side == "YES" else 1],
                   "price": price, "shares": round(shares, 4), "cost": round(usd, 2), "fee": round(shares * fee, 4),
                   "opened": round(self.now()), "lean": (m.get("lean") or {}).get("side")}
            st["next_id"] += 1
            st["cash"] = round(st["cash"] - usd, 4)
            st["open"].append(pos)
            self._save()
            return pos

    def _close(self, pos: dict, price: float, reason: str, fee: float = 0.0) -> dict:
        proceeds = pos["shares"] * (price - fee)
        done = {**pos, "exit": round(price, 4), "proceeds": round(proceeds, 2), "pnl": round(proceeds - pos["cost"], 2),
                "closed": round(self.now()), "reason": reason}
        self.state["open"] = [p for p in self.state["open"] if p["id"] != pos["id"]]
        self.state["closed"].append(done)
        self.state["closed"] = self.state["closed"][-300:]
        self.state["cash"] = round(self.state["cash"] + proceeds, 4)
        return done

    def sell(self, pos_id: int) -> dict:
        with self.lock:
            pos = next((p for p in self.state["open"] if p["id"] == int(pos_id)), None)
            if not pos:
                raise Refused("No open bet with that number.")
            m = self.feed.market(pos["market_id"]) or {}
            if not m:
                raise Refused("Can't price this market right now. Try again in a minute.")
            price = self.exit_price(m, pos["side"])
            done = self._close(pos, price, "sold", fee_per_share(price, m.get("fee_rate", 0.0)))
            self._save()
            return done

    def record_leans(self, markets: list[dict]) -> None:
        """Remember the agents' first lean on each market so it can be scored when the market resolves."""
        with self.lock:
            picks = self.state["picks"]
            for m in markets:
                lv = m.get("lean") or {}
                if lv.get("side") and m["id"] not in picks:
                    picks[m["id"]] = {"q": m["question"][:140], "yes": m["yes"], "fair": lv["fair"], "side": lv["side"],
                                      "at": round(self.now()), "result": None}
            if len(picks) > PICK_LIMIT:
                for k in sorted(picks, key=lambda k: (picks[k]["result"] is None, picks[k]["at"]))[:len(picks) - PICK_LIMIT]:
                    del picks[k]

    def settle(self, max_checks: int = 12, every: float = 300.0) -> list[dict]:
        """Pay out bets on markets that resolved; score agent leans. Asks Polymarket at most every 5 minutes."""
        if self.now() - self.last_settle < every:
            return []
        self.last_settle = self.now()
        ids = [p["market_id"] for p in self.state["open"]]
        ids += [k for k, v in self.state["picks"].items() if v["result"] is None and k not in ids]
        paid = []
        for mid in ids[:max_checks]:
            m = self.feed.market(mid, fresh=True)
            won = resolved_outcome(m) if m else None
            if won is None:
                continue
            with self.lock:
                for pos in [p for p in self.state["open"] if p["market_id"] == mid]:
                    mine = 0 if pos["side"] == "YES" else 1
                    paid.append(self._close(pos, 1.0 if mine == won else 0.0, "market resolved"))
                pick = self.state["picks"].get(mid)
                if pick and pick["result"] is None:
                    pick["result"] = "YES" if won == 0 else "NO"
                self._save()
        return paid

    def snapshot(self) -> dict:
        with self.lock:
            st = self.state
            rows = []
            for p in st["open"]:
                mark = self._mark(p)
                value = p["shares"] * mark
                rows.append({**p, "mark": round(mark, 4), "value": round(value, 2), "pnl": round(value - p["cost"], 2),
                             "payout": round(p["shares"], 2)})
            closed = st["closed"]
            wins = [c for c in closed if c["pnl"] > 0]
            realised = sum(c["pnl"] for c in closed)
            unrealised = sum(r["pnl"] for r in rows)
            scored = [v for v in st["picks"].values() if v["result"]]
            brier_agent = brier_market = None
            if scored:
                hit = lambda v: 1.0 if v["result"] == "YES" else 0.0  # noqa: E731
                brier_agent = round(sum((v["fair"] - hit(v)) ** 2 for v in scored) / len(scored), 4)
                brier_market = round(sum((v["yes"] - hit(v)) ** 2 for v in scored) / len(scored), 4)
            right = sum(1 for v in scored if v["side"] == v["result"])
            equity = st["cash"] + sum(r["value"] for r in rows)
            return {"cash": round(st["cash"], 2), "start": st["start"], "equity": round(equity, 2),
                    "pnl": round(equity - st["start"], 2), "realised": round(realised, 2), "unrealised": round(unrealised, 2),
                    "open": rows, "closed": closed[::-1][:60], "bets": len(closed), "wins": len(wins),
                    "win_rate": round(len(wins) / len(closed) * 100, 1) if closed else None,
                    "max_bet": round(equity * MAX_BET_PCT, 2), "max_open": MAX_OPEN,
                    "agents": {"tracked": len(st["picks"]), "scored": len(scored), "right": right,
                               "hit_rate": round(right / len(scored) * 100, 1) if scored else None,
                               "brier": brier_agent, "brier_market": brier_market},
                    "live_trading": "locked"}

    def reset(self) -> dict:
        with self.lock:
            picks = self.state.get("picks", {})
            self.state = {"cash": START_CASH, "start": START_CASH, "next_id": 1, "open": [], "closed": [], "picks": picks}
            self._save()
        return self.snapshot()
