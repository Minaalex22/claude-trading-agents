"""Time Machine and day-trader tools: replay a trade, scan opening-range breakouts, read Congress trades,
size the position, check it against the money rules, and write a PRACTICE ticket. PRACTICE ONLY.

The idea comes from the free "day-trader-skill" pack (11 Claude skills, MIT licence). This is our own code,
built on our money rules (trading/calculator.py) and our practice desk. Nothing here can place a real order.

How the Time Machine works:
  1. Pool: the last N FULL sessions of 5-minute bars (US shares: 78 bars, 9:30 to 4:00 New York time).
     Every bar becomes four ratios to the bar before it (open, high, low, close over the previous close).
  2. One replay: start at the entry price. For each bar left in the day, pick a past bar from the same time
     of day (give or take 2 bars) at random and scale it to the current price. The stop is checked before the
     target, so a bar that touches both counts as the stop.
  3. Thousands of replays give the odds: target first, stop first, or neither by the close (flat).
  4. History: the same plan, scaled to each past day's price at the same time, on the real bars.
It resamples the SAME few weeks of bars over and over. It is a way to see risk and reward, not a prediction:
earnings, news and halts can do things those sessions never did.
"""
from __future__ import annotations

import io
import json
import math
import random
import threading
import time
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime, timedelta, timezone

from . import calculator

USER_AGENT = "Mozilla/5.0 (compatible; ats-matrix-practice/1.0)"
BAR_MIN = 5
OPEN_MIN = 9 * 60 + 30            # 9:30 New York
STOCK_BARS = 78                   # 9:30 .. 15:55 bar starts
CRYPTO_BARS = 288                 # 24h of 5-minute bars (UTC days)
MIN_SESSIONS = 10
MAX_SESSIONS = 60
DEFAULT_RUNS = 10_000
MAX_RUNS = 20_000
DEFAULT_SEED = 19
WINDOW = 2                        # pick from the same time of day, give or take 2 bars
CACHE_SECONDS = 300
CONGRESS_CACHE_SECONDS = 3600
SCAN_SYMBOLS = ("AAPL", "MSFT", "NVDA", "AMZN", "GOOG", "TSLA", "META", "AMD", "NFLX", "AVGO",
                "PLTR", "COIN", "MSTR", "SMCI", "UBER", "SHOP", "INTC", "BA", "JPM", "SPY", "QQQ")
CLERK = "https://disclosures-clerk.house.gov/public_disc"
WARNING = ("Resamples the same past sessions again and again: it is not thousands of real trades. "
           "Earnings, news and halts can do things those sessions never did. Practice only.")


# ---------------------------------------------------------------- time and data

def _ny_offset(ts: float) -> int:
    """New York's UTC offset in seconds (US daylight saving rules), without needing tzdata installed."""
    t = datetime.fromtimestamp(ts, timezone.utc)
    y = t.year
    mar1 = datetime(y, 3, 1, tzinfo=timezone.utc)
    start = mar1 + timedelta(days=(6 - mar1.weekday()) % 7 + 7, hours=7)     # 2nd Sunday of March, 2am EST
    nov1 = datetime(y, 11, 1, tzinfo=timezone.utc)
    end = nov1 + timedelta(days=(6 - nov1.weekday()) % 7, hours=6)           # 1st Sunday of November, 2am EDT
    return -4 * 3600 if start <= t < end else -5 * 3600


def is_crypto(symbol: str) -> bool:
    return symbol.upper().endswith("-USD")


def _get(url: str, timeout: float = 12.0) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 - fixed https hosts only
        return r.read(8_000_000)


