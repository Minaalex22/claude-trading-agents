"""The Pelosi trader: follow the stock trades US Congress members must disclose (STOCK Act), on paper.

Built on the trader-pelosi skill from github.com/jakeschincariol/day-trader-skill (MIT), vendored as
trading/pelosi_ptr.py for the House Clerk index and PTR parsing.

Every trade carries TWO dates, and both are kept and shown:
  - trade_date: the day the member (or spouse / child) actually bought or sold.
  - announce_date: the day the report was filed and the public could see it. The law allows up to
    45 days after the trade (30 days after the member is notified), so a trade can surface any time
    in that window. lag_days = announce_date - trade_date; over 45 is flagged late.
The desk can only act on what is public, so every paper buy (and every backtest entry) happens on the
first trading day AFTER announce_date, never on the trade date. The trade date is still used: it is shown
on every row, it sets the lag, and the page shows how far the price moved between trade and announcement.

Two arms (both paper):
  - "pelosi": every announced purchase by Nancy Pelosi's household (stock or call options; calls are
    followed with shares only, never options).
  - "congress": any House member's announced purchase of $250,001 or more.
mina's guards (2026-11-25): no buy when the price already rose 15%+ between the trade date and the announcement
(the 45-day chase trap), and only when the stock is above its 200-day average. Sage's news lean gets +0.20 on
a stock with an announced buy (trend gate respected).
Exits: after HOLD_DAYS calendar days (84, about 60 trading days), or as soon as the same member's SALE
of that stock is announced. Long only. No options, no shorts, no leverage. Real money stays locked.

Where the data comes from (the cloud app cannot always reach the House site, so three sources):
  1. CapitolStockWatch open data (raw.githubusercontent.com, daily): real filing dates, House + Senate.
  2. house-stock-watcher-data (raw.githubusercontent.com, daily): House PTR rows back to 2014. Its
     "disclosure_date" is the PTR's notification date, not the filing date, so the announce date is set to
     the legal deadline min(notified + 30, trade + 45) and marked estimated. That is the latest legal day,
     so it never peeks ahead (except for late filers).
  3. The House Clerk's own yearly index (<YEAR>FD.zip) when reachable: the true filing date for each DocID.

Environment: PELOSI_FEED=off stops the feed; PELOSI_HOLD_DAYS (84), PELOSI_CONGRESS_MIN (250001), PELOSI_CHASE_CAP (0.15), PELOSI_TREND_GATE (on),
UW_API_TOKEN (adds Unusual Whales' congress feed every 30 minutes),
PELOSI_BOOK ($100,000 paper), PELOSI_MEMBERS (extra names to follow, comma separated).
"""

from __future__ import annotations

import bisect
import csv
import io
import json
import os
import threading
import time
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

CAPITOL_CSV = ("https://raw.githubusercontent.com/MichaelBMiner/capitol-stock-watch-open-data/main/"
               "congress_trades_latest.csv")
HSW_JSON = "https://raw.githubusercontent.com/TattooedHead/house-stock-watcher-data/main/data/all_transactions.json"
LATE_DAYS = 45
NOTIFY_DAYS = 30
HOLD_DAYS = 84
CONGRESS_MIN = 250_001  # mina's design: only $250,001+ purchases (replay: 75% wins, PF 7.3 vs 59% / 4.0 at $50k)
CHASE_CAP = 0.15  # skip a buy when the price already rose 15%+ since the trade date (mina's chase cap)
TREND_GATE = True  # only buy above the 200-day average (mina's design, trend_anchor rule); PELOSI_TREND_GATE=off
UW_CONGRESS = "https://api.unusualwhales.com/api/congress/recent-trades"
UW_EVERY = 1800  # Unusual Whales congress feed every 30 minutes when UW_API_TOKEN is set
BOOK = 100_000.0
REFRESH_SECONDS = 6 * 3600
HTTP_TIMEOUT = 30
DATA = Path(__file__).resolve().parent / "data" / "pelosi_backtest.json"
NOTE = ("Public STOCK Act disclosures, filed up to 45 days after the trade. Reading them fast is not inside "
        "information. Paper only: nothing here places a real order.")
ALIAS = {"FB": "META", "GOOGL": "GOOG", "BRK.B": "BRK-B"}


