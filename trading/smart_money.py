"""Smart money: what company insiders really bought and sold, from SEC EDGAR Form 4 filings. READ ONLY.

Ported from the "Gey" Mac system (Minaalex22/jarvis smart_money.py), keeping only the cloud-safe part: EDGAR needs no
broker, just a contact User-Agent (SEC rule), set as SEC_USER_AGENT="Name contact@email". Without it the page says so
and the fast gate's insider head abstains. moomoo's institutional data stays on the Mac.

Only open-market trades count (Form 4 code P = buy, S = sale); grants, option exercises, tax withholding and gifts are
left out. buy_ratio = buy $ / (buy $ + sell $): "buying" at >= 0.70, "selling" at <= 0.30, otherwise "mixed".
Routine 10b5-1 plan selling is common for big caps, so selling is weighed lightly and buying strongly.
"""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date, timedelta

BUY_THRESHOLD = 0.70
SELL_THRESHOLD = 0.30
CACHE_SECONDS = 6 * 3600
MIN_INTERVAL = 0.125  # under SEC's 10 requests a second
MAX_FILINGS = 40
TICKERS = ("AAPL", "MSFT", "NVDA", "AMZN", "GOOG", "TSLA", "META", "AMD")


def _txt(node, path):
    el = node.find(path)
    if el is None:
        return None
    v = el.findtext("value")
    if v is None:
        v = el.text
    return v.strip() if v and v.strip() else None


def _num(s):
    try:
        return float(s) if s is not None else None
    except ValueError:
        return None


def parse_form4(xml_bytes: bytes) -> tuple[list[dict], list[dict]]:
    root = ET.fromstring(xml_bytes)
    owners = []
    for ro in root.findall("reportingOwner"):
        rel = ro.find("reportingOwnerRelationship")
        role = []
        if rel is not None:
            if (rel.findtext("isDirector") or "").strip() in ("1", "true"):
                role.append("Director")
            if (rel.findtext("isOfficer") or "").strip() in ("1", "true"):
                role.append((rel.findtext("officerTitle") or "Officer").strip())
            if (rel.findtext("isTenPercentOwner") or "").strip() in ("1", "true"):
                role.append("10% Owner")
        owners.append({"name": (ro.findtext("reportingOwnerId/rptOwnerName") or "").strip(),
                       "title": ", ".join(role) or None})
    plan = (root.findtext("aff10b5One") or "").strip() in ("1", "true")
    notes = " ".join((f.text or "") for f in root.findall("footnotes/footnote")).lower()
    trades = []
    for t in root.findall("nonDerivativeTable/nonDerivativeTransaction"):
        code = (t.findtext("transactionCoding/transactionCode") or "").strip()
        if code not in ("P", "S"):
            continue
        shares = _num(_txt(t, "transactionAmounts/transactionShares"))
        price = _num(_txt(t, "transactionAmounts/transactionPricePerShare"))
        trades.append({"date": _txt(t, "transactionDate"), "side": "buy" if code == "P" else "sell",
                       "shares": shares, "price": price,
                       "value": round(shares * price, 2) if shares is not None and price is not None else None,
                       "plan_10b5_1": plan or "10b5-1" in notes})
    return owners, trades


def summarize(ticker: str, trades: list[dict]) -> dict:
    buys = [t for t in trades if t["side"] == "buy"]
    sells = [t for t in trades if t["side"] == "sell"]
    bd = sum(t["value"] or 0 for t in buys)
    sd = sum(t["value"] or 0 for t in sells)
    if not trades:
        signal, ratio = "no data", None
    else:
        ratio = bd / (bd + sd) if bd + sd else len(buys) / len(trades)
        signal = "buying" if ratio >= BUY_THRESHOLD else "selling" if ratio <= SELL_THRESHOLD else "mixed"
    people: dict[str, dict] = {}
    for t in trades:
        p = people.setdefault(t.get("name") or "?", {"name": t.get("name"), "title": t.get("title"),
                                                    "bought": 0.0, "sold": 0.0, "trades": 0, "last": t["date"]})
        p["bought" if t["side"] == "buy" else "sold"] += t["value"] or 0
        p["trades"] += 1
        p["last"] = max(p["last"] or "", t["date"] or "")
    top = sorted(people.values(), key=lambda p: -(p["bought"] + p["sold"]))[:5]
    return {"ticker": ticker, "signal": signal, "buy_ratio": None if ratio is None else round(ratio, 3),
            "buys": len(buys), "sells": len(sells), "buy_dollars": round(bd, 2), "sell_dollars": round(sd, 2),
            "planned_sales": sum(1 for t in sells if t["plan_10b5_1"]), "people": top,
            "recent": sorted(trades, key=lambda t: t["date"] or "", reverse=True)[:8]}