def parse_chart(chart: dict, crypto: bool = False) -> list[dict]:
    """Yahoo v8 chart JSON -> list of sessions, oldest first: {"date", "bars": [(slot, o, h, l, c)], "full"}."""
    res = (chart.get("chart") or {}).get("result") or []
    if not res:
        raise ValueError("no chart data")
    r = res[0]
    q = r["indicators"]["quote"][0]
    days: dict[str, dict[int, tuple]] = {}
    for i, ts in enumerate(r.get("timestamp") or []):
        o, h, l, c = q["open"][i], q["high"][i], q["low"][i], q["close"][i]
        if None in (o, h, l, c) or min(o, h, l, c) <= 0:
            continue
        if crypto:
            t = datetime.fromtimestamp(ts, timezone.utc)
            slot = (t.hour * 60 + t.minute) // BAR_MIN
        else:
            t = datetime.fromtimestamp(ts + _ny_offset(ts), timezone.utc)
            mins = t.hour * 60 + t.minute - OPEN_MIN
            if mins < 0 or mins % BAR_MIN:
                continue
            slot = mins // BAR_MIN
            if slot >= STOCK_BARS:
                continue
        days.setdefault(t.strftime("%Y-%m-%d"), {})[slot] = (slot, float(o), float(h), float(l), float(c))
    need = CRYPTO_BARS if crypto else STOCK_BARS
    out = []
    for d in sorted(days):
        bars = [days[d][k] for k in sorted(days[d])]
        out.append({"date": d, "bars": bars, "full": len(bars) == need and bars[-1][0] == need - 1})
    return out


def practice_sessions(symbol: str, n: int = 60, price: float = 100.0, seed: int = 0) -> list[dict]:
    """Simulated 5-minute sessions for when the live feed can't be reached (busier at the open and close)."""
    rng = random.Random(f"{symbol}:{seed}")
    need = CRYPTO_BARS if is_crypto(symbol) else STOCK_BARS
    out, p = [], price
    start = datetime(2026, 1, 5)
    for d in range(n):
        bars = []
        for k in range(need):
            u = 1.0 + 1.6 * (abs(k - need / 2) / (need / 2)) ** 3
            vol = 0.0016 * u
            o = p * (1 + rng.gauss(0, vol * 0.2))
            c = o * (1 + rng.gauss(0, vol))
            h = max(o, c) * (1 + abs(rng.gauss(0, vol * 0.5)))
            l = min(o, c) * (1 - abs(rng.gauss(0, vol * 0.5)))
            bars.append((k, o, h, l, c))
            p = c
        out.append({"date": (start + timedelta(days=d)).strftime("%Y-%m-%d"), "bars": bars, "full": True})
    return out


class Bars:
    """5-minute bars per symbol, fetched from Yahoo (60 days) and cached for 5 minutes. Practice bars if offline."""

    def __init__(self, get=None, clock=time.time):
        self._get = get or (lambda url, timeout=12.0: _get(url, timeout))   # looked up late so tests can swap it
        self._clock = clock
        self._cache: dict[str, tuple[float, list[dict], str]] = {}
        self._lock = threading.Lock()

    def sessions(self, symbol: str) -> tuple[list[dict], str]:
        symbol = symbol.upper()
        with self._lock:
            hit = self._cache.get(symbol)
            if hit and self._clock() - hit[0] < CACHE_SECONDS:
                return hit[1], hit[2]
        crypto = is_crypto(symbol)
        sess, source = None, ""
        for host in ("query1", "query2"):
            try:
                raw = self._get(f"https://{host}.finance.yahoo.com/v8/finance/chart/{symbol}?interval=5m&range=60d")
                sess = parse_chart(json.loads(raw), crypto)
                source = "live 5-minute bars (Yahoo Finance)"
                break
            except Exception:  # noqa: BLE001 - any failure falls back to practice bars
                continue
        if not sess:
            sess = practice_sessions(symbol)
            source = "practice bars (simulated: the live feed couldn't be reached)"
        with self._lock:
            self._cache[symbol] = (self._clock(), sess, source)
        return sess, source


# ---------------------------------------------------------------- the Time Machine

def slot_for(time_str: str | None, crypto: bool = False) -> int:
    """Entry time = the CLOSE of the entry bar, 'HH:MM' (New York for shares, UTC for crypto) -> bar slot."""
    if not time_str:
        return 8 if not crypto else 0                       # 10:15 close = the 10:10 bar
    hh, mm = (int(x) for x in time_str.strip().split(":")[:2])
    mins = hh * 60 + mm - (0 if crypto else OPEN_MIN)
    if mins % BAR_MIN:
        raise ValueError("entry time must be on the 5-minute grid, like 10:15")
    slot = mins // BAR_MIN - 1
    last = (CRYPTO_BARS if crypto else STOCK_BARS) - 1
    if not 0 <= slot < last:
        raise ValueError("entry time must be inside the session, leaving at least one bar to play out")
    return slot


