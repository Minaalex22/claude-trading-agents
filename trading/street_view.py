"""Street view: what the top analysis houses in New York, London, China and
Singapore think, turned into one bullish/bearish read per market for Atlas.

Their full reports are client-only, so this reads only PUBLIC notes,
headlines and outlooks that the app is given (RSS items, free web pages,
pasted text). Each note becomes a ``Call`` (house, market, direction,
confidence). Calls are weighted by how fresh they are and by each house's
track record, which is scored later against real price moves.

Reading a note uses Claude when ANTHROPIC_API_KEY is set (and
STREET_VIEW_CLAUDE is not "off"); otherwise a built-in word list.
"""
from __future__ import annotations

import json
import os
import re
import time
from dataclasses import asdict, dataclass, field

HOUSES: list[dict] = [
    # New York
    {"id": "gs", "name": "Goldman Sachs Research", "city": "New York"},
    {"id": "ms", "name": "Morgan Stanley Research", "city": "New York"},
    {"id": "jpm", "name": "J.P. Morgan Global Research", "city": "New York"},
    {"id": "bofa", "name": "BofA Global Research", "city": "New York"},
    {"id": "citi", "name": "Citi Research", "city": "New York"},
    # London
    {"id": "barc", "name": "Barclays Research", "city": "London"},
    {"id": "hsbc", "name": "HSBC Global Research", "city": "London"},
    {"id": "stanc", "name": "Standard Chartered Global Research", "city": "London"},
    {"id": "capecon", "name": "Capital Economics", "city": "London"},
    {"id": "oxecon", "name": "Oxford Economics", "city": "London"},
    # China
    {"id": "cicc", "name": "CICC Research", "city": "China"},
    {"id": "citics", "name": "CITIC Securities Research", "city": "China"},
    {"id": "gtht", "name": "Guotai Haitong Securities Research", "city": "China"},
    {"id": "htsc", "name": "Huatai Securities Research", "city": "China"},
    {"id": "cmsc", "name": "China Merchants Securities Research", "city": "China"},
    # Singapore
    {"id": "dbs", "name": "DBS Group Research", "city": "Singapore"},
    {"id": "uob", "name": "UOB Global Economics & Markets Research", "city": "Singapore"},
    {"id": "ocbc", "name": "OCBC Global Markets Research", "city": "Singapore"},
    {"id": "maybank", "name": "Maybank Investment Banking Group Research", "city": "Singapore"},
    {"id": "cgsi", "name": "CGS International Research", "city": "Singapore"},
]
HOUSE_BY_ID = {h["id"]: h for h in HOUSES}
CITIES = ["New York", "London", "China", "Singapore"]

# Words that point to a market, so a note can be filed under the right symbol.
MARKET_WORDS: dict[str, list[str]] = {
    "SPX": ["s&p", "s&p 500", "us equities", "us stocks", "wall street", "spx"],
    "NDX": ["nasdaq", "tech stocks", "ndx"],
    "BTC": ["bitcoin", "btc", "crypto"],
    "ETH": ["ether", "ethereum", "eth"],
    "GOLD": ["gold", "xau", "bullion"],
    "OIL": ["oil", "brent", "wti", "crude"],
    "EURUSD": ["euro", "eur/usd", "eurusd"],
    "USDJPY": ["yen", "usd/jpy", "usdjpy"],
    "GBPUSD": ["sterling", "pound", "gbp/usd", "gbpusd"],
    "USD": ["dollar", "dxy", "greenback"],
    "UST": ["treasuries", "treasury yields", "us bonds", "10-year"],
    "CSI300": ["china a-shares", "a-shares", "csi 300", "csi300", "shanghai composite"],
    "HSI": ["hang seng", "hong kong stocks", "hsi"],
    "STI": ["straits times", "singapore stocks", "sti"],
    "USDCNH": ["yuan", "renminbi", "cnh", "cny"],
    "USDSGD": ["singapore dollar", "sgd"],
    "FTSE": ["ftse", "uk stocks", "uk equities"],
}

BULL = ["overweight", "upgrade", "buy", "bullish", "rally", "upside", "outperform",
        "raise target", "raised target", "higher", "strong", "recovery", "beat",
        "accumulate", "positive", "rebound", "rate cut", "easing", "stimulus"]