def _d(s) -> date | None:
    s = str(s or "").strip()
    if not s:
        return None
    try:
        if "/" in s:
            m, d, y = s.split("/")
            y = int(y)
            return date(y + 2000 if y < 100 else y, int(m), int(d))
        return date.fromisoformat(s[:10])
    except ValueError:
        return None


def _side(t: str) -> str | None:
    t = str(t or "").strip().lower()
    if t.startswith("p") or t.startswith("buy"):
        return "buy"
    if t.startswith("s"):
        return "sell"
    return None  # exchanges and the rest are shown, never traded


def _kind(text: str) -> str:
    t = str(text or "").lower()
    if "call" in t:
        return "call"
    if "put" in t:
        return "put"
    return "stock"


def _trade(member, ticker, trade_d, announce_d, side, kind, low, high, *, owner="", asset="", url="",
           source="", estimated=False, chamber="house", party="", state="", amount="") -> dict | None:
    if not trade_d or not announce_d:
        return None
    lag = (announce_d - trade_d).days
    ticker = str(ticker or "").strip().upper()
    return {"member": member.strip(), "chamber": chamber, "party": party, "state": state, "owner": owner or "self",
            "ticker": ticker, "asset": asset.strip(), "side": side, "kind": kind,
            "trade_date": trade_d.isoformat(), "announce_date": announce_d.isoformat(),
            "announce_estimated": estimated, "lag_days": lag, "late": lag > LATE_DAYS,
            "amount": amount or (f"${low:,.0f} - ${high:,.0f}" if low and high else ""),
            "amount_low": low or 0, "amount_high": high or 0, "url": url, "source": source}


def from_capitol_csv(text: str) -> list[dict]:
    """CapitolStockWatch rows: transaction_date + filing_date (the real announce date)."""
    out = []
    for r in csv.DictReader(io.StringIO(text)):
        side = _side(r.get("transaction_type"))
        if not side:
            continue
        t = _trade(r.get("filer_name", ""), r.get("ticker"), _d(r.get("transaction_date")), _d(r.get("filing_date")),
                   side, _kind(r.get("security_type", "")), float(r.get("amount_min_usd") or 0),
                   float(r.get("amount_max_usd") or 0), asset=r.get("asset_name", ""), url=r.get("filing_url", ""),
                   source="capitolstockwatch", chamber=r.get("chamber", "house"), party=r.get("party", ""),
                   state=r.get("state", ""), amount=r.get("amount_range", ""))
        if t:
            out.append(t)
    return out


def announce_deadline(trade_d: date, notified_d: date | None) -> date:
    """The latest day the law allows: 30 days after notification, never more than 45 after the trade."""
    last = trade_d + timedelta(days=LATE_DAYS)
    if notified_d and notified_d >= trade_d:
        last = min(last, notified_d + timedelta(days=NOTIFY_DAYS))
    return last


def from_hsw_json(rows: list) -> list[dict]:
    """house-stock-watcher rows. Their disclosure_date is the PTR notification date, so the announce date is
    estimated as the legal deadline (see announce_deadline) unless the House index gives the real one."""
    out = []
    for r in rows or []:
        side = _side(r.get("type"))
        td = _d(r.get("transaction_date"))
        if not side or not td:
            continue
        low = high = 0.0
        amt = str(r.get("amount") or "")
        nums = [float(x.replace(",", "")) for x in __import__("re").findall(r"\$([\d,]+)", amt)]
        if nums:
            low, high = nums[0], nums[-1]
        t = _trade(r.get("representative", ""), r.get("ticker"), td, announce_deadline(td, _d(r.get("disclosure_date"))),
                   side, _kind(r.get("asset_type", "")), low, high, owner=str(r.get("owner") or "").lower(),
                   asset=r.get("asset_description", ""), url=r.get("source_url", ""), source="house-stock-watcher",
                   estimated=True, state=str(r.get("district") or "")[:2], amount=amt)
        if t:
            t["doc_id"] = str(r.get("filing_id") or "")
            out.append(t)
    return out


