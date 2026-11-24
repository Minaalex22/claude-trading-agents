"""Five session desks so the practice desk trades around the clock: Sydney, Asia (Tokyo), Europe (Frankfurt),
London and New York.

Each desk works its own city's business hours, Monday to Friday in that city. While a desk is open it is on
shift: its crew (a fifth of every department, so each desk has trend, reversion, research and risk agents)
watches the markets that are busiest in that session. When the next city opens, it takes the lead and the
desk before it hands over (follow the sun). The hours together cover all 24 hours of the trading week.

Weekends: forex, stocks and indices are shut, so only crypto trades, at half size, led by whichever desk's
hours it is (the same weekend rule as trading/sessions.py).

Times shown to the owner are Melbourne time. Practice only: every desk routes to the paper desk. When the
MetaTrader 5 demo bridge is set up (METAAPI_TOKEN; MT5_MODE defaults to demo), the desks show that
route as ready for demo orders. Real money stays locked.
"""

from datetime import datetime, timedelta, timezone

from .markets import catalog
from .org import DEPARTMENTS, MIN_DEPARTMENT_SIZE
from .sessions import _london, _ny, _sunday

CRYPTO = ("BTC", "ETH", "SOL", "XRP", "DOGE")


def _sydney_offset(utc: datetime) -> int:
    """Sydney and Melbourne: +11 from the first Sunday of October to the first Sunday of April, else +10."""
    y = utc.year
    end = datetime(y, 4, _sunday(y, 4, 1), tzinfo=timezone.utc) - timedelta(hours=8)  # 3 a.m. AEDT = Sat 16:00 UTC
    start = datetime(y, 10, _sunday(y, 10, 1), tzinfo=timezone.utc) - timedelta(hours=8)  # 2 a.m. AEST
    return 11 if utc < end or utc >= start else 10