BEAR = ["underweight", "downgrade", "sell", "bearish", "selloff", "sell-off",
        "downside", "underperform", "cut target", "lower", "weak", "recession",
        "miss", "reduce", "negative", "slowdown", "rate hike", "tightening", "risk-off"]
# For quote currencies and bonds, "bullish dollar" means USDxxx up, etc.
# Kept simple: direction is about the named market's price.


@dataclass
class Call:
    house: str
    market: str
    direction: float          # -1 bearish .. +1 bullish
    confidence: float         # 0..1
    ts: float
    summary: str = ""
    source: str = ""
    entry_price: float | None = None
    scored: bool = False
    correct: bool | None = None


@dataclass
class HouseRecord:
    right: int = 0
    wrong: int = 0

    @property
    def hit_rate(self) -> float:
        # start every house at 50% with a small prior so one call can't swing it
        return (self.right + 2) / (self.right + self.wrong + 4)


def find_markets(text: str) -> list[str]:
    t = text.lower()
    found = []
    for market, words in MARKET_WORDS.items():
        for w in words:
            if re.search(r"(?<![a-z])" + re.escape(w) + r"(?![a-z])", t):
                found.append(market)
                break
    return found


def lexicon_read(text: str) -> tuple[float, float]:
    t = text.lower()
    def count(words):
        return sum(len(re.findall(r"(?<![a-z])" + re.escape(w) + r"(?![a-z])", t))
                   for w in words)
    b, s = count(BULL), count(BEAR)
    if b + s == 0:
        return 0.0, 0.0
    direction = (b - s) / (b + s)
    confidence = min(0.6, 0.15 * (b + s)) * abs(direction)
    return round(direction, 3), round(confidence, 3)


class ClaudeNoteReader:
    """Reads a public note with Claude into calls per market."""

    MODEL = os.environ.get("STREET_VIEW_MODEL", "claude-opus-5-5")

    def __init__(self):
        import anthropic  # only needed when the key is set
        self.client = anthropic.Anthropic()

    def read(self, text: str) -> list[dict]:
        markets = ", ".join(MARKET_WORDS)
        msg = self.client.messages.create(
            model=self.MODEL, max_tokens=800,
            system=("You read public market research notes. Return only JSON: "
                    '{"calls": [{"market": one of [' + markets + '], '
                    '"direction": number -1..1, "confidence": number 0..1, '
                    '"summary": short plain sentence}]}. '
                    "Only include markets the note clearly has a view on. "
                    "Direction is about that market's price."),
            messages=[{"role": "user", "content": text[:12000]}],
        )
        raw = "".join(getattr(b, "text", "") for b in msg.content)
        m = re.search(r"\{.*\}", raw, re.S)
        if not m:
            return []
        try:
            calls = json.loads(m.group(0)).get("calls", [])
        except json.JSONDecodeError:
            return []
        out = []
        for c in calls:
            if c.get("market") in MARKET_WORDS:
                out.append({
                    "market": c["market"],
                    "direction": max(-1.0, min(1.0, float(c.get("direction", 0)))),
                    "confidence": max(0.0, min(1.0, float(c.get("confidence", 0)))),
                    "summary": str(c.get("summary", ""))[:300],
                })
        return out


def _make_reader():
    if os.environ.get("STREET_VIEW_CLAUDE", "on").lower() == "off":
        return None
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    try:
        return ClaudeNoteReader()
    except Exception:
        return None