def from_uw(rows: list) -> list[dict]:
    """Unusual Whales /api/congress/recent-trades rows (field names vary a little, so several are tried)."""
    import re
    out = []
    for r in rows or []:
        side = _side(r.get("txn_type") or r.get("type") or r.get("transaction_type"))
        if not side:
            continue
        amt = str(r.get("amounts") or r.get("amount") or "")
        nums = [float(x.replace(",", "")) for x in re.findall(r"\$?([\d,]{4,})", amt)]
        t = _trade(str(r.get("name") or r.get("reporter") or r.get("representative") or ""), r.get("ticker"),
                   _d(r.get("transaction_date")), _d(r.get("filed_at_date") or r.get("filed_at") or r.get("disclosure_date")),
                   side, _kind(r.get("asset_type") or r.get("issuer") or ""), nums[0] if nums else 0,
                   nums[-1] if nums else 0, owner=str(r.get("member_type") or ""), url=str(r.get("url") or ""),
                   source="unusualwhales", chamber=str(r.get("chamber") or "house").lower(), amount=amt)
        if t:
            out.append(t)
    return out


def _key(t: dict) -> tuple:
    return (t["member"].split()[-1].lower() if t["member"] else "", ALIAS.get(t["ticker"], t["ticker"]),
            t["trade_date"], t["side"])


def merge(*lists: list[dict], filed: dict | None = None) -> list[dict]:
    """One list, newest announcement first. Real filing dates win over estimated ones; `filed` maps a House
    DocID to its real filing date (from the Clerk's index) and fixes estimated rows."""
    best: dict[tuple, dict] = {}
    for lst in lists:
        for t in lst:
            if filed and t.get("announce_estimated") and t.get("doc_id") in filed:
                real = _d(filed[t["doc_id"]])
                if real:
                    t = {**t, "announce_date": real.isoformat(), "announce_estimated": False,
                         "lag_days": (real - _d(t["trade_date"])).days}
                    t["late"] = t["lag_days"] > LATE_DAYS
            k = _key(t)
            old = best.get(k)
            if old is None or (old.get("announce_estimated") and not t.get("announce_estimated")):
                best[k] = t
    return sorted(best.values(), key=lambda t: (t["announce_date"], t["trade_date"]), reverse=True)


def is_pelosi(t: dict) -> bool:
    return "pelosi" in t.get("member", "").lower()


def members_followed(env: dict | None = None) -> list[str]:
    extra = (env or os.environ).get("PELOSI_MEMBERS", "")
    return ["pelosi"] + [m.strip().lower() for m in extra.split(",") if m.strip()]


def arm_of(t: dict, members: list[str], congress_min: float = CONGRESS_MIN) -> str | None:
    """Which arm follows this purchase, or None."""
    if t["side"] != "buy" or not t["ticker"] or t["kind"] == "put":
        return None
    name = t["member"].lower()
    if any(m in name for m in members):
        return "pelosi"
    if t["chamber"] == "house" and t["amount_low"] >= congress_min:
        return "congress"
    return None


def confidence(t: dict, move_since_trade: float | None = None) -> float:
    """0.5 to 0.9: Pelosi, size of the range, call options and a price already rising since the trade date
    (Pelosi only: in the replay her buys that ran up 5%+ before the announcement beat the S&P 500 7 times in 10;
    for other members it did not help)."""
    c = 0.55
    if is_pelosi(t):
        c += 0.1
    if t["amount_low"] >= 250_000:
        c += 0.1
    if t["kind"] == "call":
        c += 0.05
    if is_pelosi(t) and move_since_trade is not None and move_since_trade > 0.05:
        c += 0.1
    if t["late"]:
        c -= 0.05
    return round(max(0.5, min(0.9, c)), 2)


# ---------------------------------------------------------------- backtest (real prices, announce-date entries)

