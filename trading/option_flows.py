"""Option Big Flows: big opening option trades (SPX 0DTE whales) as a leading signal for SPY, QQQ and Big Tech.

Decodes each print into the direction it bets on the underlying. Puts are INVERTED (the trap in the reel):

  CALL buy to open  -> bullish      PUT buy to open  -> bearish
  CALL sell to open -> bearish      PUT sell to open -> bullish
  anything *to close -> ignored (an unwind, not a new bet)

Prints below the premium floor ($3M by default), not 0DTE, or a call and a put on the same strike at once
(a straddle: no direction) are ignored. A print near a key level counts in full; one floating in no-man's-land
counts 0.35x. Strength grows with premium ($3M = 0.8, $5M+ = 1.0).

`FEED` keeps a time-decayed ledger (35-minute half-life, gone after 3 hours). The desk reads `FEED.bias(symbol)`:
SPX prints count 0.85x for SPY, QQQ and Big Tech; a print on the symbol itself counts in full.

  - hooks.against_whale blocks a new position that fights a $3M+ print at a key level (bias -0.35 or worse)
  - desk boost: when the whale bias (|bias| >= 0.45) agrees with the desk's call, score x1.25 (x1.12 when not at
    a key level)
  - /decision: the "order flow" panel shows the whale bias
  - POST /api/option-flow takes JSON prints or pasted DeepCharts lines (see `parse_line`)

Live source: trading/whale_bridge.py polls Unusual Whales once a minute in US hours (UW_API_TOKEN on Render).
Prints can also be sent by webhook or pasted. Every print is appended to $EVENTS_DIR/whale_prints.jsonl, which the
engine processes read (`FEED.source`), so the desks see prints that arrive in the web process. History for a backtest needs CBOE/OPRA option trades (e.g. Polygon's options trades) with premium,
side and open/close; our free price history has none, so the backtest can't measure this signal.
Switch: env OPTION_FLOWS (default on).
"""

from __future__ import annotations

import json
import math
import re
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path

OPTION_FLOWS = True
MIN_PREMIUM = 3_000_000.0
MAX_DTE = 0
KEY_LEVEL_PCT = 0.0025  # within 0.25% of a key level
HALF_LIFE_MINS = 35.0
MAX_AGE_MINS = 180.0
KEY_LEVEL_FRESH_MINS = 45.0
SPX_SPILL = 0.85
VETO_BIAS, VETO_AGAINST = 0.45, 0.35
BOOST_BIAS, BOOST_KEY, BOOST_OFF_KEY = 0.45, 1.25, 1.12

# SPX flow steers the S&P and the Nasdaq names it is made of. CFD names for the same indices are included.
FOLLOWERS = {"SPX", "SPY", "QQQ", "US500", "SPX500", "NAS100", "US100", "USTEC",
             "NVDA", "AAPL", "MSFT", "AMZN", "META", "GOOGL", "GOOG", "TSLA", "AMD"}


@dataclass(frozen=True)
class FlowPrint:
    underlying: str
    option_type: str          # "CALL" or "PUT"
    action: str               # "BUY_TO_OPEN" | "SELL_TO_OPEN" | "BUY_TO_CLOSE" | "SELL_TO_CLOSE"
    premium: float
    dte: int = 0
    strike: float = 0.0
    volume: int = 0
    open_interest: int = 0
    spot: float = 0.0
    key_level: float | None = None
    at_key_level: bool | None = None  # when the sender already knows (e.g. "at KEY LEVEL" in a pasted line)
    straddle: bool = False
    at: float = 0.0           # unix seconds


@dataclass(frozen=True)
class Decoded:
    bias: str                 # "BULLISH" | "BEARISH" | "IGNORE"
    score: float              # -1..+1
    tier: str                 # "INSTITUTIONAL_3M+" | "WHALE_1M+" | "NOISE"
    at_key_level: bool
    reason: str