class StreetView:
    def __init__(self, half_life_hours: float = 72.0, reader="auto"):
        self.calls: list[Call] = []
        self.records: dict[str, HouseRecord] = {h["id"]: HouseRecord() for h in HOUSES}
        self.half_life = half_life_hours * 3600
        self.reader = _make_reader() if reader == "auto" else reader

    # ---- input ---------------------------------------------------------
    def add_note(self, house: str, text: str, ts: float | None = None,
                 source: str = "", prices: dict[str, float] | None = None) -> list[Call]:
        """File one public note from ``house``. Returns the calls it produced."""
        if house not in HOUSE_BY_ID:
            raise ValueError(f"unknown house {house!r}")
        ts = time.time() if ts is None else ts
        prices = prices or {}
        parsed: list[dict] = []
        if self.reader is not None:
            try:
                parsed = self.reader.read(text)
            except Exception:
                parsed = []
        if not parsed:
            d, c = lexicon_read(text)
            if c > 0:
                parsed = [{"market": m, "direction": d, "confidence": c,
                           "summary": text.strip()[:200]} for m in find_markets(text)]
        made = []
        for p in parsed:
            call = Call(house=house, market=p["market"], direction=p["direction"],
                        confidence=p["confidence"], ts=ts, summary=p["summary"],
                        source=source, entry_price=prices.get(p["market"]))
            self.calls.append(call)
            made.append(call)
        return made

    # ---- scoring houses ------------------------------------------------
    def score(self, prices: dict[str, float], now: float | None = None,
              after_hours: float = 24.0, min_move: float = 0.002) -> int:
        """Mark calls older than ``after_hours`` right/wrong against price moves."""
        now = time.time() if now is None else now
        n = 0
        for c in self.calls:
            if c.scored or c.entry_price is None or c.market not in prices:
                continue
            if now - c.ts < after_hours * 3600 or c.direction == 0:
                continue
            move = (prices[c.market] - c.entry_price) / c.entry_price
            if abs(move) < min_move:
                continue  # flat: don't count either way
            c.scored = True
            c.correct = (move > 0) == (c.direction > 0)
            rec = self.records[c.house]
            if c.correct:
                rec.right += 1
            else:
                rec.wrong += 1
            n += 1
        return n

    # ---- output for Atlas ----------------------------------------------
    def view(self, market: str, now: float | None = None) -> dict:
        now = time.time() if now is None else now
        num = den = 0.0
        by_city: dict[str, list[float]] = {c: [] for c in CITIES}
        latest: dict[str, Call] = {}
        for c in self.calls:
            if c.market != market:
                continue
            prev = latest.get(c.house)
            if prev is None or c.ts >= prev.ts:
                latest[c.house] = c
        for house, c in latest.items():
            age = max(0.0, now - c.ts)
            fresh = 0.5 ** (age / self.half_life)
            skill = self.records[house].hit_rate
            w = fresh * c.confidence * (skill / 0.5)
            num += w * c.direction
            den += w
            by_city[HOUSE_BY_ID[house]["city"]].append(c.direction)
        if den == 0:
            return {"market": market, "direction": 0.0, "confidence": 0.0,
                    "houses": 0, "by_city": {}, "calls": [], "ready": False}
        direction = num / den
        agree = sum(1 for c in latest.values() if c.direction * direction > 0)
        confidence = min(1.0, abs(direction) * (agree / max(1, len(latest)))
                         * min(1.0, den))
        return {
            "market": market, "direction": round(direction, 3),
            "confidence": round(confidence, 3), "houses": len(latest),
            "by_city": {k: round(sum(v) / len(v), 3) for k, v in by_city.items() if v},
            "calls": [{"house": HOUSE_BY_ID[h]["name"], "city": HOUSE_BY_ID[h]["city"],
                       "direction": c.direction, "summary": c.summary, "ts": c.ts}
                      for h, c in sorted(latest.items())],
            "ready": True,
        }

    def board(self) -> list[dict]:
        """The 20 houses with their track record and latest call, for the dashboard."""
        latest: dict[str, Call] = {}
        for c in self.calls:
            if c.house not in latest or c.ts >= latest[c.house].ts:
                latest[c.house] = c
        rows = []
        for h in HOUSES:
            rec = self.records[h["id"]]
            last = latest.get(h["id"])
            rows.append({**h, "hit_rate": round(rec.hit_rate, 3),
                         "scored_calls": rec.right + rec.wrong,
                         "latest": asdict(last) if last else None})
        return rows

    # ---- save / load ---------------------------------------------------
    def to_json(self) -> str:
        return json.dumps({
            "calls": [asdict(c) for c in self.calls],
            "records": {k: asdict(v) for k, v in self.records.items()},
        })

    def load_json(self, data: str) -> None:
        obj = json.loads(data)
        self.calls = [Call(**c) for c in obj.get("calls", [])]
        for k, v in obj.get("records", {}).items():
            if k in self.records:
                self.records[k] = HouseRecord(**v)
