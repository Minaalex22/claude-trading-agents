"""Live log dashboard. Tails every shard's events-*.jsonl and streams them to the browser.

Endpoints:
  /             live dashboard (installable as a home-screen app)
  /3d           3D cluster view: every department, manager and pipeline stage live,
                with TradingView price and P&L charts and the agent slider
  /api/org      departments and manager reports merged across shards (JSON)
  /api/prices   recent price and P&L history for the charts (JSON)
  /api/agents   GET the agent slider's setting; POST {"per_department": n} to resize
                every shard (JSON only, so other sites can't post to it)
  /events       Server-Sent Events stream of new events
  /api/state    latest tick, leaderboard, agent count per shard, ring heatmap
                and the Jev decision desk totals (JSON)
  /jarvis       JARVIS, the owner's voice assistant with the hologram (trading/jarvis.py)
  /api/jarvis   GET her status; POST /api/jarvis/unlock {"passphrase"} and /api/jarvis/ask {"question"}
  /api/platforms  real platform connectors: mode and whether keys are set (names only)
  /api/mt5      MetaTrader 5 account and open positions (read only, via MetaApi cloud)
  /api/mt5/report?days=30  where the money went: win rate, losers by symbol, costs, drawdown, notes
                POST /api/mt5/order {"symbol","side","volume"} sends a DEMO order; real accounts are refused
  /health       200 once at least one shard is reporting
  /api/training training report: win rate, exams, weakest departments, exits, confidence-gate test
  /api/report   read-only trade report (signed-in session, or bearer REPORT_TOKEN)
  /login        password sign-in when the dashboard is reachable from the internet
  /markets      Markets: 25 products with favourites, top risers/fallers and 5m/1d/40d change
  /markets/BTC  one product: candles on 6 timeframes, indicators, 1m/1h/4h/24h outlook,
                why it's moving, volume profile, and crypto order flow (trading/markets/)
  /api/markets  the board as JSON; /api/markets/<SYM>?tf=1h one product;
                POST /api/markets/<SYM>/favourite toggles a favourite

When auth is on (see trading/webauth.py), /, /3d, /events and the /api/
endpoints need a signed session cookie; /health then reports only {"ok": ...}.
"""

import hmac
import json
import os
import re
import urllib.parse
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import btcc, control, mitrade, live_orders, markets, metaapi, platforms, training_report
from .alerts import Alerts
from .atlas_sources import AtlasSources
from .jarvis import UNLOCK_COOKIE, Jarvis, snapshot
from .news_pipeline import NewsPipeline
from .polymarket_desk import PolymarketDesk
from . import polymarket_live, betfair
from .conductor import Conductor, departments as conductor_departments
from . import pairs as pairs_kit
from . import calculator as calc_kit
from . import news_lab
from . import decision_desk
from .smart_money import SmartMoney
from . import session_desks
from .org import DEPARTMENTS, finish_school, manager_reports
from .wallet import Wallet, WalletError
from .webauth import COOKIE, WebAuth