def vote(summary: dict | None) -> tuple[int, float, str]:
    """For the fast gate: buying counts strongly, selling lightly (routine plans), no data abstains."""
    if not summary or summary.get("signal") in (None, "no data", "mixed"):
        return 0, 0.0, (summary or {}).get("signal") or "no insider data"
    if summary["signal"] == "buying":
        return 1, 0.8, f"insiders buying ({summary['buys']} buys)"
    return -1, 0.3, f"insiders selling ({summary['sells']} sales, {summary['planned_sales']} planned)"


class SmartMoney:
    """Fetches in the background and caches, so a page load never waits on SEC."""

    def __init__(self, user_agent: str | None, fetch=None, days: int = 90):
        self.ua = (user_agent or "").strip()
        self.days = days
        self._fetch = fetch or self._http
        self.lock = threading.Lock()
        self.data: dict[str, dict] = {}
        self.at = 0.0
        self.error: str | None = None
        self.busy = False
        self._last = 0.0
        self._ciks: dict[str, int] | None = None

    def _http(self, url: str) -> bytes:
        wait = MIN_INTERVAL - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)
        self._last = time.monotonic()
        req = urllib.request.Request(url, headers={"User-Agent": self.ua, "Accept-Encoding": "identity"})
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.read()

    def insiders(self, ticker: str) -> dict:
        if self._ciks is None:
            raw = json.loads(self._fetch("https://www.sec.gov/files/company_tickers.json"))
            self._ciks = {v["ticker"].upper(): int(v["cik_str"]) for v in raw.values()}
        cik = self._ciks.get(ticker.upper())
        if cik is None:
            return summarize(ticker, [])
        sub = json.loads(self._fetch(f"https://data.sec.gov/submissions/CIK{cik:010d}.json"))
        recent = sub["filings"]["recent"]
        cutoff = (date.today() - timedelta(days=self.days)).isoformat()
        filings = [(recent["accessionNumber"][i], recent["primaryDocument"][i], recent["filingDate"][i])
                   for i in range(len(recent["form"]))
                   if recent["form"][i] in ("4", "4/A") and recent["filingDate"][i] >= cutoff][:MAX_FILINGS]
        trades = []
        for acc, doc, filed in filings:
            url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc.replace('-', '')}/{doc.split('/')[-1]}"
            try:
                owners, tr = parse_form4(self._fetch(url))
            except (ET.ParseError, urllib.error.URLError, OSError, ValueError):
                continue  # one bad filing must not sink the report
            name = "; ".join(o["name"] for o in owners) or None
            title = "; ".join(o["title"] for o in owners if o["title"]) or None
            trades += [{**t, "name": name, "title": title, "filed": filed} for t in tr if (t["date"] or "") >= cutoff]
        return summarize(ticker, trades)

    def refresh(self) -> None:
        with self.lock:
            if self.busy:
                return
            self.busy = True
        try:
            for t in TICKERS:
                s = self.insiders(t)
                with self.lock:
                    self.data[t] = s
            with self.lock:
                self.at, self.error = time.time(), None
        except Exception as e:  # noqa: BLE001 - shown on the page, never crashes the app
            with self.lock:
                self.error = f"{type(e).__name__}: {str(e)[:120]}"
                self.at = time.time()
        finally:
            with self.lock:
                self.busy = False

    def snapshot(self) -> dict:
        if not self.ua:
            return {"ready": False, "setup": "Add SEC_USER_AGENT (your name and email) in Render's Environment to "
                                             "switch on real insider data from SEC EDGAR.", "stocks": []}
        with self.lock:
            stale = time.time() - self.at > CACHE_SECONDS
        if stale:
            threading.Thread(target=self.refresh, daemon=True).start()
        with self.lock:
            return {"ready": bool(self.data), "busy": self.busy, "error": self.error, "as_of": self.at or None,
                    "source": "SEC EDGAR Form 4 (open-market trades only)", "days": self.days,
                    "stocks": [self.data[t] for t in TICKERS if t in self.data]}

    def summary(self, ticker: str) -> dict | None:
        with self.lock:
            return self.data.get(ticker.upper())