def decode(p: FlowPrint, min_premium: float | None = None, max_dte: int | None = None) -> Decoded:
    floor = MIN_PREMIUM if min_premium is None else min_premium
    if p.dte > (MAX_DTE if max_dte is None else max_dte):
        return Decoded("IGNORE", 0.0, "NOISE", False, f"{p.dte} days to expiry, not 0DTE")
    if p.premium < floor:
        return Decoded("IGNORE", 0.0, "NOISE", False, f"premium ${p.premium / 1e6:.2f}M below ${floor / 1e6:.1f}M")
    if p.straddle:
        return Decoded("IGNORE", 0.0, "NOISE", False, "call and put on the same strike: no direction")
    act = p.action.upper().replace(" ", "_")
    opening = "OPEN" in act or ("CLOSE" not in act and p.volume > p.open_interest > 0)
    if not opening:
        return Decoded("IGNORE", 0.0, "NOISE", False, "closing trade: an unwind, not a new bet")
    buy = "BUY" in act
    kind = p.option_type.upper()
    if kind == "CALL":
        direction = 1.0 if buy else -1.0
    elif kind == "PUT":
        direction = -1.0 if buy else 1.0  # inverted put polarity
    else:
        return Decoded("IGNORE", 0.0, "NOISE", False, f"unknown option type {kind!r}")
    if p.at_key_level is not None:
        at_key = p.at_key_level
    elif p.key_level and p.spot:
        at_key = abs(p.spot - p.key_level) / p.spot <= KEY_LEVEL_PCT
    else:
        at_key = False
    size = min(1.0, 0.5 + p.premium / 10_000_000)
    score = round(direction * size * (1.0 if at_key else 0.35), 4)
    tier = "INSTITUTIONAL_3M+" if p.premium >= 3_000_000 else "WHALE_1M+"
    return Decoded("BULLISH" if score > 0 else "BEARISH", score, tier, at_key,
                   f"{kind} {act.lower().replace('_', ' ')} ${p.premium / 1e6:.2f}M" + (" at key level" if at_key else ""))


_MONEY = re.compile(r"\$?\s*([\d.,]+)\s*([KMB])?", re.I)


def _money(text: str) -> float:
    m = _MONEY.search(text)
    if not m:
        return 0.0
    n = float(m.group(1).replace(",", ""))
    return n * {"K": 1e3, "M": 1e6, "B": 1e9}.get((m.group(2) or "").upper(), 1.0)


def parse_line(line: str, underlying: str = "SPX", at: float | None = None) -> FlowPrint | None:
    """One pasted DeepCharts-style line, e.g.
    "CALL - BUY TO OPEN, Volume 150, Premium $1.41M, DTE 0, Filled, Green Diamond at KEY LEVEL"
    "QQQ PUT SELL TO OPEN premium 4.2M dte 0 strike 520"
    Returns None when the line has no CALL/PUT. "Hollow" (not filled) means a closing trade in DeepCharts."""
    t = line.upper()
    kind = "CALL" if re.search(r"\bCALL\b|DIAMOND", t) else "PUT" if re.search(r"\bPUT\b|SQUARE", t) else None
    if not kind:
        return None
    side = "SELL" if re.search(r"\bSELL\b", t) else "BUY"
    phase = "CLOSE" if re.search(r"\bCLOSE\b|HOLLOW|EMPTY", t) else "OPEN"
    prem = re.search(r"PREMIUM[:\s]*([$\d.,]+\s*[KMB]?)", t)
    dte = re.search(r"DTE[:\s]*(\d+)|(\d+)\s*DTE", t)
    vol = re.search(r"VOL(?:UME)?[:\s]*([\d,]+)", t)
    strike = re.search(r"STRIKE[:\s]*([\d.]+)", t)
    sym = re.match(r"\s*([A-Z]{2,6})\b", t)
    under = sym.group(1) if sym and sym.group(1) not in ("CALL", "PUT", "BUY", "SELL") else underlying
    return FlowPrint(
        underlying=under.upper(), option_type=kind, action=f"{side}_TO_{phase}",
        premium=_money(prem.group(1)) if prem else 0.0,
        dte=int(next(g for g in dte.groups() if g)) if dte else 0,
        strike=float(strike.group(1)) if strike else 0.0,
        volume=int(vol.group(1).replace(",", "")) if vol else 0,
        at_key_level=True if re.search(r"KEY\s*LEVEL|SUPPORT|RESISTANCE", t) else None,
        straddle="STRADDLE" in t or "OVERLAP" in t,
        at=time.time() if at is None else at)


def from_json(d: dict, at: float | None = None) -> FlowPrint:
    return FlowPrint(
        underlying=str(d.get("underlying", d.get("symbol", "SPX"))).upper(),
        option_type=str(d.get("option_type", d.get("type", ""))).upper(),
        action=str(d.get("action", "BUY_TO_OPEN")).upper().replace(" ", "_"),
        premium=float(d.get("premium", d.get("premium_usd", 0.0))),
        dte=int(d.get("dte", 0)), strike=float(d.get("strike", 0.0)), volume=int(d.get("volume", 0)),
        open_interest=int(d.get("open_interest", 0)), spot=float(d.get("spot", d.get("spot_price", 0.0))),
        key_level=(float(d["key_level"]) if d.get("key_level") is not None else None),
        at_key_level=d.get("at_key_level"), straddle=bool(d.get("straddle", d.get("is_straddle_overlap", False))),
        at=float(d.get("at", time.time() if at is None else at)))