def backtest(trades: list[dict], prices: dict, members: list[str] | None = None, hold: int = 60,
             congress_min: float | None = None, arm: str | None = None, bench: str = "SPY",
             chase_cap: float | None = None, trend_gate: bool | None = None) -> dict:
    """Replay announced purchases on daily closes. prices: {ticker: (dates[], closes[])} with ISO dates.
    Entry: close of the first trading day AFTER announce_date. Exit: `hold` trading days later, or the first
    trading day after the same member's sale is announced. Returns stats and every trade."""
    members = members or ["pelosi"]
    congress_min = CONGRESS_MIN if congress_min is None else congress_min
    chase_cap = CHASE_CAP if chase_cap is None else chase_cap
    trend_gate = TREND_GATE if trend_gate is None else trend_gate
    skipped = {"chase": 0, "trend": 0}
    sales: dict[tuple, list[str]] = {}
    for t in trades:
        if t["side"] == "sell":
            sales.setdefault((t["member"], ALIAS.get(t["ticker"], t["ticker"])), []).append(t["announce_date"])
    bd, bc = prices.get(bench, ([], []))
    rows = []
    for t in trades:
        a = arm_of(t, members, congress_min)
        if not a or (arm and a != arm):
            continue
        tk = ALIAS.get(t["ticker"], t["ticker"])
        if tk not in prices:
            continue
        d, c = prices[tk]
        if not d or t["announce_date"] < d[0]:
            continue
        i = bisect.bisect_right(d, t["announce_date"])
        if i >= len(d):
            continue
        j = min(i + hold, len(d) - 1)
        why = "held"
        for s in sorted(sales.get((t["member"], tk), [])):
            if s > t["announce_date"]:
                k = bisect.bisect_right(d, s)
                if i < k < j:
                    j, why = k, "member sold"
                break
        if j <= i:
            continue
        ti = bisect.bisect_left(d, t["trade_date"])
        move = c[i] / c[ti] - 1 if 0 < ti < len(d) and d[ti] >= t["trade_date"] else None
        if chase_cap and move is not None and move > chase_cap:
            skipped["chase"] += 1
            continue
        if trend_gate and i >= 200 and c[i] <= sum(c[i - 200:i]) / 200:
            skipped["trend"] += 1
            continue
        ret = c[j] / c[i] - 1
        spy = None
        if bd:
            si, sj = bisect.bisect_left(bd, d[i]), bisect.bisect_left(bd, d[j])
            if sj < len(bc):
                spy = bc[sj] / bc[si] - 1
        rows.append({"arm": a, "member": t["member"], "ticker": tk, "kind": t["kind"], "amount": t["amount"],
                     "trade_date": t["trade_date"], "announce_date": t["announce_date"], "lag_days": t["lag_days"],
                     "announce_estimated": t.get("announce_estimated", False), "entry": d[i], "exit": d[j],
                     "exit_why": why, "move_trade_to_announce": None if move is None else round(move, 4),
                     "ret": round(ret, 4), "spy": None if spy is None else round(spy, 4)})
    return {"hold_days": hold, "stats": stats(rows), "skipped": skipped, "trades": sorted(rows, key=lambda r: r["entry"])}


def stats(rows: list[dict], stake: float = 1000.0) -> dict:
    """Win rate, profit factor, average return, beat-SPY rate, P&L on $1,000 a trade and its worst drawdown."""
    n = len(rows)
    if not n:
        return {"trades": 0}
    wins = [r for r in rows if r["ret"] > 0]
    gain = sum(r["ret"] for r in wins)
    loss = -sum(r["ret"] for r in rows if r["ret"] <= 0)
    eq = peak = dd = 0.0
    for r in sorted(rows, key=lambda r: r["exit"]):
        eq += stake * r["ret"]
        peak = max(peak, eq)
        dd = max(dd, peak - eq)
    vs = [r["ret"] - r["spy"] for r in rows if r.get("spy") is not None]
    return {"trades": n, "win_rate": round(100 * len(wins) / n, 1),
            "profit_factor": round(gain / loss, 2) if loss else None,
            "avg_return_pct": round(100 * (gain - loss) / n, 2),
            "vs_spy_pct": round(100 * sum(vs) / len(vs), 2) if vs else None,
            "beat_spy_pct": round(100 * sum(1 for v in vs if v > 0) / len(vs), 1) if vs else None,
            "pnl_per_1k": round(eq, 0), "max_drawdown": round(dd, 0),
            "avg_lag_days": round(sum(r["lag_days"] for r in rows) / n, 1)}