HISTORY = 500
PRICE_HISTORY = 600
WEB = Path(__file__).resolve().parent / "web"
# Every file the server will ever return, so no request path can reach anything else.
STATIC = {
    "/app.css": ("app.css", "text/css; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/command.css": ("command.css", "text/css; charset=utf-8"),
    "/command.js": ("command.js", "text/javascript; charset=utf-8"),
    "/holo-pages.css": ("holo-pages.css", "text/css; charset=utf-8"),
    "/3d.css": ("3d.css", "text/css; charset=utf-8"),
    "/3d.js": ("3d.js", "text/javascript; charset=utf-8"),
    "/jarvis.css": ("jarvis.css", "text/css; charset=utf-8"),
    "/jarvis.js": ("jarvis.js", "text/javascript; charset=utf-8"),
    "/orderflow.css": ("orderflow.css", "text/css; charset=utf-8"),
    "/orderflow.js": ("orderflow.js", "text/javascript; charset=utf-8"),
    "/markets.css": ("markets.css", "text/css; charset=utf-8"),
    "/galaxy.css": ("galaxy.css", "text/css; charset=utf-8"),
    "/galaxy.js": ("galaxy.js", "text/javascript; charset=utf-8"),
    "/duel.css": ("duel.css", "text/css; charset=utf-8"),
    "/theme.css": ("theme.css", "text/css; charset=utf-8"),
    "/nav.js": ("nav.js", "text/javascript; charset=utf-8"),
    "/pages.css": ("pages.css", "text/css; charset=utf-8"),
    "/news.js": ("news.js", "text/javascript; charset=utf-8"),
    "/trades.js": ("trades.js", "text/javascript; charset=utf-8"),
    "/pipeline.js": ("pipeline.js", "text/javascript; charset=utf-8"),
    "/footprint.js": ("footprint.js", "text/javascript; charset=utf-8"),
    "/kind.js": ("kind.js", "text/javascript; charset=utf-8"),
    "/kind.css": ("kind.css", "text/css; charset=utf-8"),
    "/polymarket.js": ("polymarket.js", "text/javascript; charset=utf-8"),
    "/polymarket.css": ("polymarket.css", "text/css; charset=utf-8"),
    "/conductor.js": ("conductor.js", "text/javascript; charset=utf-8"),
    "/conductor.css": ("conductor.css", "text/css; charset=utf-8"),
    "/pairs.js": ("pairs.js", "text/javascript; charset=utf-8"),
    "/decision.js": ("decision.js", "text/javascript; charset=utf-8"),
    "/decision.css": ("decision.css", "text/css; charset=utf-8"),
    "/smart.js": ("smart.js", "text/javascript; charset=utf-8"),
    "/pairs.css": ("pairs.css", "text/css; charset=utf-8"),
    "/desks.js": ("desks.js", "text/javascript; charset=utf-8"),
    "/desks.css": ("desks.css", "text/css; charset=utf-8"),
    "/pipeline.css": ("pipeline.css", "text/css; charset=utf-8"),
    "/duel.js": ("duel.js", "text/javascript; charset=utf-8"),
    "/markets.js": ("markets.js", "text/javascript; charset=utf-8"),
    "/market.js": ("market.js", "text/javascript; charset=utf-8"),
    "/wallet.js": ("wallet.js", "text/javascript; charset=utf-8"),
    "/wallet.css": ("wallet.css", "text/css; charset=utf-8"),
    "/calculator.js": ("calculator.js", "text/javascript; charset=utf-8"),
    "/calculator.css": ("calculator.css", "text/css; charset=utf-8"),
    "/newslab.js": ("newslab.js", "text/javascript; charset=utf-8"),
    "/newslab.css": ("newslab.css", "text/css; charset=utf-8"),
    "/btcc.js": ("btcc.js", "text/javascript; charset=utf-8"),
    "/btcc.css": ("btcc.css", "text/css; charset=utf-8"),
    "/mitrade.js": ("mitrade.js", "text/javascript; charset=utf-8"),
    # Vendored so the strict CSP (scripts only from this site) holds and the cloud app needs no CDN.
    "/vendor/three.min.js": ("vendor/three.min.js", "text/javascript; charset=utf-8"),
    "/vendor/lightweight-charts.js": ("vendor/lightweight-charts.js", "text/javascript; charset=utf-8"),
    "/sw.js": ("sw.js", "text/javascript; charset=utf-8"),
    "/manifest.webmanifest": ("manifest.webmanifest", "application/manifest+json"),
    "/offline.html": ("offline.html", "text/html; charset=utf-8"),
    "/icons/icon-192.png": ("icons/icon-192.png", "image/png"),
    "/icons/icon-512.png": ("icons/icon-512.png", "image/png"),
    "/icons/icon-maskable-512.png": ("icons/icon-maskable-512.png", "image/png"),
    "/icons/apple-touch-icon.png": ("icons/apple-touch-icon.png", "image/png"),
    "/icons/favicon-32.png": ("icons/favicon-32.png", "image/png"),
}
CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; connect-src 'self'; "
       "manifest-src 'self'; worker-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
MAX_FORM_BYTES = 4096
MAX_TRADES_BYTES = 262144  # a batch of real trades for the order flow feed
KIND_PAGES = ("/crypto", "/forex", "/commodities", "/indices", "/shares")
CLOSED_KEEP = 500  # closed trades the Trades page can list
DESK_PAGE = re.compile(r"/desks/(sydney|asia|london|newyork)")
DESK_API = re.compile(r"/api/desks/(sydney|asia|london|newyork)")
MARKET_PAGE = re.compile(r"/markets/[A-Za-z0-9]{1,10}")
MARKET_API = re.compile(r"/api/markets/[A-Za-z0-9]{1,10}")
MARKET_FAV = re.compile(r"/api/markets/[A-Za-z0-9]{1,10}/favourite")
# Only JARVIS's page may use the microphone; nothing may use the camera, location or payment APIs.
PERMISSIONS = "microphone=(self), camera=(), geolocation=(), payment=(), usb=(), interest-cohort=()"


class EventHub:
    def __init__(self, events_dir: Path):
        self.events_dir = events_dir
        self.events: deque[tuple[int, dict]] = deque(maxlen=HISTORY)
        self.seq = 0
        self.shards: dict[str, dict] = {}
        self.cond = threading.Condition()
        self.offsets: dict[Path, tuple[int, int]] = {}  # path -> (inode, offset)
        # Every engine shard sees the same market, so one shard's ticks give the price history.
        self.price_history: deque[dict] = deque(maxlen=PRICE_HISTORY)
        # Atlas's extra eyes: in-house ATAS-style order flow + the 20 analysis houses' street view.
        self.sources = AtlasSources()
        self.equity: dict[int, dict[str, float]] = {}
        # Closed round trips for the Trades page: the latest ones plus running totals since the app started.
        self.closed: deque[dict] = deque(maxlen=CLOSED_KEEP)
        self.pipeline = NewsPipeline()  # every headline: triage -> gate -> deep read -> tags -> bill
        self.closed_totals = {"trades": 0, "won": 0, "lost": 0, "pnl": 0.0, "by_symbol": {}}

    def poll_files(self) -> None:
        for path in sorted(self.events_dir.glob("events-*.jsonl")):
            with open(path, "rb") as f:
                st = os.fstat(f.fileno())
                inode, offset = self.offsets.get(path, (st.st_ino, 0))
                if inode != st.st_ino or st.st_size < offset:
                    # The engine rotated the file. Finish the old one (now <name>.1)
                    # first so no events are lost, then read the new one from the top.
                    self._finish_rotated(path, inode, offset)
                    offset = 0
                f.seek(offset)
                chunk = f.read()
            end = chunk.rfind(b"\n") + 1  # only consume complete lines
            if not end:
                self.offsets[path] = (st.st_ino, offset)
                continue
            self.offsets[path] = (st.st_ino, offset + end)
            self._consume(chunk[:end])

    def _finish_rotated(self, path: Path, inode: int, offset: int) -> None:
        backup = path.with_name(path.name + ".1")
        try:
            with open(backup, "rb") as f:
                if os.fstat(f.fileno()).st_ino != inode:
                    return  # rotated more than once since the last poll; that tail is gone
                f.seek(offset)
                self._consume(f.read())
        except FileNotFoundError:
            pass

    def _consume(self, chunk: bytes) -> None:
        for line in chunk.splitlines():
            try:
                self.add(json.loads(line))
            except ValueError:
                continue

    def add(self, event: dict) -> None:
        with self.cond:
            self.seq += 1
            self.events.append((self.seq, event))
            shard = self.shards.setdefault(str(event.get("shard", "?")), {})
            kind = event.get("type")
            if kind in ("start", "tick", "leaderboard", "stop", "heatmap", "matrix", "org", "market"):
                shard[kind] = event
            if kind == "resize" and "start" in shard:
                shard["start"] = {**shard["start"], "agents": event["agents"], "total_agents": event["total_agents"]}
            if kind == "closed":
                self._record_closed(event)
            if kind == "org" and event.get("news"):
                views = (event.get("reading_room") or {}).get("views") or {}
                self.pipeline.feed(event["news"], views)
            if kind == "tick" and "prices" in event and isinstance(event.get("shard"), int):
                self._record_tick(event)  # engine shards only; the ring and matrix trade other books
            self.cond.notify_all()

    def _record_closed(self, event: dict) -> None:
        won, pnl = bool(event.get("won")), float(event.get("pnl") or 0.0)
        self.closed.append({k: event.get(k) for k in ("ts", "shard", "symbol", "side", "qty", "entry", "exit", "move", "won", "pnl")})
        t = self.closed_totals
        t["trades"] += 1
        t["won" if won else "lost"] += 1
        t["pnl"] += pnl
        sym = t["by_symbol"].setdefault(str(event.get("symbol")), {"trades": 0, "won": 0, "pnl": 0.0})
        sym["trades"] += 1
        sym["won"] += won
        sym["pnl"] += pnl

    def trades(self) -> dict:
        """Every closed paper trade we still hold (newest first), with wins, losses and money made or lost."""
        with self.cond:
            t = self.closed_totals
            recent = list(self.closed)[::-1]
            by_symbol = {k: {**v, "pnl": round(v["pnl"], 2), "win_rate": round(v["won"] / v["trades"], 4)}
                         for k, v in sorted(t["by_symbol"].items(), key=lambda kv: -kv[1]["pnl"])}
            return {"trades": recent, "totals": {"trades": t["trades"], "won": t["won"], "lost": t["lost"],
                                                 "win_rate": round(t["won"] / t["trades"], 4) if t["trades"] else None,
                                                 "pnl": round(t["pnl"], 2)}, "by_symbol": by_symbol}

    def _record_tick(self, event: dict) -> None:
        tick = event["tick"]
        self.equity.setdefault(tick, {})[str(event.get("shard"))] = event.get("pnl", 0.0)
        if len(self.equity) > PRICE_HISTORY:
            del self.equity[min(self.equity)]
        if not self.price_history or tick > self.price_history[-1]["tick"]:
            self.price_history.append({"tick": tick, "ts": event["ts"], "prices": event["prices"]})
            self.sources.on_prices(event["prices"], event["ts"])

    def prices(self) -> dict:
        with self.cond:
            # Only ticks every shard has reported, so a lagging shard doesn't dent the line.
            shards = max((len(v) for v in self.equity.values()), default=0)
            pnl = [{"tick": t, "pnl": round(sum(v.values()), 2)} for t, v in sorted(self.equity.items())
                   if len(v) == shards]
            return {"history": list(self.price_history), "pnl": pnl}

    def org(self) -> dict:
        """Merge every shard's latest department report into one org chart."""
        with self.cond:
            reports = [v["org"] for v in self.shards.values() if "org" in v]
        merged = {d.key: {"name": d.name, "job": d.job, "agents": 0, "active": 0, "sitting_out": 0, "pnl": 0.0,
                          "votes": [], "vote_sum": 0.0, "weight_sum": 0.0, "wins": 0, "experience": 0, "held": 0,
                          "macro_sum": 0.0, "macro_count": 0, "traders": 0, "trades_hour_sum": 0,
                          "trades_hour_min": None, "trades_total": 0, "masters_used": {},
                          "trade_wins": 0, "trade_losses": 0, "practice_wins": 0, "practice_losses": 0,
                          "reviews_learned": 0, "exits": {}, "by_sure": {},
                          "loss_causes": {}, "filtered": {}, "calibration": None,
                          "school": {"trained": 0, "graduates": 0, "exam_sum": 0.0, "course_sums": {}}}
                  for d in DEPARTMENTS}
        for report in reports:
            for key, dept in report["departments"].items():
                m = merged.get(key)
                if m is None:
                    continue
                m["agents"] += dept["agents"]
                m["active"] += dept["active"]
                m["pnl"] += dept["pnl"]
                m["votes"].extend(dept["votes"])
                m["sitting_out"] += dept.get("sitting_out", 0)
                m["wins"] += dept.get("wins", 0)
                m["experience"] += dept.get("experience", 0)
                m["held"] += dept.get("held", 0)
                for k in ("traders", "trades_hour_sum", "trades_total", "trade_wins", "trade_losses",
                          "practice_wins", "practice_losses", "reviews_learned"):
                    m[k] += dept.get(k, 0)
                if dept.get("trades_hour_min") is not None:
                    m["trades_hour_min"] = (dept["trades_hour_min"] if m["trades_hour_min"] is None
                                            else min(m["trades_hour_min"], dept["trades_hour_min"]))
                for reason, n in dept.get("exits", {}).items():
                    m["exits"][reason] = m["exits"].get(reason, 0) + n
                for k in ("loss_causes", "filtered"):
                    for cause, n in dept.get(k, {}).items():
                        m[k][cause] = m[k].get(cause, 0) + n
                if dept.get("calibration") and not m["calibration"]:
                    m["calibration"] = dept["calibration"]
                for b, (w, n) in dept.get("by_sure", {}).items():
                    row = m["by_sure"].setdefault(b, [0, 0])
                    row[0] += w
                    row[1] += n
                for course, n in dept.get("masters_used", {}).items():
                    m["masters_used"][course] = m["masters_used"].get(course, 0) + n
                m["macro_sum"] += dept.get("macro_sum", 0.0)
                m["macro_count"] += dept.get("macro_count", 0)
                school = dept.get("school")
                if school:
                    ms = m["school"]
                    for k in ("trained", "graduates", "exam_sum"):
                        ms[k] += school[k]
                    for k, v in school["course_sums"].items():
                        ms["course_sums"][k] = ms["course_sums"].get(k, 0.0) + v
                if dept.get("avg_weight") is not None:
                    m["weight_sum"] += dept["avg_weight"] * dept["agents"]
                if dept["avg_vote"] is not None:
                    m["vote_sum"] += dept["avg_vote"] * dept["active"]
        for m in merged.values():
            m["avg_vote"] = round(m.pop("vote_sum") / m["active"], 4) if m["active"] else None
            m["avg_weight"] = round(m.pop("weight_sum") / m["agents"], 4) if m["agents"] else None
            m["skill"] = round(m["wins"] / m["experience"], 4) if m["experience"] else None
            closed = m["trade_wins"] + m["trade_losses"]
            m["trade_win_rate"] = round(m["trade_wins"] / closed, 4) if closed else None
            practice = m["practice_wins"] + m["practice_losses"]
            m["practice_win_rate"] = round(m["practice_wins"] / practice, 4) if practice else None
            m["pnl"] = round(m["pnl"], 2)
            finish_school(m)
        tick = max((r["tick"] for r in reports), default=0)
        latest = max(reports, key=lambda r: r["tick"], default={})
        per_department = control.read(self.events_dir)
        return {"tick": tick, "departments": merged, "managers": manager_reports(merged), "news": latest.get("news", []),
                "practice": latest.get("practice"), "reading_room": latest.get("reading_room"),
                "slider": {"per_department": per_department, "min": control.MIN_DEPARTMENT_SIZE,
                           "max": control.MAX_DEPARTMENT_SIZE, "departments": len(DEPARTMENTS)}}

    def since(self, seq: int, timeout: float) -> list[tuple[int, dict]]:
        with self.cond:
            if self.seq <= seq:
                self.cond.wait(timeout)
            return [(s, e) for s, e in self.events if s > seq]

    def state(self) -> dict:
        with self.cond:
            shards = dict(sorted(self.shards.items()))
            total = sum(v["start"].get("agents", 0) for v in shards.values() if "start" in v)
            ticks = [v["tick"] for v in shards.values() if "tick" in v]
            approved = sum(t.get("jev", {}).get("approved", 0) for t in ticks)
            rejected = sum(t.get("jev", {}).get("rejected", 0) for t in ticks)
            heatmap = shards.get("ring", {}).get("heatmap", {}).get("bots", {})
            desk = {"approved": approved, "rejected": rejected,
                    "approval_rate": round(approved / (approved + rejected), 4) if approved + rejected else None,
                    "session_pnl": round(sum(t.get("pnl", 0) for t in ticks), 2)}
            return {"shards": shards, "agents_reporting": total, "events_seen": self.seq,
                    "heatmap": heatmap, "jev_desk": desk}

    def run_tailer(self, interval: float = 0.25) -> None:
        while True:
            try:
                self.poll_files()
            except OSError:
                pass
            time.sleep(interval)


def _read(name: str) -> bytes:
    return (WEB / name).read_bytes()


MIN_BRIDGE_TOKEN = 32


def trade_report(hub: "EventHub", events_dir: Path, env=os.environ) -> dict:
    """A read-only summary of the trading: paper desk money, orders, wins and losses, agents, stops, real money."""
    st, org = hub.state(), hub.org()
    engines = {k: v for k, v in st["shards"].items() if k.isdigit()}
    ticks = [v["tick"] for v in engines.values() if "tick" in v]
    risks = [t["risk"] for t in ticks if isinstance(t.get("risk"), dict)]
    depts = org.get("departments", {})
    wins = sum(d.get("trade_wins", 0) for d in depts.values())
    losses = sum(d.get("trade_losses", 0) for d in depts.values())
    top = sorted((r for v in engines.values() for r in (v.get("leaderboard") or {}).get("top", []) if r.get("account")),
                 key=lambda r: (r["account"]["proven"], r["account"]["equity"]), reverse=True)[:5]
    live = live_orders.snapshot(events_dir, live_orders.Limits(env))
    counts: dict[str, int] = {}
    for o in live["orders"]:
        counts[o["status"]] = counts.get(o["status"], 0) + 1
    starts = [v["start"] for v in engines.values() if "start" in v]
    market = next((s.get("market") for s in starts if s.get("market")), None)
    rep = {
        "generated_at": round(time.time()), "market": market, "agents": sum(s.get("agents", 0) for s in starts),
        "tick": max((t.get("tick", 0) for t in ticks), default=0),
        "equity": round(sum(t.get("equity", 0) for t in ticks), 2), "session_pnl": round(sum(t.get("pnl", 0) for t in ticks), 2),
        "today_pnl": round(sum(r.get("day_pnl", 0) for r in risks), 2),
        "orders": st["jev_desk"]["approved"], "jev_rejected": st["jev_desk"]["rejected"],
        "agent_trades": {"won": wins, "lost": losses, "win_rate": round(wins / (wins + losses), 4) if wins + losses else None},
        "proven_agents": sum(t.get("proven", 0) for t in ticks), "top_agents": top,
        "stops": sorted({t["stopped"] for t in ticks if t.get("stopped")}),
        "stale_prices": sorted({s for t in ticks for s in t.get("stale", [])}),
        "drawdown": max((r.get("drawdown", 0) for r in risks), default=0),
        "real_money": {"limits_on": live["limits"]["on"], "spent_today": live["spent_today"], "orders": counts,
                       "audit": live["audit"],
                       "positions": live["positions"], "latest": live["orders"][:5]},
        "training": training_report.build(org),
        "ring": {k: st["shards"]["ring"]["tick"].get(k) for k in ("equity", "pnl")} if "tick" in st["shards"].get("ring", {}) else None,
    }
    return rep


def make_handler(hub: EventHub, auth: WebAuth | None = None, jarvis: Jarvis | None = None, env=os.environ):
    auth = auth or WebAuth(None, None, enabled=False)
    # Real-money bridge (trading/live_orders.py, trading/bridge.py): off unless a long BRIDGE_TOKEN is set.
    bridge_token = env.get("BRIDGE_TOKEN", "")
    bridge_on = len(bridge_token) >= MIN_BRIDGE_TOKEN
    alerts = Alerts.from_env(env)  # fills and failures from the bridge go to the owner's phone
    # Read-only trade report for an assistant or script: a bearer REPORT_TOKEN (32+ characters) or a signed-in session.
    report_token = env.get("REPORT_TOKEN", "")
    report_on = len(report_token) >= MIN_BRIDGE_TOKEN
    jarvis = jarvis or Jarvis(None, required=auth.enabled)
    assets = {path: (_read(name), ctype) for path, (name, ctype) in STATIC.items()}
    index = _read("index.html")
    cluster_page = _read("3d.html")
    log_page = _read("log.html")
    jarvis_page = _read("jarvis.html")
    orderflow_page = _read("orderflow.html")
    markets_page = _read("markets.html")
    galaxy_page = _read("galaxy.html")
    duel_page = _read("duel.html")
    news_page = _read("news.html")
    trades_page = _read("trades.html")
    pipeline_page = _read("pipeline.html")
    kind_page = _read("kind.html")  # one page per market type: /crypto /forex /commodities /indices /shares
    polymarket_page = _read("polymarket.html")
    poly = {}  # the Polymarket practice desk, started on first use
    poly_lock = threading.Lock()

    def get_poly_book() -> polymarket_live.PaperBook:
        """Real Polymarket prices (read only) and the paper betting book, made on first use."""
        with poly_lock:
            if "book" not in poly:
                feed = polymarket_live.MarketFeed()
                poly["book"] = polymarket_live.PaperBook(feed, hub.events_dir)
            return poly["book"]

    def get_betfair() -> betfair.Bridge:
        with poly_lock:
            if "bf" not in poly:
                poly["bf"] = betfair.Bridge(env, hub.events_dir)
            return poly["bf"]

    def poly_wallet() -> dict | None:
        total, seen = 0.0, False
        if "desk" in poly:
            total += poly["desk"].snapshot()["total"]; seen = True
        if "book" in poly:
            total += poly["book"].snapshot()["pnl"]; seen = True
        return {"total": round(total, 2)} if seen else None
    smart = SmartMoney(env.get("SEC_USER_AGENT"))  # insiders from SEC EDGAR, fetched in the background
    conductor_page = _read("conductor.html")
    pairs_page = _read("pairs.html")
    decision_page = _read("decision.html")
    smart_page = _read("smart.html")
    desks_page = _read("desks.html")  # the four session desks: /desks and /desks/<sydney|asia|london|newyork>
    floor = {}  # the six-agent practice floor, started on first use
    floor_lock = threading.Lock()
    market_page = _read("market.html")
    wallet_page = _read("wallet.html")
    calculator_page = _read("calculator.html")
    newslab_page = _read("newslab.html")
    wallet = {}  # the practice wallet, loaded on first use
    wallet_lock = threading.Lock()
    btcc_page = _read("btcc.html")
    btcc_desk = {}  # the BTCC practice desk, loaded on first use

    mitrade_page = _read("mitrade.html")
    mitrade_desk = {}  # the Mitrade practice desk, loaded on first use

    def get_mitrade() -> mitrade.PracticeDesk:
        with wallet_lock:
            if "d" not in mitrade_desk:
                mitrade_desk["d"] = mitrade.PracticeDesk(hub.events_dir)
            return mitrade_desk["d"]

    def get_btcc() -> btcc.PracticeDesk:
        with wallet_lock:
            if "d" not in btcc_desk:
                btcc_desk["d"] = btcc.PracticeDesk(hub.events_dir)
            return btcc_desk["d"]

    def get_wallet() -> Wallet:
        with wallet_lock:
            if "w" not in wallet:
                wallet["w"] = Wallet(hub.events_dir, env=env)
            return wallet["w"]

    login_page = _read("login.html").decode()

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        timeout = 60  # close idle or stalled connections (e.g. a request sent byte by byte)

        def log_message(self, *args):
            pass

        def _headers(self, status: int, ctype: str, length: int | None = None, extra: dict | None = None):
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            if length is not None:
                self.send_header("Content-Length", str(length))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            # "same-origin", not "no-referrer": with no-referrer, browsers send `Origin: null` on our own
            # form posts, and the CSRF check below then refused every sign-in. Nothing leaks cross-site either way.
            self.send_header("Referrer-Policy", "same-origin")
            self.send_header("Content-Security-Policy", CSP)
            self.send_header("Permissions-Policy", PERMISSIONS)
            self.send_header("Cross-Origin-Opener-Policy", "same-origin")
            self.send_header("Cross-Origin-Resource-Policy", "same-origin")
            if auth.enabled and self._secure():  # no HSTS for this computer's own plain-http address
                self.send_header("Strict-Transport-Security", "max-age=31536000")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()

        def _send(self, status: int, body: bytes, ctype: str, extra: dict | None = None):
            self._headers(status, ctype, len(body), extra)
            self.wfile.write(body)

        def _redirect(self, to: str, extra: dict | None = None):
            self._send(303, b"", "text/plain", {"Location": to, **(extra or {})})

        def _secure(self) -> str:
            """'; Secure' except on this computer's own address (plain http there, and Safari drops Secure cookies)."""
            host = self.headers.get("Host", "").rsplit(":", 1)[0].strip("[]")
            return "" if host in ("127.0.0.1", "localhost", "::1") else "; Secure"

        def _cookie(self, value: str, max_age: int) -> str:
            return f"{COOKIE}={value}; Path=/; Max-Age={max_age}; HttpOnly{self._secure()}; SameSite=Lax"

        def _session(self, cookie: str = COOKIE) -> str | None:
            for part in self.headers.get("Cookie", "").split(";"):
                name, _, value = part.strip().partition("=")
                if name == cookie:
                    return value
            return None

        def _json(self, status: int, body: dict, extra: dict | None = None):
            self._send(status, json.dumps(body).encode(), "application/json", {"Cache-Control": "no-store", **(extra or {})})

        def _same_origin(self) -> bool:
            """Refuse POSTs a browser sent from another site (CSRF). Non-browser clients send no Origin."""
            origin = self.headers.get("Origin")
            if not origin:
                return True
            if origin == "null":
                # Some browsers (privacy modes, older policies) send `Origin: null` on same-site form posts.
                # Fetch metadata says where the request really came from; a cross-site post is never "same-origin".
                return self.headers.get("Sec-Fetch-Site") == "same-origin" or not auth.enabled
            return urllib.parse.urlsplit(origin).netloc == self.headers.get("Host", "")

        def _jarvis_unlocked(self) -> bool:
            return jarvis.unlocked(self._session(UNLOCK_COOKIE))

        def _authed(self) -> bool:
            return auth.valid(self._session())

        def _client(self) -> str:
            # Behind Render's proxy the client address is the first X-Forwarded-For entry.
            forwarded = self.headers.get("X-Forwarded-For", "")
            return forwarded.split(",")[0].strip() or self.client_address[0]

        def do_GET(self):
            path = urllib.parse.urlsplit(self.path).path
            if path in assets:
                body, ctype = assets[path]
                self._send(200, body, ctype, {"Cache-Control": "no-cache" if path == "/sw.js" else "public, max-age=3600"})
            elif path == "/health":
                state = hub.state()
                ok = state["agents_reporting"] > 0
                body = {"ok": ok} if auth.enabled else {"ok": ok, **state}
                self._send(200 if ok else 503, json.dumps(body).encode(), "application/json")
            elif path == "/api/report" and (self._authed() or self._report_authed()):
                self._json(200, trade_report(hub, hub.events_dir, env))
            elif path == "/api/training" and (self._authed() or self._report_authed()):
                self._json(200, training_report.build(hub.org()))
            elif path == "/api/bridge/next":
                if not self._bridge_authed():
                    return self._json(401, {"error": "bridge token required"})
                self._json(200, {"order": live_orders.next_for_bridge(hub.events_dir)})
            elif path == "/login":
                if not auth.enabled or self._authed():
                    return self._redirect("/news")  # News is the first page
                query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
                message = {"1": "Wrong password or code." if auth.two_step else "Wrong password.",
                           "locked": "Too many attempts. Try again in 15 minutes."}
                error = message.get(query.get("error", [""])[0], "")
                page = login_page.replace("<!--ERROR-->", f'<div class="err">{error}</div>' if error else "")
                if auth.two_step:
                    page = page.replace("<!--CODE-->", '<input type="text" name="code" placeholder="6-digit code" '
                                        'inputmode="numeric" pattern="[0-9 ]{6,7}" autocomplete="one-time-code" required>')
                self._send(200, page.encode(), "text/html; charset=utf-8", {"Cache-Control": "no-store"})
            elif not self._authed():
                if path in ("/", "/3d", "/jarvis", "/log", "/orderflow", "/markets", "/galaxy", "/duel", "/news", "/trades", "/pipeline", "/polymarket", "/conductor", "/pairs", "/desks", "/wallet", "/btcc", "/mitrade", "/calculator", "/newslab") or path in KIND_PAGES or DESK_PAGE.fullmatch(path) or MARKET_PAGE.fullmatch(path):
                    self._redirect("/login")
                else:
                    self._send(401, b'{"error":"sign in required"}', "application/json")
            elif path == "/":
                self._send(200, index, "text/html; charset=utf-8", {"Cache-Control": "no-store"})
            elif path == "/log":
                self._send(200, log_page, "text/html; charset=utf-8", {"Cache-Control": "no-store"})
            elif path == "/3d":
                self._send(200, cluster_page, "text/html; charset=utf-8", {"Cache-Control": "no-store"})
            elif path == "/jarvis":
                self._send(200, jarvis_page, "text/html; charset=utf-8", {"Cache-Control": "no-store"})
            elif path == "/orderflow":
                self._send(200, orderflow_page, "text/html; charset=utf-8", {"Cache-Control": "no-store"})
            elif path == "/api/orderflow":
                self._json(200, hub.sources.page_data())
            elif path == "/api/atlas/check":
                query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
                symbol = query.get("symbol", [""])[0][:32]
                side = query.get("side", ["buy"])[0]
                if not symbol or side not in ("buy", "sell"):
                    return self._json(400, {"error": "symbol and side=buy|sell required"})
                self._json(200, hub.sources.check(symbol, side))
            elif path == "/api/jarvis":
                self._json(200, jarvis.status(self._jarvis_unlocked()))
            elif path == "/api/live":
                self._json(200, {**live_orders.snapshot(hub.events_dir, live_orders.Limits(env)),
                                 "bridge": bridge_on, "two_step": auth.two_step})
            elif path == "/api/platforms":
                self._json(200, {"platforms": platforms.status(), "live_trading": "locked"})
            elif path in ("/api/mt5", "/api/mt5/report"):
                self._mt5_get(path)
            elif path == "/mitrade":
                self._send(200, mitrade_page, "text/html; charset=utf-8", {"Cache-Control": "no-store"})
            elif path == "/api/mitrade":
                desk = get_mitrade()
                self._json(200, {"feed": mitrade.FEED.status(), "quotes": mitrade.FEED.quotes(),
                                 "trend": mitrade.FEED.trend(), "practice": desk.snapshot(),
                                 "account": {"linked": False, "note": mitrade.ACCOUNT_NOTE},
                                 "links": mitrade.LINKS, "live_trading": "locked"})
            elif path == "/btcc":
                self._send(200, btcc_page, "text/html; charset=utf-8", {"Cache-Control": "no-store"})
            elif path == "/api/btcc":
                desk = get_btcc()
                self._json(200, {"account": btcc.live_account(env), "practice": desk.snapshot(), "marks": desk.marks(),
                                 "news": btcc.news(), "links": btcc.LINKS, "live_trading": "locked"})
            elif path == "/api/state":
                self._send(200, json.dumps(hub.state()).encode(), "application/json", {"Cache-Control": "no-store"})
            elif path == "/api/pipeline":
                with hub.cond:
                    snap = hub.pipeline.snapshot()
                self._send(200, json.dumps(snap).encode(), "application/json", {"Cache-Control": "no-store"})
            elif path == "/api/trades":
                self._send(200, json.dumps(hub.trades()).encode(), "application/json", {"Cache-Control": "no-store"})
            elif path == "/api/org":
                self._send(200, json.dumps(hub.org()).encode(), "application/json", {"Cache-Control": "no-store"})
            elif path == "/api/prices":
                self._send(200, json.dumps(hub.prices()).encode(), "application/json", {"Cache-Control": "no-store"})
            elif path == "/api/agents":
                body = {"per_department": control.read(hub.events_dir)}
                self._send(200, json.dumps(body).encode(), "application/json", {"Cache-Control": "no-store"})
            elif path == "/galaxy":
                self._send(200, galaxy_page, "text/html; charset=utf-8", {"Cache-Control": "no-store"})
            elif path == "/duel":
                self._send(200, duel_page, "text/html; charset=utf-8", {"Cache-Control": "no-store"})
            elif path == "/news":
                self._send(200, news_page, "text/html; charset=utf-8", {"Cache-Control": "no-store"})
            elif path == "/trades":
                self._send(200, trades_page, "text/html; charset=utf-8", {"Cache-Control": "no-store"})
            elif path == "/polymarket":
                self._send(200, polymarket_page, "text/html; charset=utf-8", {"Cache-Control": "no-store"})
            elif path == "/api/polymarket":
                if "desk" not in poly:
                    poly["desk"] = PolymarketDesk(env=env)
                self._send(200, json.dumps(poly["desk"].snapshot()).encode(), "application/json", {"Cache-Control": "no-store"})
            elif path == "/api/polymarket/markets":
                query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
                book = get_poly_book()
                view = book.feed.view(query.get("q", [""])[0], query.get("cat", ["all"])[0][:20])
                book.record_leans(book.feed.cache["markets"])
                self._json(200, view)
            elif path == "/api/polymarket/real":
                self._json(200, {"available": False, "geoblock": polymarket_live.geoblock(), "note": polymarket_live.REAL_MONEY_NOTE})
            elif path == "/api/betfair":
                bf = get_betfair()
                self._json(200, {"status": bf.status(), "book": bf.snapshot()})
            elif path == "/api/betfair/search":
                query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
                try:
                    self._json(200, {"markets": get_betfair().search(query.get("q", [""])[0][:80])})
                except Exception as e:  # noqa: BLE001
                    self._json(502, {"error": str(e)[:200]})
            elif path == "/api/betfair/matches":
                book = get_poly_book()
                picks = book.feed.view()["picks"]
                try:
                    self._json(200, {"matches": get_betfair().matches(picks)})
                except Exception as e:  # noqa: BLE001
                    self._json(502, {"error": str(e)[:200]})
            elif path == "/api/polymarket/book":
                book = get_poly_book()
                book.feed.markets()
                book.settle()
                self._json(200, book.snapshot())
            elif path == "/wallet":
                self._send(200, wallet_page, "text/html; charset=utf-8", {"Cache-Control": "no-store"})
            elif path == "/api/wallet":
                latest = hub.price_history[-1]["prices"] if hub.price_history else {}
                snap = get_wallet().snapshot(desk=hub.trades(), polymarket=poly_wallet(),
                                             platforms=platforms.status(env), prices=latest)
                self._send(200, json.dumps(snap).encode(), "application/json", {"Cache-Control": "no-store"})
            elif path == "/conductor":
                self._send(200, conductor_page, "text/html; charset=utf-8", {"Cache-Control": "no-store"})
            elif path == "/api/conductor":
                with floor_lock:
                    if "c" not in floor:
                        floor["c"] = Conductor()
                snap = floor["c"].snapshot()
                snap["org"] = conductor_departments(hub.org(), snap, control.read(hub.events_dir) or control.MIN_DEPARTMENT_SIZE)
                self._send(200, json.dumps(snap).encode(), "application/json", {"Cache-Control": "no-store"})
            elif path == "/decision":
                self._send(200, decision_page, "text/html; charset=utf-8", {"Cache-Control": "no-store"})
            elif path == "/api/decision":
                prices_now = hub.prices()
                hist = prices_now.get("history") or []
                with hub.cond:
                    leans = {sym: hub.pipeline.lean(sym) for sym in (hist[-1]["prices"] if hist else {})}
                body = decision_desk.build(hub.org(), hub.trades(), prices_now, getattr(hub, "sources", None), smart, leans)
                self._send(200, json.dumps(body).encode(), "application/json", {"Cache-Control": "no-store"})
            elif path == "/smart":
                self._send(200, smart_page, "text/html; charset=utf-8", {"Cache-Control": "no-store"})
            elif path == "/api/smart":
                body = smart.snapshot()
                body["street"] = hub.sources.street.board() if getattr(hub, "sources", None) else []
                self._send(200, json.dumps(body).encode(), "application/json", {"Cache-Control": "no-store"})
            elif path == "/newslab":
                self._send(200, newslab_page, "text/html; charset=utf-8", {"Cache-Control": "no-store"})
            elif path == "/api/newslab":
                q = {k: v[0][:20] for k, v in urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query).items()}
                body = news_lab.lab(q)
                with hub.cond:
                    log = list(hub.pipeline.log)[::-1]
                body["live"] = news_lab.reroute(log, body["params"]["gate"])
                self._json(200, body)
            elif path == "/calculator":
                self._send(200, calculator_page, "text/html; charset=utf-8", {"Cache-Control": "no-store"})
            elif path == "/api/calculator":
                q = {k: v[0][:30] for k, v in urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query).items()}
                if not q.get("symbol"):
                    self._json(200, {"products": calc_kit.specs(), "defaults": {
                        "risk_pct": calc_kit.DEFAULT_RISK_PCT, "rr": calc_kit.DEFAULT_RR,
                        "rule_max_pct": calc_kit.RULE_MAX_PCT, "rule_max_usd": calc_kit.RULE_MAX_USD}})
                else:
                    fx = {}
                    for pair in ("USDJPY", "USDCAD", "AUDUSD", "EURUSD"):
                        try:
                            v = float(q.get(pair.lower(), ""))
                            if 0 < v < 1000:
                                fx[pair] = v
                        except ValueError:
                            pass
                    try:
                        self._json(200, calc_kit.calculate(q["symbol"], q.get("balance"), q.get("entry"), q.get("stop"),
                                                           q.get("target"), q.get("risk", calc_kit.DEFAULT_RISK_PCT),
                                                           q.get("leverage"), q.get("win"), fx))
                    except (KeyError, ValueError) as e:
                        self._json(400, {"error": str(e).strip("'\"")})
            elif path == "/pairs":
                self._send(200, pairs_page, "text/html; charset=utf-8", {"Cache-Control": "no-store"})
            elif path == "/api/pairs":
                query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
                try:
                    self._json(200, pairs_kit.pair_study(query.get("pair", ["sim"])[0][:20].lower()))
                except (KeyError, ValueError) as e:
                    self._json(404, {"error": str(e).strip("'\"")})
            elif path == "/desks" or DESK_PAGE.fullmatch(path):
                self._send(200, desks_page, "text/html; charset=utf-8", {"Cache-Control": "no-store"})
            elif path == "/api/desks" or DESK_API.fullmatch(path):
                history = hub.prices()["history"]
                body = session_desks.board(org=hub.org(), trades=hub.trades()["trades"],
                                           prices=history[-1]["prices"] if history else {}, env=env)
                if path != "/api/desks":
                    key = path.rsplit("/", 1)[-1]
                    body = {**body, "desk": next(d for d in body["desks"] if d["key"] == key)}
                self._json(200, body)
            elif path in KIND_PAGES:
                self._send(200, kind_page, "text/html; charset=utf-8", {"Cache-Control": "no-store"})
            elif path == "/pipeline":
                self._send(200, pipeline_page, "text/html; charset=utf-8", {"Cache-Control": "no-store"})
            elif path == "/markets":
                self._send(200, markets_page, "text/html; charset=utf-8", {"Cache-Control": "no-store"})
            elif MARKET_PAGE.fullmatch(path):
                if path.split("/")[-1].upper() not in markets.catalog.BY_SYMBOL:
                    return self._send(404, b"not found", "text/plain")
                self._send(200, market_page, "text/html; charset=utf-8", {"Cache-Control": "no-store"})
            elif path == "/api/markets":
                self._json(200, markets.board())
            elif MARKET_API.fullmatch(path):
                query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
                try:
                    body = markets.product(path.split("/")[-1], query.get("tf", ["1h"])[0])
                except (KeyError, ValueError) as e:
                    return self._json(404, {"error": str(e).strip("'\"")})
                self._json(200, body)
            elif path == "/events":
                self.stream()
            else:
                self._send(404, b"not found", "text/plain")

        def do_POST(self):
            path = urllib.parse.urlsplit(self.path).path
            # Consume the body first so the connection stays in sync, and never
            # read more than a login form needs.
            try:
                length = int(self.headers.get("Content-Length", 0))
            except ValueError:
                length = MAX_FORM_BYTES + 1
            limit = MAX_TRADES_BYTES if path == "/api/orderflow/trades" else MAX_FORM_BYTES
            if length > limit or length < 0:
                self.close_connection = True
                return self._send(413, b"too large", "text/plain", {"Connection": "close"})
            raw = self.rfile.read(length) if length else b""
            if not self._same_origin():
                return self._send(403, b'{"error":"cross-site request refused"}', "application/json")
            if path == "/api/bridge/result":
                return self._bridge_result(raw)
            if path == "/api/live/decide":
                return self._live_decide(raw)
            if path == "/logout":
                return self._redirect("/login", {"Set-Cookie": self._cookie("", 0)})
            if path == "/api/agents":
                return self._set_agents(raw)
            if path == "/api/orderflow/trades":
                return self._orderflow_trades(raw)
            if path == "/api/mt5/order":
                return self._mt5_order(raw)
            if path in ("/api/mitrade/open", "/api/mitrade/close"):
                return self._mitrade_post(path, raw)
            if path in ("/api/btcc/open", "/api/btcc/close"):
                return self._btcc_post(path, raw)
            if path in ("/api/betfair/bet", "/api/betfair/close", "/api/betfair/cancel"):
                return self._betfair_post(path, raw)
            if path in ("/api/polymarket/bet", "/api/polymarket/sell", "/api/polymarket/reset"):
                return self._poly_post(path, raw)
            if path == "/api/street/note":
                return self._street_note(raw)
            if path in ("/api/wallet/move", "/api/wallet/watch", "/api/wallet/unwatch"):
                return self._wallet_post(path, raw)
            if path in ("/api/jarvis/unlock", "/api/jarvis/ask", "/api/jarvis/lock", "/api/jarvis/setup"):
                return self._jarvis_post(path, raw)
            if MARKET_FAV.fullmatch(path):
                if not self._authed():
                    return self._json(401, {"error": "sign in required"})
                try:
                    return self._json(200, {"favourites": markets.toggle_favourite(path.split("/")[-2])})
                except KeyError:
                    return self._json(404, {"error": "unknown product"})
            if path != "/login" or not auth.enabled:
                return self._send(404, b"not found", "text/plain")
            client = self._client()
            if not auth.allowed(client):
                return self._redirect("/login?error=locked")
            form = urllib.parse.parse_qs(raw.decode("utf-8", "replace"))
            if auth.check(form.get("password", [""])[0], form.get("code", [""])[0]):
                auth.record_success(client)
                return self._redirect("/news", {"Set-Cookie": self._cookie(auth.issue(), auth.max_age)})
            auth.record_failure(client)
            self._redirect("/login?error=1")

        def _mt5_get(self, path: str):
            if not metaapi.configured(env):
                return self._json(200, {"connected": False, "needs": list(metaapi.ENV_KEYS),
                                        "help": "Add METAAPI_TOKEN on Render (Environment). The account is found from it."})
            try:
                api = metaapi.MetaApi(env)
                if path == "/api/mt5":
                    body = {"connected": True, **api.snapshot()}
                else:
                    query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
                    try:
                        days = int(query.get("days", ["30"])[0])
                    except ValueError:
                        days = 30
                    body = {"connected": True, **api.report(days)}
            except Exception as e:  # noqa: BLE001  (network or MetaApi error: report it, keep serving)
                return self._json(502, {"connected": False, "error": metaapi.explain(e)})
            self._json(200, body)

        def _mt5_order(self, raw: bytes):
            """Demo account only. The account type is checked with the broker on every order."""
            if not self._authed():
                return self._json(401, {"error": "sign in required"})
            if self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
                return self._json(415, {"error": "send JSON"})
            try:
                b = json.loads(raw or b"null")
                symbol, side, volume = str(b["symbol"])[:32], str(b["side"]), float(b["volume"])
                if b.get("live"):
                    raise platforms.LiveTradingLocked("Real-money orders are locked.")
                fill = metaapi.MetaApi(env).demo_order(symbol, side, volume, b.get("stop_loss"), b.get("take_profit"))
            except (platforms.LiveTradingLocked, metaapi.NotDemo) as e:
                return self._json(403, {"error": str(e)})
            except (ValueError, KeyError, TypeError) as e:
                return self._json(400, {"error": f"need symbol, side, volume: {e}"})
            except Exception as e:  # noqa: BLE001
                return self._json(502, {"error": f"MetaTrader cloud: {type(e).__name__}"})
            self._json(200, {"order": fill})

        def _mitrade_post(self, path: str, raw: bytes):
            """Mitrade practice desk only. Fills at Mitrade's bid/ask; nothing is sent to Mitrade."""
            if not self._authed():
                return self._json(401, {"error": "sign in required"})
            if self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
                return self._json(415, {"error": "send JSON"})
            try:
                b = json.loads(raw or b"null")
                desk = get_mitrade()
                if path == "/api/mitrade/open":
                    tp = b.get("take_profit")
                    pos = desk.open(str(b["symbol"])[:12], str(b["side"]), float(b["margin"]), float(b["leverage"]),
                                    float(b["stop_loss"]), float(tp) if tp not in (None, "") else None,
                                    live=bool(b.get("live")))
                    return self._json(200, {"position": pos})
                return self._json(200, {"closed": desk.close(int(b["id"]))})
            except mitrade.Refused as e:
                return self._json(403, {"error": str(e)})
            except KeyError as e:
                return self._json(400, {"error": f"missing or unknown: {str(e).strip(chr(39))}"})
            except (ValueError, TypeError, RuntimeError) as e:
                return self._json(400, {"error": str(e)})

        def _btcc_post(self, path: str, raw: bytes):
            """BTCC practice desk only. Nothing is sent to BTCC; real money is locked."""
            if not self._authed():
                return self._json(401, {"error": "sign in required"})
            if self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
                return self._json(415, {"error": "send JSON"})
            try:
                b = json.loads(raw or b"null")
                desk = get_btcc()
                if path == "/api/btcc/open":
                    tp = b.get("take_profit")
                    pos = desk.open(str(b["symbol"])[:12], str(b["side"]), float(b["margin"]), float(b["leverage"]),
                                    float(b["stop_loss"]), float(tp) if tp not in (None, "") else None,
                                    live=bool(b.get("live")))
                    return self._json(200, {"position": pos})
                return self._json(200, {"closed": desk.close(int(b["id"]))})
            except btcc.Refused as e:
                return self._json(403, {"error": str(e)})
            except KeyError as e:
                return self._json(400, {"error": f"missing or unknown: {str(e).strip(chr(39))}"})
            except (ValueError, TypeError, RuntimeError) as e:
                return self._json(400, {"error": str(e)})

        def _poly_post(self, path: str, raw: bytes):
            """Paper bets on real Polymarket prices. Nothing is sent to Polymarket; real money is locked."""
            if not self._authed():
                return self._json(401, {"error": "sign in required"})
            if self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
                return self._json(415, {"error": "send JSON"})
            try:
                b = json.loads(raw or b"null") or {}
                book = get_poly_book()
                if path == "/api/polymarket/bet":
                    pos = book.bet(str(b["market_id"])[:40], str(b["side"]), float(b["usd"]), live=bool(b.get("live")))
                    return self._json(200, {"bet": pos, "book": book.snapshot()})
                if path == "/api/polymarket/sell":
                    return self._json(200, {"sold": book.sell(int(b["id"])), "book": book.snapshot()})
                if not b.get("confirm"):
                    return self._json(400, {"error": "send confirm: true to reset the paper book"})
                return self._json(200, {"book": book.reset()})
            except polymarket_live.Refused as e:
                return self._json(403, {"error": str(e)})
            except KeyError as e:
                return self._json(400, {"error": f"missing: {str(e).strip(chr(39))}"})
            except (ValueError, TypeError, AttributeError) as e:
                return self._json(400, {"error": str(e)})

        def _betfair_post(self, path: str, raw: bytes):
            """Betfair bridge: paper by default; real money only with the owner's switch, confirm and the money rules."""
            if not self._authed():
                return self._json(401, {"error": "sign in required"})
            if self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
                return self._json(415, {"error": "send JSON"})
            try:
                b = json.loads(raw or b"null") or {}
                bf = get_betfair()
                if path == "/api/betfair/bet":
                    odds = b.get("odds")
                    bet = bf.place(str(b["market_id"])[:30], int(b["selection_id"]), str(b["side"]), float(b["stake"]),
                                   float(odds) if odds not in (None, "") else None, live=bool(b.get("live")),
                                   confirm=str(b.get("confirm", ""))[:20])
                    return self._json(200, {"bet": bet, "book": bf.snapshot()})
                if path == "/api/betfair/close":
                    return self._json(200, {"closed": bf.settle_paper(int(b["id"])), "book": bf.snapshot()})
                return self._json(200, {"cancelled": bf.cancel_real(str(b["market_id"])[:30], str(b["bet_id"])[:30])})
            except betfair.Refused as e:
                return self._json(403, {"error": str(e)})
            except KeyError as e:
                return self._json(400, {"error": f"missing: {str(e).strip(chr(39))}"})
            except (ValueError, TypeError) as e:
                return self._json(400, {"error": str(e)})
            except Exception as e:  # noqa: BLE001 (Betfair down or refused)
                return self._json(502, {"error": str(e)[:200]})

        def _report_authed(self) -> bool:
            sent = self.headers.get("Authorization", "")
            return report_on and hmac.compare_digest(sent.encode(), f"Bearer {report_token}".encode())

        def _orderflow_trades(self, raw: bytes):
            """A real trade feed (e.g. a script on the owner's computer reading an exchange):
            {"symbol": "BTC-USD", "trades": [{"ts", "price", "size", "side"}]}. Bridge token or signed in."""
            if not (self._authed() or self._bridge_authed()):
                return self._json(401, {"error": "sign in or bridge token required"})
            try:
                body = json.loads(raw or b"{}")
                symbol = str(body["symbol"])[:32]
                trades = body["trades"]
                assert isinstance(trades, list)
            except (ValueError, KeyError, TypeError, AssertionError):
                return self._json(400, {"error": "need {symbol, trades: [...]}"})
            self._json(200, {"used": hub.sources.on_trades(symbol, trades)})

        def _wallet_post(self, path: str, raw: bytes):
            """Practice money in or out, or add/remove a public crypto address. JSON body. Signed in only."""
            if not self._authed():
                return self._json(401, {"error": "sign in required"})
            try:
                body = json.loads(raw or b"{}")
                assert isinstance(body, dict)
                w = get_wallet()
                if path == "/api/wallet/move":
                    return self._json(200, {"entry": w.move(str(body.get("kind", "")), body.get("amount"),
                                                            str(body.get("note", "")))})
                if path == "/api/wallet/watch":
                    return self._json(200, {"watch": w.watch(str(body.get("address", "")), str(body.get("label", "")))})
                return self._json(200, {"removed": w.unwatch(str(body.get("address", "")))})
            except (ValueError, AssertionError) as e:  # WalletError is a ValueError
                msg = str(e) if isinstance(e, WalletError) else "send a JSON object"
                return self._json(400, {"error": msg})

        def _street_note(self, raw: bytes):
            """File one public analyst note: {"house": "dbs", "text": "...", "source": "url"}."""
            if not self._authed():
                return self._json(401, {"error": "sign in required"})
            try:
                body = json.loads(raw or b"{}")
                house, text = str(body["house"]), str(body["text"])[:3500]
            except (ValueError, KeyError, TypeError):
                return self._json(400, {"error": "need {house, text}"})
            prices = (hub.price_history[-1]["prices"] if hub.price_history else {})
            try:
                with hub.sources.lock:
                    calls = hub.sources.street.add_note(house, text, source=str(body.get("source", ""))[:300],
                                                        prices=prices)
            except ValueError as e:
                return self._json(400, {"error": str(e)})
            self._json(200, {"calls": [{"market": c.market, "direction": c.direction,
                                        "confidence": c.confidence} for c in calls]})

        def _bridge_authed(self) -> bool:
            sent = self.headers.get("Authorization", "")
            return bridge_on and hmac.compare_digest(sent.encode(), f"Bearer {bridge_token}".encode())

        def _bridge_result(self, raw: bytes):
            if not self._bridge_authed():
                return self._json(401, {"error": "bridge token required"})
            try:
                b = json.loads(raw or b"null")
                order = live_orders.record_result(hub.events_dir, str(b["id"]), bool(b["ok"]), str(b.get("detail", "")),
                                                  int(b.get("filled_qty") or 0), b.get("fill_price"),
                                                  b.get("positions") if isinstance(b.get("positions"), dict) else None)
            except (ValueError, KeyError, TypeError, AttributeError) as e:
                return self._json(400, {"error": str(e)})
            alerts.result(order)
            self._json(200, {"order": order})

        def _live_decide(self, raw: bytes):
            if not self._authed():
                return self._json(401, {"error": "sign in required"})
            if self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
                return self._json(415, {"error": "send JSON"})
            if not (auth.two_step and bridge_on):
                return self._json(403, {"error": "Real money needs two-step login (DASHBOARD_TOTP_SECRET) and "
                                                 "BRIDGE_TOKEN set first."})
            client = self._client()
            if not auth.allowed(client):
                return self._json(429, {"error": "Too many wrong codes. Try again in 15 minutes."})
            try:
                b = json.loads(raw or b"null")
                order_id, approve, code = str(b["id"]), bool(b["approve"]), str(b.get("code", ""))
            except (ValueError, KeyError, TypeError) as e:
                return self._json(400, {"error": f"bad request: {e}"})
            if approve and not auth.check_code(code):
                auth.record_failure(client)
                return self._json(403, {"error": "That code isn't right. Use a fresh code from your authenticator."})
            try:
                order = live_orders.decide(hub.events_dir, order_id, approve, live_orders.Limits(env),
                                           by=f"owner via dashboard from {client}")
            except ValueError as e:
                return self._json(409, {"error": str(e)})
            self._json(200, {"order": order})

        def _set_agents(self, raw: bytes):
            if not self._authed():
                return self._send(401, b'{"error":"sign in required"}', "application/json")
            # Requiring a JSON content type means a browser on another site must
            # ask permission first (a CORS preflight), which this server never grants.
            if self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
                return self._send(415, b'{"error":"send JSON"}', "application/json")
            try:
                per_department = json.loads(raw or b"null").get("per_department")
                control.write(hub.events_dir, per_department)
            except (ValueError, AttributeError) as e:
                return self._send(400, json.dumps({"error": str(e)}).encode(), "application/json")
            except OSError:
                return self._send(503, b'{"error":"could not save the setting"}', "application/json")
            body = {"per_department": per_department, "total": control.total(per_department)}
            self._send(200, json.dumps(body).encode(), "application/json")

        def _jarvis_post(self, path: str, raw: bytes):
            if not self._authed():
                return self._json(401, {"error": "sign in required"})
            if self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
                return self._json(415, {"error": "send JSON"})
            if path == "/api/jarvis/lock":
                return self._json(200, {"unlocked": False},
                                  {"Set-Cookie": f"{UNLOCK_COOKIE}=; Path=/; Max-Age=0; HttpOnly{self._secure()}; SameSite=Strict"})
            if not jarvis.enabled:
                return self._json(403, jarvis.status(False))
            try:
                body = json.loads(raw or b"null")
                if not isinstance(body, dict):
                    raise ValueError
            except ValueError:
                return self._json(400, {"error": "bad JSON"})
            client = self._client()
            if path == "/api/jarvis/setup":
                if not jarvis.needs_setup:
                    return self._json(409, {"error": "A passphrase is already set."})
                try:
                    jarvis.setup(str(body.get("passphrase", "")))
                except ValueError as e:
                    return self._json(400, {"error": str(e)})
                except OSError:
                    return self._json(503, {"error": "Could not save the passphrase."})
                cookie = (f"{UNLOCK_COOKIE}={jarvis.issue()}; Path=/; Max-Age={jarvis.ttl}; HttpOnly{self._secure()}; "
                          "SameSite=Strict")
                return self._json(200, {"unlocked": True}, {"Set-Cookie": cookie})
            if path == "/api/jarvis/unlock":
                if not jarvis.unlock_failures.allowed(client):
                    return self._json(429, {"error": "Too many wrong passphrases. Try again in 15 minutes."})
                if jarvis.check_passphrase(str(body.get("passphrase", ""))):
                    jarvis.unlock_failures.clear(client)
                    cookie = (f"{UNLOCK_COOKIE}={jarvis.issue()}; Path=/; Max-Age={jarvis.ttl}; HttpOnly{self._secure()}; "
                              "SameSite=Strict")
                    return self._json(200, {"unlocked": True}, {"Set-Cookie": cookie})
                jarvis.unlock_failures.hit(client)
                return self._json(403, {"unlocked": False, "error": "That isn't the passphrase."})
            if not self._jarvis_unlocked():
                return self._json(403, {"error": "Say or type your passphrase to wake JARVIS."})
            question = body.get("question")
            if not isinstance(question, str) or not question.strip():
                return self._json(400, {"error": "ask a question"})
            if not jarvis.questions.allowed(client):
                return self._json(429, {"error": "Slow down a little: too many questions this minute."})
            jarvis.questions.hit(client)
            reply = jarvis.ask(client, question, snapshot(hub.org(), hub.state()))
            self._json(200, {"reply": reply, "brain": jarvis.brain.name})

        def stream(self):
            self._headers(200, "text/event-stream", extra={"Cache-Control": "no-cache", "Connection": "close"})
            self.close_connection = True
            # Replay recent history so a fresh page isn't empty, then follow live.
            seq = max(0, hub.seq - 100)
            try:
                while True:
                    batch = hub.since(seq, timeout=15)
                    if not batch:
                        self.wfile.write(b": keep-alive\n\n")
                    for seq, event in batch:
                        self.wfile.write(f"data: {json.dumps(event)}\n\n".encode())
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                return

    return Handler


def serve(events_dir: Path, host: str, port: int, auth: WebAuth, jarvis: Jarvis | None = None) -> ThreadingHTTPServer:
    hub = EventHub(events_dir)
    threading.Thread(target=hub.run_tailer, daemon=True).start()
    jarvis = jarvis or Jarvis.from_env(public=auth.enabled, store=events_dir / "jarvis_owner.json")
    server = ThreadingHTTPServer((host, port), make_handler(hub, auth, jarvis))
    server.daemon_threads = True
    mode = "password login on" if auth.enabled else "no login (local only)"
    print(f"dashboard on http://{host}:{port} reading {events_dir} ({mode})", flush=True)
    return server


def main() -> None:
    events_dir = Path(os.environ.get("EVENTS_DIR", "events"))
    events_dir.mkdir(parents=True, exist_ok=True)
    host = os.environ.get("DASHBOARD_HOST", "127.0.0.1")
    port = int(os.environ.get("DASHBOARD_PORT") or os.environ.get("PORT") or 8080)
    serve(events_dir, host, port, WebAuth.from_env(host)).serve_forever()


if __name__ == "__main__":
    main()