class FlowFeed:
    """Time-decayed ledger of decoded whale prints."""

    SYNC_EVERY = 15.0

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.active: list[tuple[float, str, Decoded]] = []
        self.recent: list[dict] = []  # last 30 decoded prints, shown on /decision
        self.ignored = 0
        self.source: Path | None = None  # engine processes: read prints the web process wrote
        self._offset = 0
        self._synced = 0.0

    def record(self, p: FlowPrint, folder) -> None:
        """Append a print to the shared file so the engine processes pick it up."""
        if not folder:
            return
        try:
            path = Path(folder) / "whale_prints.jsonl"
            if path.exists() and path.stat().st_size > 5_000_000:
                path.replace(path.with_suffix(".old"))
            with path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(asdict(p)) + "\n")
        except OSError:
            pass

    def follow(self, folder) -> None:
        """Read new prints from $EVENTS_DIR/whale_prints.jsonl from now on (skips what is already there)."""
        self.source = Path(folder) / "whale_prints.jsonl" if folder else None
        try:
            self._offset = self.source.stat().st_size if self.source else 0
        except OSError:
            self._offset = 0

    def sync(self, force: bool = False) -> int:
        if not self.source or (not force and time.time() - self._synced < self.SYNC_EVERY):
            return 0
        self._synced = time.time()
        try:
            size = self.source.stat().st_size
            if size < self._offset:
                self._offset = 0  # file was rotated
            with self.source.open("r", encoding="utf-8") as f:
                f.seek(self._offset)
                lines = f.readlines()
                self._offset = f.tell()
        except OSError:
            return 0
        n = 0
        for line in lines:
            try:
                self.ingest(FlowPrint(**json.loads(line)))
                n += 1
            except (ValueError, TypeError):
                continue
        return n

    def ingest(self, p: FlowPrint) -> Decoded:
        d = decode(p)
        with self.lock:
            if d.bias == "IGNORE":
                self.ignored += 1
            else:
                self.active.append((p.at, p.underlying, d))
            self.recent = ([{"at": p.at, "underlying": p.underlying, "bias": d.bias, "score": d.score,
                             "tier": d.tier, "reason": d.reason}] + self.recent)[:30]
        return d

    def bias(self, symbol: str, now: float | None = None) -> tuple[float, bool]:
        """(signed whale bias -1..+1, a fresh key-level print) for a symbol. Zero for symbols SPX doesn't steer."""
        sym = symbol.upper().split("-")[0].split("/")[0]
        if not OPTION_FLOWS or sym not in FOLLOWERS:
            return 0.0, False
        now = time.time() if now is None else now
        self.sync()
        total, key = 0.0, False
        with self.lock:
            self.active = [a for a in self.active if (now - a[0]) / 60 <= MAX_AGE_MINS]
            for at, under, d in self.active:
                weight = 1.0 if under == sym else SPX_SPILL if under == "SPX" else 0.0
                age = max(0.0, (now - at) / 60)
                if not weight or at > now:
                    continue
                total += d.score * weight * math.pow(0.5, age / HALF_LIFE_MINS)
                key = key or (d.at_key_level and age <= KEY_LEVEL_FRESH_MINS)
        return max(-1.0, min(1.0, total)), key

    def boost(self, symbol: str, score: float, now: float | None = None) -> float:
        """Desk score after whale confirmation: x1.25 at a key level (x1.12 otherwise) when the bias agrees."""
        b, key = self.bias(symbol, now)
        if score and abs(b) >= BOOST_BIAS and score * b > 0:
            return max(-1.0, min(1.0, score * (BOOST_KEY if key else BOOST_OFF_KEY)))
        return score

    def against(self, symbol: str, direction: float, now: float | None = None) -> str | None:
        """Reason to block a new position that fights a $3M+ print at a key level, else None."""
        b, key = self.bias(symbol, now)
        if key and abs(b) >= VETO_BIAS and direction * b < -VETO_AGAINST:
            return f"fights a 0DTE whale print (bias {b:+.2f})"
        return None

    def view(self, symbol: str, now: float | None = None) -> dict:
        b, key = self.bias(symbol, now)
        return {"bias": round(b, 3), "at_key_level": key, "ready": bool(b),
                "recent": [r for r in self.recent if r["underlying"] in (symbol.upper(), "SPX")][:5]}

    def clear(self) -> None:
        with self.lock:
            self.active.clear()
            self.recent.clear()
            self.ignored = 0


FEED = FlowFeed()