def load_backtest() -> dict:
    try:
        return json.loads(DATA.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


# ---------------------------------------------------------------- the paper book

class PaperBook:
    """Paper positions opened from announced purchases. Saved as JSON on the desk's data disk."""

    def __init__(self, path: str | Path | None = None, book: float = BOOK, hold_days: int = HOLD_DAYS) -> None:
        base = Path(os.environ.get("DESK_DATA_DIR", "").strip() or "data")
        self.path = Path(path) if path else base / "pelosi_book.json"
        self.book, self.hold_days = book, hold_days
        self.lock = threading.Lock()
        self.state = {"cash": book, "open": [], "closed": [], "seen": []}
        try:
            self.state.update(json.loads(self.path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            pass

    def _save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.state), encoding="utf-8")
            tmp.replace(self.path)
        except OSError:
            pass

    def step(self, trades: list[dict], price, members: list[str], today: date | None = None,
             congress_min: float = CONGRESS_MIN, closes=None) -> list[str]:
        """Open new positions for purchases announced in the last 5 days, close on time or an announced sale.
        price(ticker) -> float; closes(ticker) -> (dates, closes) for the move since the trade date (optional)."""
        today = today or datetime.now(timezone.utc).date()
        log = []
        with self.lock:
            st = self.state
            seen = set(st["seen"])
            for t in sorted(trades, key=lambda t: t["announce_date"]):
                k = "|".join(map(str, _key(t)))
                if k in seen:
                    continue
                ad = _d(t["announce_date"])
                if t["side"] == "sell":
                    for p in list(st["open"]):
                        if p["member"] == t["member"] and p["ticker"] == ALIAS.get(t["ticker"], t["ticker"]) \
                                and t["announce_date"] >= p["announce_date"] and ad <= today:
                            log.append(self._close(p, price, today, "member sold"))
                    seen.add(k)
                    continue
                a = arm_of(t, members, congress_min)
                if not a or not ad or ad > today or (today - ad).days > 5:
                    seen.add(k) if a is None or (ad and (today - ad).days > 5) else None
                    continue
                tk = ALIAS.get(t["ticker"], t["ticker"])
                if any(p["ticker"] == tk and p["arm"] == a for p in st["open"]):
                    seen.add(k)
                    continue
                try:
                    px = float(price(tk))
                except Exception:  # noqa: BLE001  (no price: try again next round)
                    continue
                if px <= 0:
                    continue
                move = None
                if closes:
                    try:
                        d, c = closes(tk)
                        i = bisect.bisect_left(d, t["trade_date"])
                        if 0 <= i < len(d) and c[i] > 0:
                            move = px / c[i] - 1
                    except Exception:  # noqa: BLE001
                        move = None
                if CHASE_CAP and move is not None and move > CHASE_CAP:
                    seen.add(k)
                    log.append(f"SKIP {tk}: up {100 * move:.0f}% since the trade date (chase cap)")
                    continue
                if TREND_GATE and above_200(closes, tk, px) is False:
                    seen.add(k)
                    log.append(f"SKIP {tk}: under its 200-day average (trend gate)")
                    continue
                conf = confidence(t, move)
                size = min(st["cash"], self.book * (0.02 + 0.06 * (conf - 0.5) / 0.4))  # 2% to 8% of the book
                if size < 100:
                    continue
                st["cash"] -= size
                st["open"].append({"arm": a, "member": t["member"], "ticker": tk, "kind": t["kind"],
                                   "trade_date": t["trade_date"], "announce_date": t["announce_date"],
                                   "lag_days": t["lag_days"], "amount": t["amount"], "confidence": conf,
                                   "move_trade_to_announce": None if move is None else round(move, 4),
                                   "opened": today.isoformat(), "entry": px, "qty": size / px, "cost": size,
                                   "url": t.get("url", "")})
                seen.add(k)
                log.append(f"BUY {tk} ${size:,.0f} ({a}, {t['member']}, traded {t['trade_date']}, "
                           f"announced {t['announce_date']}, {t['lag_days']} days later)")
            for p in list(st["open"]):
                if (today - _d(p["opened"])).days >= self.hold_days:
                    log.append(self._close(p, price, today, "held"))
            st["seen"] = sorted(seen)[-5000:]
            if log:
                self._save()
        return log

    def _close(self, p: dict, price, today: date, why: str) -> str:
        try:
            px = float(price(p["ticker"]))
        except Exception:  # noqa: BLE001
            px = p["entry"]
        value = p["qty"] * px
        self.state["cash"] += value
        self.state["open"].remove(p)
        self.state["closed"].append({**p, "closed": today.isoformat(), "exit": px, "pnl": round(value - p["cost"], 2),
                                     "ret": round(px / p["entry"] - 1, 4), "why": why})
        self.state["closed"] = self.state["closed"][-500:]
        return f"SELL {p['ticker']} ({why}) {100 * (px / p['entry'] - 1):+.1f}%"

    def snapshot(self, price=None) -> dict:
        with self.lock:
            st = json.loads(json.dumps(self.state))
        open_value = 0.0
        for p in st["open"]:
            px = p["entry"]
            if price:
                try:
                    px = float(price(p["ticker"]))
                except Exception:  # noqa: BLE001
                    pass
            p["last"] = px
            p["pnl"] = round(p["qty"] * px - p["cost"], 2)
            open_value += p["qty"] * px
        closed = st["closed"]
        wins = sum(1 for c in closed if c["pnl"] > 0)
        st.pop("seen", None)
        return {**st, "equity": round(st["cash"] + open_value, 2), "book": self.book,
                "closed_win_rate": round(100 * wins / len(closed), 1) if closed else None, "real_money": "locked"}


def above_200(closes, ticker: str, px: float) -> bool | None:
    """True/False against the 200-day average, None when there are not 200 closes (the gate abstains)."""
    if not closes:
        return None
    try:
        _, c = closes(ticker)
    except Exception:  # noqa: BLE001
        return None
    return px > sum(c[-200:]) / 200 if len(c) >= 200 else None


# ---------------------------------------------------------------- the feed

def _get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "ats-matrix-pelosi/1.0"})
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:  # noqa: S310 (fixed https hosts)
        return r.read()