def _offset(desk: str, utc: datetime) -> int:
    if desk in ("sydney", "melbourne"):
        return _sydney_offset(utc)
    if desk == "asia":
        return 9  # Tokyo keeps no daylight saving
    if desk == "europe":  # Frankfurt: one hour ahead of London all year (the EU and UK change clocks together)
        return int(_london(utc.timestamp()).utcoffset().total_seconds() // 3600) + 1
    ts = utc.timestamp()
    local = _london(ts) if desk == "london" else _ny(ts)
    return int(local.utcoffset().total_seconds() // 3600)


def local(desk: str, utc: datetime) -> datetime:
    return utc.astimezone(timezone(timedelta(hours=_offset(desk, utc))))


def melbourne(utc: datetime) -> datetime:
    return local("melbourne", utc)


def _to_utc(desk: str, naive: datetime) -> datetime:
    guess = naive.replace(tzinfo=timezone.utc)
    off = _offset(desk, guess - timedelta(hours=_offset(desk, guess)))
    return guess - timedelta(hours=off)


DESKS = {
    "sydney": {
        "name": "Sydney", "city": "Sydney", "zone": "Sydney time", "open": 7, "close": 16,
        "note": "Opens the trading week. Thin books at first, so new risk goes in at 3/4 until Asia joins.",
        "markets": ["ASX200", "AUDUSD", "GOLD", "COPPER", "BTC", "ETH"],
        "size": 0.75,
    },
    "asia": {
        "name": "Asia", "city": "Tokyo", "zone": "Tokyo time", "open": 9, "close": 18,
        "note": "Tokyo and Hong Kong. Yen, Aussie dollar, metals and crypto move most here.",
        "markets": ["USDJPY", "AUDUSD", "GOLD", "COPPER", "BTC", "ETH", "SOL"],
        "size": 0.75,
    },
    "europe": {
        "name": "Europe", "city": "Frankfurt", "zone": "Frankfurt time", "open": 8, "close": 17,
        "note": "Frankfurt, Paris and Zurich open an hour before London. DAX, euro, Swiss franc and Bund moves start here.",
        "markets": ["DAX", "EURUSD", "USDCHF", "EURGBP", "GOLD", "BTC"],
        "size": 0.75,
    },
    "london": {
        "name": "London", "city": "London", "zone": "London time", "open": 8, "close": 17,
        "note": "The deepest forex session. Euro, pound, DAX, gold and oil set the day's trend.",
        "markets": ["EURUSD", "GBPUSD", "DAX", "GOLD", "SILVER", "OIL", "BTC"],
        "size": 1.0,
    },
    "newyork": {
        "name": "New York", "city": "New York", "zone": "New York time", "open": 8, "close": 17,
        "note": "US stocks and indices (9:30 to 16:00), dollar pairs, oil and gas. London overlap is the busiest hour.",
        "markets": ["SPX", "NDX", "DJI", "AAPL", "NVDA", "TSLA", "MSFT", "AMZN", "EURUSD", "USDCAD", "OIL", "NATGAS", "GOLD", "BTC"],
        "size": 1.0,
    },
}
ORDER = ("sydney", "asia", "europe", "london", "newyork")


def windows(desk: str, now: datetime, days: int = 3) -> list[tuple[datetime, datetime]]:
    """The desk's open windows (UTC) from a day before `now` to `days` days after, weekdays in the desk's city."""
    d = DESKS[desk]
    today = local(desk, now).date()
    out = []
    for i in range(-1, days + 1):
        day = today + timedelta(days=i)
        if day.weekday() >= 5:
            continue
        start = _to_utc(desk, datetime(day.year, day.month, day.day, d["open"]))
        end = _to_utc(desk, datetime(day.year, day.month, day.day, d["close"]))
        out.append((start, end))
    return out


def is_open(desk: str, now: datetime) -> bool:
    return any(s <= now < e for s, e in windows(desk, now, 1))


def _last_open(desk: str, now: datetime) -> datetime | None:
    starts = [s for s, e in windows(desk, now, 1) if s <= now < e]
    return max(starts) if starts else None


def weekend(now: datetime) -> bool:
    """The forex week is shut: from Friday 17:00 New York until Sydney opens on Monday."""
    ny = _ny(now.timestamp())
    if ny.weekday() == 4 and ny.hour >= 17 or ny.weekday() == 5:
        return True
    return ny.weekday() == 6 and not is_open("sydney", now) and not is_open("asia", now)


def lead(now: datetime) -> str:
    """The desk in charge now: the open desk that opened last (follow the sun). On weekends, or in a gap,
    the desk whose city hours it is anyway, else the next to open."""
    open_now = [(t, k) for k in ORDER if (t := _last_open(k, now))]
    if open_now:
        return max(open_now)[1]
    nxt = next_change(now, 4)
    opens = [c for c in nxt if c["type"] == "open"]
    return opens[0]["desk"] if opens else "sydney"


def next_change(now: datetime, n: int = 6) -> list[dict]:
    """The next opens and closes across all five desks, soonest first."""
    events = []
    for k in ORDER:
        for s, e in windows(k, now, 3):
            if s > now:
                events.append((s, k, "open"))
            if e > now:
                events.append((e, k, "close"))
    events.sort()
    return [{"desk": k, "name": DESKS[k]["name"], "type": t, "utc": ts.isoformat(),
             "melbourne": melbourne(ts).strftime("%a %H:%M"), "in_minutes": int((ts - now).total_seconds() // 60)}
            for ts, k, t in events[:n]]


def handoffs(now: datetime, n: int = 6) -> list[dict]:
    """When the lead passes from one desk to the next, in Melbourne time."""
    out, prev, t = [], lead(now), now
    end = now + timedelta(days=4)
    while t < end and len(out) < n:
        changes = next_change(t, 1)
        if not changes:
            break
        t = datetime.fromisoformat(changes[0]["utc"]) + timedelta(seconds=1)
        cur = lead(t)
        if cur != prev:
            out.append({"from": prev, "from_name": DESKS[prev]["name"], "to": cur, "to_name": DESKS[cur]["name"],
                        "utc": t.replace(microsecond=0).isoformat(), "melbourne": melbourne(t).strftime("%a %H:%M")})
            prev = cur
    return out


def route(env) -> dict:
    """Where a desk's orders go. Always the paper desk; the MT5 demo bridge shows as ready once it is set up."""
    mt5 = bool(env.get("METAAPI_TOKEN")) and (env.get("MT5_MODE") or "demo").lower() == "demo"
    return {"orders_to": "paper", "mt5_demo": "ready" if mt5 else "not set up", "real_money": "locked"}


def _crew(org: dict | None) -> dict[str, int]:
    """Each desk's share of every department: a fifth, so every desk has all four kinds of agent."""
    out = {}
    for d in DEPARTMENTS:
        total = int((org or {}).get(d.key, {}).get("agents") or MIN_DEPARTMENT_SIZE * 1)
        total = max(total, MIN_DEPARTMENT_SIZE)
        out[d.key] = total // len(ORDER)
    return out


def _local_hours(desk: str, now: datetime) -> dict:
    """Today's (or the next) window in the city's own time and in Melbourne time."""
    ws = windows(desk, now, 4)
    cur = [w for w in ws if w[0] <= now < w[1]] or [w for w in ws if w[0] > now]
    s, e = cur[0]
    d = DESKS[desk]
    return {"local": f"{d['open']:02d}:00 to {d['close']:02d}:00 {d['zone']}",
            "melbourne": f"{melbourne(s).strftime('%H:%M')} to {melbourne(e).strftime('%H:%M')} Melbourne",
            "opens": s.isoformat(), "closes": e.isoformat(),
            "open_melbourne": melbourne(s).strftime("%a %H:%M"), "close_melbourne": melbourne(e).strftime("%a %H:%M")}


def assign(trades: list[dict]) -> dict[str, list[dict]]:
    """Each closed trade goes to the desk that was leading when it closed (looked up once per 10 minutes)."""
    out: dict[str, list[dict]] = {k: [] for k in ORDER}
    cache: dict[int, str] = {}
    for t in trades:
        try:
            ts = float(t.get("ts"))
        except (TypeError, ValueError):
            continue
        slot = int(ts // 600)
        if slot not in cache:
            cache[slot] = lead(datetime.fromtimestamp(slot * 600, timezone.utc))
        out[cache[slot]].append(t)
    return out


def _book(mine: list[dict]) -> dict:
    won = sum(1 for t in mine if t.get("won"))
    pnl = round(sum(float(t.get("pnl") or 0) for t in mine), 2)
    return {"trades": len(mine), "won": won, "lost": len(mine) - won,
            "win_rate": round(won / len(mine), 4) if mine else None, "pnl": pnl, "recent": mine[:12]}


def desk(key: str, now: datetime, org: dict | None = None, trades: list[dict] | None = None,
         prices: dict | None = None, env=None, assigned: dict | None = None) -> dict:
    d = DESKS[key]
    wk = weekend(now)
    open_ = is_open(key, now) and not wk
    leading = lead(now) == key
    crew = _crew(org)
    markets = [m for m in d["markets"] if not wk or m in CRYPTO]
    size = 0.5 if wk else d["size"]
    if open_ and key in ("london", "newyork") and is_open("london", now) and is_open("newyork", now):
        size = 1.0
    if not open_ and not (wk and leading):
        size = 0.0
    status = "leading" if leading and (open_ or wk) else "open" if open_ else "closed"
    return {
        "key": key, "name": d["name"], "city": d["city"], "note": d["note"], "status": status,
        "open": open_, "leading": leading, "weekend": wk,
        "city_time": local(key, now).strftime("%a %H:%M"), "hours": _local_hours(key, now),
        "markets": [{"symbol": m, "name": catalog.BY_SYMBOL[m].name if m in catalog.BY_SYMBOL else m,
                     "kind": catalog.BY_SYMBOL[m].kind if m in catalog.BY_SYMBOL else "",
                     "price": (prices or {}).get(m)} for m in markets],
        "crew": crew, "crew_total": sum(crew.values()), "on_shift": sum(crew.values()) if open_ or (wk and leading) else 0,
        "size": size, "route": route(env or {}), "book": _book((assigned or assign(trades or []))[key]),
    }


def board(now: datetime | None = None, org: dict | None = None, trades: list[dict] | None = None,
          prices: dict | None = None, env=None) -> dict:
    now = now or datetime.now(timezone.utc)
    assigned = assign(trades or [])
    desks = [desk(k, now, org, trades, prices, env, assigned) for k in ORDER]
    timeline = []
    for k in ORDER:  # each desk's next 24 hours as Melbourne-time bars for the clock strip
        bars = []
        for s, e in windows(k, now, 2):
            lo, hi = max(s, now), min(e, now + timedelta(hours=24))
            if lo < hi:
                bars.append({"from_h": round((lo - now).total_seconds() / 3600, 3),
                             "to_h": round((hi - now).total_seconds() / 3600, 3)})
        timeline.append({"desk": k, "name": DESKS[k]["name"], "bars": bars})
    return {"now_melbourne": melbourne(now).strftime("%a %d %b %H:%M"), "now_utc": now.isoformat(),
            "lead": lead(now), "weekend": weekend(now), "desks": desks, "timeline": timeline,
            "next": next_change(now), "handoffs": handoffs(now), "route": route(env or {}),
            "coverage_hours": coverage(now)}


def coverage(now: datetime) -> float:
    """Hours of the next 24 that at least one desk is open (24 on a weekday: the sun never sets on the desk)."""
    covered = 0
    for m in range(0, 24 * 60, 10):
        t = now + timedelta(minutes=m)
        if any(is_open(k, t) for k in ORDER):
            covered += 10
    return round(covered / 60, 1)