def _ratios(sessions: list[dict]) -> dict[int, list[tuple]]:
    pool: dict[int, list[tuple]] = {}
    for s in sessions:
        bars = s["bars"]
        for prev, b in zip(bars, bars[1:]):
            pc = prev[4]
            pool.setdefault(b[0], []).append((b[1] / pc, b[2] / pc, b[3] / pc, b[4] / pc))
    return pool


def _walk(path, entry: float, stop: float, target: float, sign: int) -> tuple[str, float]:
    """Walk bars (h, l, c) from the entry. Stop is checked first. Returns (outcome, last price)."""
    price = entry
    for h, l, c in path:
        if (l <= stop) if sign > 0 else (h >= stop):
            return "stop", stop
        if (h >= target) if sign > 0 else (l <= target):
            return "target", target
        price = c
    return "flat", price


def verdict(target_pct: float, stop_pct: float, rr: float) -> str:
    decided = target_pct + stop_pct
    if decided <= 0:
        return "Neither side gets hit in time: the close decides this trade"
    share = 100 * target_pct / decided
    need = 100 / (1 + rr)
    gap = share - need
    if abs(gap) < 5:
        return "Close to a coin flip" if abs(rr - 1) < 0.05 else "Close to break-even"
    side = "target" if gap > 0 else "stop"
    return ("Leans toward the " if abs(gap) < 15 else "Strongly toward the ") + side


def replay(sessions: list[dict], entry: float, stop: float, target: float, entry_slot: int,
           runs: int = DEFAULT_RUNS, seed: int = DEFAULT_SEED, last_n: int = MAX_SESSIONS,
           crypto: bool = False) -> dict:
    """Run the plan `runs` times over resampled bars from the last `last_n` full sessions."""
    if entry <= 0 or stop <= 0 or target <= 0:
        raise ValueError("prices must be above zero")
    if stop == entry:
        raise ValueError("the stop can't equal the entry")
    sign = 1 if stop < entry else -1
    if sign * (target - entry) <= 0:
        raise ValueError(f"the target must be {'above' if sign > 0 else 'below'} the entry")
    runs = max(500, min(MAX_RUNS, int(runs)))
    last_n = max(MIN_SESSIONS, min(MAX_SESSIONS, int(last_n)))
    full = [s for s in sessions if s["full"]][-last_n:]
    if len(full) < MIN_SESSIONS:
        raise ValueError(f"only {len(full)} full sessions in the data; the Time Machine needs at least {MIN_SESSIONS}")
    need = CRYPTO_BARS if crypto else STOCK_BARS
    pool = _ratios(full)
    slots = range(entry_slot + 1, need)
    picks = {k: [r for j in range(k - WINDOW, k + WINDOW + 1) for r in pool.get(j, ())] for k in slots}
    rng = random.Random(seed)
    risk = abs(entry - stop)
    rr = abs(target - entry) / risk
    counts = {"target": 0, "stop": 0, "flat": 0}
    closes, rs, green = [], [], 0
    for _ in range(runs):
        price = entry
        outcome, end = "flat", entry
        for k in slots:
            ro, rh, rl, rc = rng.choice(picks[k])
            h, l, c = price * rh, price * rl, price * rc
            if outcome == "flat":
                if (l <= stop) if sign > 0 else (h >= stop):
                    outcome, end = "stop", stop
                elif (h >= target) if sign > 0 else (l <= target):
                    outcome, end = "target", target
            price = c                      # keep walking: where the price sits at the close
        if outcome == "flat":
            end = price
        counts[outcome] += 1
        closes.append(price)
        r = 1.0 * rr if outcome == "target" else -1.0 if outcome == "stop" else sign * (end - entry) / risk
        rs.append(r)
        green += outcome == "target" or (outcome == "flat" and sign * (end - entry) > 0)

    # History: the same plan, scaled to each past day's price at the entry time, on the real bars.
    hist = {"target": 0, "stop": 0, "flat": 0}
    days = []
    for s in full:
        bars = {b[0]: b for b in s["bars"]}
        if entry_slot not in bars:
            continue
        e = bars[entry_slot][4]
        st, tg = e * stop / entry, e * target / entry
        path = [(bars[k][2], bars[k][3], bars[k][4]) for k in slots if k in bars]
        out, _ = _walk(path, e, st, tg, sign)
        hist[out] += 1
        days.append({"date": s["date"], "outcome": out})

    closes.sort()
    pct = lambda p: closes[min(len(closes) - 1, int(p * len(closes)))]  # noqa: E731
    lo, hi = closes[0], closes[-1]
    bins = 24
    width = (hi - lo) / bins or 1.0
    histo = [0] * bins
    for c in closes:
        histo[min(bins - 1, int((c - lo) / width))] += 1
    tp, sp, fp = (round(100 * counts[k] / runs, 1) for k in ("target", "stop", "flat"))
    return {
        "side": "buy" if sign > 0 else "sell", "entry": entry, "stop": stop, "target": target,
        "rr": round(rr, 2), "runs": runs, "seed": seed, "sessions_used": len(full),
        "first_day": full[0]["date"], "last_day": full[-1]["date"],
        "target_first": tp, "stop_first": sp, "flat": fp, "green": round(100 * green / runs, 1),
        "avg_r": round(sum(rs) / runs, 3), "breakeven_target_share": round(100 / (1 + rr), 1),
        "verdict": verdict(tp, sp, rr),
        "close_spread": {"p5": round(pct(0.05), 4), "p50": round(pct(0.5), 4), "p95": round(pct(0.95), 4)},
        "histogram": {"low": round(lo, 4), "high": round(hi, 4), "counts": histo},
        "history": {**hist, "days": days[-60:]},
        "warning": WARNING,
    }