class Feed:
    """Pulls the three sources every 6 hours in a background thread; `trades()` is the merged list."""

    def __init__(self, get=_get, uw_token: str = "") -> None:
        self.get = get
        self.uw_token = uw_token
        self._free_at = 0.0
        self._free: list[list[dict]] = []
        self.lock = threading.Lock()
        self._trades: list[dict] = []
        self.status_ = {"sources": {}, "updated": None, "new_ptrs": []}
        self._seen_docs: set[str] = set()

    def refresh(self) -> None:
        if self._free and time.time() - self._free_at < REFRESH_SECONDS:  # free sources: every 6 hours
            lists, src, filed = list(self._free), dict(self.status_.get("sources", {})), self._filed
            self._uw(lists, src)
            self._publish(lists, src, filed, [])
            return
        lists, src, filed = [], {}, {}
        try:
            lists.append(from_capitol_csv(self.get(CAPITOL_CSV).decode("utf-8", "replace")))
            src["capitolstockwatch"] = {"ok": True, "rows": len(lists[-1])}
        except Exception as e:  # noqa: BLE001
            src["capitolstockwatch"] = {"ok": False, "error": str(e)[:120]}
        try:
            lists.append(from_hsw_json(json.loads(self.get(HSW_JSON))))
            src["house-stock-watcher"] = {"ok": True, "rows": len(lists[-1])}
        except Exception as e:  # noqa: BLE001
            src["house-stock-watcher"] = {"ok": False, "error": str(e)[:120]}
        new = []
        try:
            from . import pelosi_ptr
            year = datetime.now(timezone.utc).year
            idx = pelosi_ptr.parse_index(pelosi_ptr.read_index_bytes(self.get(pelosi_ptr.INDEX_URL % (year, "FD"))), year)
            ptrs = [r for r in idx if r["type"] == "P"]
            filed = {r["doc_id"]: r["filed"] for r in ptrs if r["filed"]}
            fresh = [r for r in ptrs if r["doc_id"] not in self._seen_docs]
            if self._seen_docs:
                new = [{"name": r["name"], "district": r["district"], "filed": r["filed"], "url": r["url"]}
                       for r in fresh if pelosi_ptr.member_match(r, "pelosi")]
            self._seen_docs.update(r["doc_id"] for r in ptrs)
            src["house-clerk"] = {"ok": True, "ptrs": len(ptrs)}
        except Exception as e:  # noqa: BLE001  (the Clerk's site is optional)
            src["house-clerk"] = {"ok": False, "error": str(e)[:120]}
        self._free, self._free_at, self._filed = list(lists), time.time(), filed
        self._uw(lists, src)
        self._publish(lists, src, filed, new)

    _filed: dict = {}

    def _uw(self, lists: list, src: dict) -> None:
        if not self.uw_token:
            return
        try:
            req = urllib.request.Request(UW_CONGRESS + "?limit=100", headers={
                "Authorization": f"Bearer {self.uw_token}", "Accept": "application/json",
                "User-Agent": "ats-matrix-pelosi/1.0"})
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:  # noqa: S310
                rows = from_uw(json.loads(r.read() or b"{}").get("data", []))
            lists.append(rows)
            src["unusualwhales"] = {"ok": True, "rows": len(rows)}
        except Exception as e:  # noqa: BLE001
            src["unusualwhales"] = {"ok": False, "error": str(e)[:120]}

    def _publish(self, lists: list, src: dict, filed: dict, new: list) -> None:
        if lists:
            merged = merge(*lists, filed=filed)
            with self.lock:
                if merged:
                    self._trades = merged
                self.status_ = {"sources": src, "updated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                "new_ptrs": (new + self.status_.get("new_ptrs", []))[:20]}

    def trades(self) -> list[dict]:
        with self.lock:
            return list(self._trades)

    def status(self) -> dict:
        with self.lock:
            return dict(self.status_, trades=len(self._trades))

    def run(self, on_update=None, first_wait: float = 20.0) -> None:
        time.sleep(first_wait)  # let the app finish booting first
        while True:
            try:
                self.refresh()
                if on_update:
                    on_update()
            except Exception:  # noqa: BLE001  (never take the app down)
                pass
            time.sleep(UW_EVERY if self.uw_token else REFRESH_SECONDS)


def daily_closes(ticker: str, get=_get, days: str = "1y") -> tuple[list[str], list[float]]:
    """Yahoo daily closes (ISO dates) for the move between the trade date and today."""
    raw = json.loads(get(f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?interval=1d&range={days}"))
    res = raw["chart"]["result"][0]
    ds = [datetime.fromtimestamp(x, timezone.utc).date().isoformat() for x in res["timestamp"]]
    cs = res["indicators"]["quote"][0]["close"]
    pairs = [(d, c) for d, c in zip(ds, cs) if c]
    return [p[0] for p in pairs], [float(p[1]) for p in pairs]


def page_data(feed: Feed, book: PaperBook, price=None, members: list[str] | None = None) -> dict:
    """Everything /pelosi shows."""
    members = members or ["pelosi"]
    trades = feed.trades()
    pel = [t for t in trades if is_pelosi(t)][:60]
    def shown(t):
        if arm_of(t, members):
            return True
        return t["side"] == "sell" and bool(t["ticker"]) and (any(m in t["member"].lower() for m in members)
                                                               or (t["chamber"] == "house" and t["amount_low"] >= CONGRESS_MIN))
    signals = [dict(t, arm=arm_of(t, members)) for t in trades if shown(t)][:40]
    late = [t for t in trades if t["late"]][:20]
    return {"note": NOTE, "feed": feed.status(), "agents_see": sorted(
                ({"ticker": k, **v} for k, v in BIAS.map.items()), key=lambda x: -abs(x["bias"]))[:20], "members": members, "pelosi": pel, "signals": signals,
            "late": late, "book": book.snapshot(price), "backtest": load_backtest(), "hold_days": book.hold_days,
            "rules": {"entry": "first trading day after the announce date", "exit": f"{book.hold_days} days or "
                      "when the member's sale is announced", "congress_min": CONGRESS_MIN, "long_only": True,
                      "options": "followed with shares only"}}


def start(env: dict, price, closes=None) -> tuple[Feed, PaperBook]:
    """Start the feed thread (unless PELOSI_FEED=off) and step the paper book after every refresh."""
    global HOLD_DAYS, CONGRESS_MIN, CHASE_CAP, TREND_GATE
    HOLD_DAYS = int(env.get("PELOSI_HOLD_DAYS") or HOLD_DAYS)
    CONGRESS_MIN = float(env.get("PELOSI_CONGRESS_MIN") or CONGRESS_MIN)
    CHASE_CAP = float(env.get("PELOSI_CHASE_CAP") or CHASE_CAP)
    TREND_GATE = str(env.get("PELOSI_TREND_GATE", "on")).lower() not in ("off", "0", "false")
    feed = Feed(uw_token=str(env.get("UW_API_TOKEN") or "").strip())
    book = PaperBook(book=float(env.get("PELOSI_BOOK") or BOOK), hold_days=HOLD_DAYS)
    members = members_followed(env)
    if str(env.get("PELOSI_FEED", "on")).lower() not in ("off", "0", "false"):
        def updated() -> None:
            BIAS.publish(signal_map(feed.trades(), members, congress_min=CONGRESS_MIN), env.get("EVENTS_DIR") or None)
            book.step(feed.trades(), price, members, congress_min=CONGRESS_MIN, closes=closes)
        threading.Thread(target=feed.run, args=(updated,), daemon=True, name="pelosi-feed").start()
    return feed, book


# ---------------------------------------------------------------- what every agent sees

CONGRESS_SIGNALS = True  # CONGRESS_SIGNALS=off: the desks and the decision gate ignore Congress trades
SIGNAL_DAYS = 60  # an announced trade counts for 60 days after its announcement, fading to zero
BOOST_AGREE = 1.15
DAMP_AGAINST = 0.85


def signal_map(trades: list[dict], members: list[str], today: date | None = None,
               congress_min: float = CONGRESS_MIN) -> dict:
    """{ticker: {"bias": -1..1, "why": str}} from trades ANNOUNCED in the last SIGNAL_DAYS (never by trade date).
    Buys push up (more for Pelosi, bigger ranges, calls); announced sales push down. Fades with age."""
    today = today or datetime.now(timezone.utc).date()
    out: dict[str, dict] = {}
    for t in trades:
        ad = _d(t["announce_date"])
        if not ad or ad > today or (today - ad).days > SIGNAL_DAYS or not t["ticker"]:
            continue
        fade = 1 - (today - ad).days / SIGNAL_DAYS
        if t["side"] == "buy" and arm_of(t, members, congress_min):
            b = confidence(t) * 0.7 * fade
        elif t["side"] == "sell" and (is_pelosi(t) or any(m in t["member"].lower() for m in members)
                                      or t["amount_low"] >= congress_min):
            b = -0.3 * fade
        else:
            continue
        tk = ALIAS.get(t["ticker"], t["ticker"])
        cur = out.setdefault(tk, {"bias": 0.0, "why": []})
        cur["bias"] = max(-1.0, min(1.0, cur["bias"] + b))
        cur["why"].append(f"{t['member']} {t['side'].upper()} {t['amount']} traded {t['trade_date']}, "
                          f"announced {t['announce_date']}")
    return {k: {"bias": round(v["bias"], 3), "why": "; ".join(v["why"][:3])} for k, v in out.items()}


class CongressBias:
    """Shared between the web process (writes) and the engine desks (read) through $EVENTS_DIR/congress_signals.json,
    the same way whale prints travel."""

    SYNC_EVERY = 60.0

    def __init__(self) -> None:
        self.path: Path | None = None
        self.map: dict = {}
        self._synced = 0.0

    def follow(self, folder) -> None:
        self.path = Path(folder) / "congress_signals.json" if folder else None

    def publish(self, smap: dict, folder=None) -> None:
        self.map = smap
        path = Path(folder) / "congress_signals.json" if folder else self.path
        if not path:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"at": time.time(), "signals": smap}), encoding="utf-8")
            tmp.replace(path)
        except OSError:
            pass

    def _sync(self) -> None:
        if not self.path or time.time() - self._synced < self.SYNC_EVERY:
            return
        self._synced = time.time()
        try:
            self.map = json.loads(self.path.read_text(encoding="utf-8")).get("signals", {})
        except (OSError, ValueError):
            pass

    def bias(self, symbol: str) -> tuple[float, str]:
        if not CONGRESS_SIGNALS:
            return 0.0, ""
        self._sync()
        sym = str(symbol or "").upper().split("-")[0].split("/")[0]
        s = self.map.get(ALIAS.get(sym, sym))
        return (float(s["bias"]), s.get("why", "")) if s else (0.0, "")

    def boost(self, symbol: str, score: float, above_200: bool | None = None) -> float:
        """Desk score with Congress trades in mind: x1.15 when an announced trade agrees, x0.85 when it disagrees.
        An announced buy only lifts a long when the stock is above its 200-day average (trend gate)."""
        b, _ = self.bias(symbol)
        if not score or abs(b) < 0.1:
            return score
        if b > 0 and score > 0 and TREND_GATE and above_200 is False:
            return score
        return max(-1.0, min(1.0, score * (BOOST_AGREE if score * b > 0 else DAMP_AGAINST)))


    def sage_lean(self, symbol: str, news_lean: float, above_200: bool | None = None) -> float:
        """Sage's blackboard news lean +0.20 on a stock with an announced buy (mina's design), trend gate respected."""
        b, _ = self.bias(symbol)
        if b >= 0.1 and not (TREND_GATE and above_200 is False):
            return min(1.0, news_lean + SAGE_BOOST)
        return news_lean


SAGE_BOOST = 0.20
BIAS = CongressBias()