# ---------------------------------------------------------------- opening-range scanner

def opening_range(session: dict, or_minutes: int = 15, until_slot: int | None = None,
                  rr: float = calculator.DEFAULT_RR) -> dict | None:
    """First 5-minute close outside the opening range (after it forms), up to `until_slot`. Plan: stop at the
    range's middle, target `rr` times the risk (our money rule is 2.4x)."""
    n = max(1, or_minutes // BAR_MIN)
    bars = [b for b in session["bars"] if until_slot is None or b[0] <= until_slot]
    first = [b for b in bars if b[0] < n]
    if len(first) < n:
        return None
    hi, lo = max(b[2] for b in first), min(b[3] for b in first)
    mid = (hi + lo) / 2
    for b in bars:
        if b[0] < n:
            continue
        side = "buy" if b[4] > hi else "sell" if b[4] < lo else None
        if side:
            entry = b[4]
            risk = abs(entry - mid)
            if risk <= 0:
                return None
            sign = 1 if side == "buy" else -1
            close_min = OPEN_MIN + (b[0] + 1) * BAR_MIN
            return {"side": side, "range_high": round(hi, 4), "range_low": round(lo, 4),
                    "entry": round(entry, 4), "stop": round(mid, 4),
                    "target": round(entry + sign * risk * rr, 4),
                    "entry_time": f"{close_min // 60:02d}:{close_min % 60:02d}",
                    "beyond_pct": round(100 * sign * (entry - (hi if sign > 0 else lo)) / entry, 2)}
    return None


def scan(bars: Bars, symbols=SCAN_SYMBOLS, or_minutes: int = 15) -> dict:
    hits, checked, sources = [], 0, set()
    for sym in symbols:
        sess, source = bars.sessions(sym)
        sources.add(source.split(" (")[0])
        if not sess:
            continue
        checked += 1
        today = sess[-1]
        hit = opening_range(today, or_minutes)
        if hit:
            hits.append({"symbol": sym, "date": today["date"], **hit})
    hits.sort(key=lambda h: -abs(h["beyond_pct"]))
    return {"checked": checked, "found": len(hits), "or_minutes": or_minutes, "hits": hits,
            "sources": sorted(sources),
            "note": "A scan is a list of charts that match a rule, not a list of things to buy."}


# ---------------------------------------------------------------- Congress trades (House Clerk)

def parse_house_index(raw_zip: bytes, year: int) -> list[dict]:
    """The House Clerk's yearly index zip -> Periodic Transaction Reports (stock trades), newest first."""
    with zipfile.ZipFile(io.BytesIO(raw_zip)) as z:
        name = next(n for n in z.namelist() if n.lower().endswith(".xml"))
        if z.getinfo(name).file_size > 30_000_000:
            raise ValueError("index too large")
        root = ET.fromstring(z.read(name))
    rows = []
    for m in root.iter("Member"):
        if (m.findtext("FilingType") or "").strip() != "P":
            continue
        try:
            filed = datetime.strptime((m.findtext("FilingDate") or "").strip(), "%m/%d/%Y")
        except ValueError:
            continue
        doc = (m.findtext("DocID") or "").strip()
        if not doc.isdigit():
            continue
        name = " ".join(x for x in ((m.findtext("First") or "").strip(), (m.findtext("Last") or "").strip()) if x)
        rows.append({"member": name, "district": (m.findtext("StateDst") or "").strip(),
                     "filed": filed.strftime("%Y-%m-%d"), "doc_id": doc,
                     "link": f"{CLERK}/ptr-pdfs/{year}/{doc}.pdf"})
    rows.sort(key=lambda r: (r["filed"], r["doc_id"]), reverse=True)
    return rows


class Congress:
    def __init__(self, get=None, clock=time.time):
        self._get = get or (lambda url, timeout=12.0: _get(url, timeout))
        self._clock = clock
        self._cache: tuple[float, dict] | None = None
        self._lock = threading.Lock()

    def latest(self, limit: int = 40, member: str = "") -> dict:
        with self._lock:
            if not self._cache or self._clock() - self._cache[0] > CONGRESS_CACHE_SECONDS:
                year = datetime.fromtimestamp(self._clock(), timezone.utc).year
                try:
                    rows = parse_house_index(self._get(f"{CLERK}/financial-pdfs/{year}FD.zip", 30), year)
                    body = {"ok": True, "year": year, "rows": rows}
                except Exception as e:  # noqa: BLE001
                    body = {"ok": False, "year": year, "rows": [], "error": f"House Clerk not reachable ({type(e).__name__})"}
                self._cache = (self._clock(), body)
            body = self._cache[1]
        rows = body["rows"]
        if member:
            rows = [r for r in rows if member.lower() in r["member"].lower()]
        return {**{k: v for k, v in body.items() if k != "rows"}, "total": len(rows), "rows": rows[:limit],
                "source": "House Clerk Periodic Transaction Reports (public filings)",
                "note": "Fast, not early: members have up to 45 days to report, so every trade here is days or weeks old."}


# ---------------------------------------------------------------- budget, risk, practice ticket

def plan_check(entry: float, stop: float, target: float, balance: float = 10_000.0,
               risk_pct: float = calculator.DEFAULT_RISK_PCT, machine: dict | None = None) -> dict:
    """Budget (whole shares from the max loss) + money-rules risk check, including the Time Machine's odds."""
    if balance <= 0 or not 0 < risk_pct <= 10:
        raise ValueError("balance must be above zero and risk between 0 and 10%")
    risk_per_share = abs(entry - stop)
    if risk_per_share <= 0:
        raise ValueError("the stop can't equal the entry")
    rule_cap = min(balance * calculator.RULE_MAX_PCT / 100, calculator.RULE_MAX_USD)
    budget_risk = min(balance * risk_pct / 100, rule_cap)
    by_risk = math.floor(budget_risk / risk_per_share + 1e-9)
    by_cash = math.floor(balance * 0.9 / entry + 1e-9)          # keep 10% cash
    shares = max(0, min(by_risk, by_cash))
    decided_by = "max loss per trade" if by_risk <= by_cash else "cash (10% kept back)"
    loss = shares * risk_per_share
    rr = abs(target - entry) / risk_per_share
    checks = [
        {"rule": "a stop is set", "ok": True, "detail": f"stop {stop:g}, {risk_per_share:.2f} a share at risk"},
        {"rule": "size fits", "ok": shares > 0,
         "detail": (f"{shares} shares" if shares else
                    f"the stop is too far: one share risks ${risk_per_share:.2f}, the cap is ${budget_risk:.2f}")},
        {"rule": "risk per trade", "ok": loss <= rule_cap + 1e-9,
         "detail": f"${loss:,.2f} at the stop; money rules allow the lower of 1% and $10 = ${rule_cap:,.2f}"},
        {"rule": "target 2.4x the stop", "ok": rr >= calculator.DEFAULT_RR - 1e-9, "detail": f"this plan is 1:{rr:.2f}"},
        {"rule": "2% daily loss", "ok": loss <= balance * calculator.DAILY_STOP_PCT / 100,
         "detail": f"daily stop ${balance * calculator.DAILY_STOP_PCT / 100:,.2f}"},
    ]
    if machine:
        lean = machine["verdict"]
        checks.append({"rule": "Time Machine odds", "ok": "toward the stop" not in lean,
                       "detail": f"{lean}: target first {machine['target_first']}%, stop first {machine['stop_first']}%"})
    ok = all(c["ok"] for c in checks)
    return {"shares": shares, "decided_by": decided_by, "loss_at_stop": round(loss, 2),
            "win_at_target": round(shares * abs(target - entry), 2), "rr": round(rr, 2),
            "rule_cap": round(rule_cap, 2), "checks": checks, "verdict": "PASS" if ok else "BLOCK",
            "broker": "Demo", "real_money": "locked"}


class PracticeTickets:
    """Tickets that passed the risk check, kept for the practice desk. Never sent to any broker."""

    def __init__(self, keep: int = 50):
        self.keep = keep
        self.items: list[dict] = []
        self._lock = threading.Lock()

    def add(self, symbol: str, side: str, shares: int, entry: float, stop: float, target: float,
            check: dict, clock=time.time) -> dict:
        if check.get("verdict") != "PASS":
            raise ValueError("the risk check blocked this plan, so no ticket")
        if shares <= 0:
            raise ValueError("no shares to trade")
        t = {"id": int(clock() * 1000), "symbol": symbol, "side": side, "shares": shares, "entry": entry,
             "stop_loss": stop, "take_profit": target, "type": "bracket", "mode": "Demo",
             "status": "practice ticket (not sent to any broker)",
             "at": datetime.fromtimestamp(clock(), timezone.utc).strftime("%Y-%m-%d %H:%M UTC")}
        with self._lock:
            self.items = ([t] + self.items)[: self.keep]
        return t

    def list(self) -> list[dict]:
        with self._lock:
            return list(self.items)


def _f(q: dict, k: str, default=None) -> float | None:
    v = q.get(k)
    if v in (None, ""):
        return default
    x = float(v)
    if not math.isfinite(x):
        raise ValueError(f"{k} must be a number")
    return x


def run(bars: Bars, q: dict) -> dict:
    """GET /api/timemachine: symbol, entry, stop, target, time, sessions, runs, seed, balance, risk."""
    symbol = (q.get("symbol") or "TSLA").upper().strip()
    if not symbol.replace("-", "").replace(".", "").isalnum() or len(symbol) > 12:
        raise ValueError("unknown symbol")
    crypto = is_crypto(symbol)
    sess, source = bars.sessions(symbol)
    if not sess:
        raise ValueError("no bars for that symbol")
    last = sess[-1]["bars"][-1][4]
    entry = _f(q, "entry") or last
    stop = _f(q, "stop")
    if stop is None:
        stop = entry * (1 - 0.006)
    sign = 1 if stop < entry else -1
    target = _f(q, "target")
    if target is None:
        target = entry + sign * abs(entry - stop) * calculator.DEFAULT_RR
    slot = slot_for(q.get("time"), crypto)
    m = replay(sess, entry, stop, target, slot, runs=int(_f(q, "runs", DEFAULT_RUNS)),
               seed=int(_f(q, "seed", DEFAULT_SEED)), last_n=int(_f(q, "sessions", MAX_SESSIONS)), crypto=crypto)
    check = plan_check(entry, stop, target, _f(q, "balance", 10_000.0), _f(q, "risk", calculator.DEFAULT_RISK_PCT), m)
    return {"symbol": symbol, "source": source, "last_price": round(last, 4), "entry_time": q.get("time") or
            ("10:15" if not crypto else "00:05"), "machine": m, "check": check}
