"""News Lab: replay the desk's real news path on a practice market and play with its settings.

Three teams, one lab (the "research -> interactive lab" pattern, trimmed to what suits our desk):

- ANALYSTS read every headline with the desk's real readers: the reading room's word reader
  (`reading_room.read_headline`) and the News Pipeline's cheap read, checks, gate and deep read
  (`news_pipeline.cheap_read / verify / deep_read`). Nothing is re-invented here.
- BUILDERS turn the reads into a desk: news fades by its half-life, the view mixes chart and news,
  a confident view opens a paper trade one tick later, trades pay a cost.
- CHECKERS redo the maths a second way and say plainly when a result can't be trusted: the view
  matches the real ReadingRoom, the bill and the P&L add up, no trade peeks at the future, and the
  same settings are tried on an unseen market.

The practice market knows the truth (each market's hidden lean), so reads can be graded. Headlines
are a mix of the practice wire's wording and everyday market wording. Lab assumption: a story that
a second site confirms is right more often than a one-site rumour.

Paper only. Nothing in this file can place an order, real or practice.
"""

from __future__ import annotations

import math
import random
from functools import lru_cache

from . import news_pipeline as np_
from .academy import ECONOMY, POLITICS
from .news import _TEXT
from .reading_room import CHART_BARS, CHART_SHARE, CONFIDENT, NEWS_KEEP, ReadingRoom, read_chart, read_headline

SYMBOLS = ("AAPL", "NVDA", "BTC", "EURUSD", "GOLD", "SPX")
TICKS = 2400
NOTIONAL = 10_000.0          # $ per paper trade
STORY_CHANCE = 0.03          # per market per tick
CONFIRM_CHANCE = 0.55        # share of stories a second site picks up
RUMOUR_PENALTY, CONFIRMED_BONUS = 0.15, 0.10
DEFAULT_HALF_LIFE = round(math.log(0.5) / math.log(NEWS_KEEP), 1)  # 0.97 a tick -> about 22.8 ticks

DEFAULTS = {"gate": np_.START_GATE, "half_life": DEFAULT_HALF_LIFE, "news_share": round(1 - CHART_SHARE, 2),
            "sources": 1, "confident": CONFIDENT, "cost_bps": 2.0, "honest": 0.7, "use_pipeline": False, "seed": 7}
LIMITS = {"gate": (0.5, 0.95), "half_life": (2.0, 300.0), "news_share": (0.0, 1.0), "sources": (1, 2),
          "confident": (0.05, 0.8), "cost_bps": (0.0, 50.0), "honest": (0.5, 0.95), "seed": (1, 9999)}

# Everyday headlines (live-feed style). Sign = which way the writer meant it.
LIVE_BULL = ("{s} beats estimates and raises guidance", "{s} gets an analyst upgrade", "{s} announces record buyback",
             "{s} wins regulatory approval", "{s} shares surge on stimulus hopes", "{s} rally extends to record high")
LIVE_BEAR = ("{s} misses estimates", "{s} downgraded by analysts", "{s} faces probe over accounts",
             "{s} announces layoffs", "{s} hit with lawsuit", "{s} plunges as tariff fears grow")
LIVE_FOGGY = ("{s} beats estimates but cuts guidance", "{s} CEO to speak at conference", "{s} trading volume picks up")


def clean(params: dict | None) -> dict:
    """Settings from the page, clamped to safe ranges. Unknown keys are ignored."""
    p = dict(DEFAULTS)
    for k, v in (params or {}).items():
        if k not in DEFAULTS or v in (None, ""):
            continue
        try:
            if k == "use_pipeline":
                p[k] = str(v).lower() in ("1", "true", "on", "yes")
                continue
            x = float(v)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(x):
            continue
        lo, hi = LIMITS[k]
        x = min(hi, max(lo, x))
        p[k] = int(round(x)) if k in ("sources", "seed") else round(x, 4)
    return p


# ---------------------------------------------------------------- the practice market (depends on seed only)

@lru_cache(maxsize=6)
def _market(seed: int):
    rng = random.Random(f"lab-market:{seed}")
    prices, drift, chart = {}, {}, {}
    for s in SYMBOLS:
        p, d, ps, ds = 100.0, rng.choice((-1, 1)) * 0.0002, [], []
        for _ in range(TICKS + 1):
            if rng.random() < 1 / 250:
                d = rng.choice((-1, 1)) * rng.uniform(0.00005, 0.0003)
            p *= math.exp(d + rng.gauss(0, 0.004))
            ps.append(p)
            ds.append(d)
        prices[s], drift[s] = ps, ds
        chart[s] = [read_chart(ps[max(0, t - CHART_BARS + 1): t + 1]) for t in range(TICKS + 1)]
    return prices, drift, chart


@lru_cache(maxsize=12)
def _stories(seed: int, honest: float):
    """The headlines: when, which market, the words, how many sites, and whether it told the truth."""
    _, drift, _ = _market(seed)
    rng = random.Random(f"lab-news:{seed}")
    stories, arrivals = [], {}
    for t in range(1, TICKS - 1):
        for s in SYMBOLS:
            if rng.random() >= STORY_CHANCE:
                continue
            confirmed = rng.random() < CONFIRM_CHANCE
            truth = 1 if drift[s][t] >= 0 else -1
            p_right = honest + (CONFIRMED_BONUS if confirmed else -RUMOUR_PENALTY)
            bullish = (truth > 0) if rng.random() < p_right else (truth < 0)
            roll = rng.random()
            if roll < 0.12:
                text, meant = rng.choice(LIVE_FOGGY).format(s=s), 0
            elif roll < 0.5:
                text, meant = rng.choice(LIVE_BULL if bullish else LIVE_BEAR).format(s=s), 1 if bullish else -1
            elif roll < 0.75:
                event = rng.choice(sorted(k for k, v in POLITICS.items() if (v > 0) == bullish))
                text, meant = f"{s}: {_TEXT[event]}", 1 if bullish else -1
            else:
                event = rng.choice(sorted(ECONOMY))
                above = (ECONOMY[event] > 0) == bullish
                text, meant = f"{s}: {_TEXT[event]} {'above' if above else 'below'} forecast", 1 if bullish else -1
            second = min(TICKS - 1, t + rng.randint(1, 10)) if confirmed else None
            i = len(stories)
            stories.append({"id": i, "tick": t, "symbol": s, "text": text, "meant": meant, "truth": truth,
                            "confirmed": confirmed, "second": second, "push": read_headline(text)})
            arrivals.setdefault(t, []).append((i, 0))
            if second is not None:
                arrivals.setdefault(second, []).append((i, 1))
    return stories, arrivals


# ---------------------------------------------------------------- one run of the lab

def _route(h: dict, gate: float, view: dict) -> dict:
    """The News Pipeline's steps for one headline at a fixed gate (the live app also nudges its gate by audits)."""
    t = np_.cheap_read(h)
    fails = np_.verify(h, t)
    conf = t["confidence"] if not fails else min(t["confidence"], 0.39)
    text = h["text"].lower()
    overrule = next((w.strip() for w in np_.OVERRULE if w in text), None)
    deep_route = bool(overrule) or conf < gate
    d = np_.deep_read(h, view) if deep_route else None
    final = d["lean"] if d else t["lean"]
    human = bool(d) and (d["confidence"] < 0.6 or (conf < np_.HUMAN_BELOW and d["confidence"] < np_.START_GATE))
    return {"cheap_lean": t["lean"], "conf": round(conf, 3), "evidence": t["evidence"], "why": t["reason"] or ", ".join(fails),
            "route": "deep" if deep_route else "kept", "overrule": overrule, "deep_lean": d["lean"] if d else None,
            "deep_conf": d["confidence"] if d else None, "final": final, "human": human,
            "call": "BUY" if final > 0.15 else "SELL" if final < -0.15 else "WAIT",
            "cost": np_.CHEAP_COST + (np_.DEEP_COST if deep_route else 0.0)}


def _sign(x: float) -> int:
    return (x > 0) - (x < 0)


def run(params: dict | None = None, detail: bool = True) -> dict:
    p = clean(params)
    prices, drift, chart = _market(p["seed"])
    stories, arrivals = _stories(p["seed"], p["honest"])
    keep = 0.5 ** (1 / p["half_life"])
    share, conf_bar, fee = p["news_share"], p["confident"], NOTIONAL * p["cost_bps"] / 1e4
    news = {s: 0.0 for s in SYMBOLS}
    pos = {s: None for s in SYMBOLS}       # open trade
    pending = {s: None for s in SYMBOLS}   # side decided at tick t, filled at t+1
    routes, trades, trace = {}, [], []
    curve, realised = [], 0.0
    sample = max(1, TICKS // 160)

    def close(s, t):
        nonlocal realised
        tr = pos[s]
        tr["exit"], tr["close_tick"] = prices[s][t], t
        tr["gross"] = round(NOTIONAL * tr["side"] * (tr["exit"] / tr["entry"] - 1), 4)
        tr["fee"] = round(fee, 4)
        tr["pnl"] = round(tr["gross"] - tr["fee"], 4)
        realised += tr["pnl"]
        trades.append(tr)
        pos[s] = None

    for t in range(TICKS):
        # 1. fill yesterday's decisions at today's price (no peeking: decided at t-1, filled at t)
        for s in SYMBOLS:
            want = pending[s]
            if want is None:
                continue
            pending[s] = None
            if pos[s] and pos[s]["side"] != want:
                close(s, t)
            if want and not pos[s]:
                pos[s] = {"symbol": s, "side": want, "decide_tick": t - 1, "open_tick": t, "entry": prices[s][t]}
        # 2. old news fades, new headlines arrive
        for s in SYMBOLS:
            news[s] *= keep
        for i, k in arrivals.get(t, ()):
            st = stories[i]
            s = st["symbol"]
            if k == 0:  # first site: the pipeline reads it (a second copy is a cache hit, never billed)
                view_now = {"chart": chart[s][t], "news": news[s]}
                routes[i] = _route({"symbol": s, "text": st["text"], "kind": "lab"}, p["gate"], view_now)
            r = routes[i]
            w = (0.5 if p["sources"] == 1 else (0.0 if k == 0 else 1.0))
            used = st["push"] is not None and w > 0
            if used and p["use_pipeline"]:
                used = not r["human"] and r["call"] != "WAIT" and _sign(r["final"]) == _sign(st["push"])
            before = news[s]
            if used:
                news[s] = max(-1.0, min(1.0, news[s] + st["push"] * w))
            if detail and k == 0:
                trace.append({"id": i, "tick": t, "symbol": s, "text": st["text"], "confirmed": st["confirmed"],
                              "truth": st["truth"], "push": st["push"], "weight": w, "used": used,
                              "news_before": round(before, 3), "news_after": round(news[s], 3), **r})
            elif detail and k == 1 and trace:
                for row in reversed(trace):
                    if row["id"] == i:
                        row["second_used"] = used
                        row["second_tick"] = t
                        break
        # 3. one view per market, and the desk's decision for the next tick
        for s in SYMBOLS:
            v = max(-1.0, min(1.0, (1 - share) * chart[s][t] + share * news[s]))
            cur = pos[s]["side"] if pos[s] else 0
            if cur and v * cur >= conf_bar * 0.5:
                want = cur                                   # view still backs the trade
            elif v >= conf_bar:
                want = 1
            elif v <= -conf_bar:
                want = -1
            else:
                want = 0
            if want != cur and t < TICKS - 1:
                pending[s] = want
        if t % sample == 0:
            curve.append(round(realised, 2))
    for s in SYMBOLS:
        if pos[s]:
            close(s, TICKS)
    curve.append(round(realised, 2))
    return _summarise(p, stories, routes, trades, trace, curve, detail)


def _summarise(p, stories, routes, trades, trace, curve, detail):
    wins = sum(1 for tr in trades if tr["pnl"] > 0)
    pnl = sum(tr["pnl"] for tr in trades)
    peak = dd = run_ = 0.0
    for tr in sorted(trades, key=lambda x: x["close_tick"]):
        run_ += tr["pnl"]
        peak = max(peak, run_)
        dd = max(dd, peak - run_)
    rs = list(routes.values())
    n = len(rs) or 1
    bill = {"hybrid": round(sum(r["cost"] for r in rs), 4), "cheap_only": round(len(rs) * np_.CHEAP_COST, 4),
            "deep_only": round(len(rs) * np_.DEEP_COST, 4)}
    read = [st for st in stories if st["id"] in routes]
    room_read = [st for st in read if st["push"] is not None]
    room_right = sum(1 for st in room_read if _sign(st["push"]) == st["truth"])
    calls = [(routes[st["id"]], st) for st in read if routes[st["id"]]["call"] != "WAIT"]
    pipe_right = sum(1 for r, st in calls if (1 if r["call"] == "BUY" else -1) == st["truth"])
    cheap_calls = [(routes[st["id"]]["cheap_lean"], st) for st in read if abs(routes[st["id"]]["cheap_lean"]) > 0.15]
    cheap_right = sum(1 for lean, st in cheap_calls if _sign(lean) == st["truth"])
    out = {
        "params": p, "mode": "paper", "real_money": "locked",
        "summary": {"trades": len(trades), "wins": wins, "win_rate": round(wins / len(trades), 4) if trades else None,
                    "pnl": round(pnl, 2), "gross": round(sum(tr["gross"] for tr in trades), 2),
                    "fees": round(sum(tr["fee"] for tr in trades), 2),
                    "avg": round(pnl / len(trades), 2) if trades else None, "max_drawdown": round(dd, 2)},
        "pipeline": {"read": len(rs), "kept": sum(r["route"] == "kept" for r in rs),
                     "deep": sum(r["route"] == "deep" for r in rs), "overruled": sum(bool(r["overrule"]) for r in rs),
                     "human": sum(r["human"] for r in rs), "bill": bill,
                     "savings": round(1 - bill["hybrid"] / bill["deep_only"], 4) if bill["deep_only"] else 0.0},
        "analysts": {"headlines": len(read), "room_read": len(room_read), "room_unread": len(read) - len(room_read),
                     "room_right": round(room_right / len(room_read), 4) if room_read else None,
                     "pipeline_calls": len(calls), "pipeline_right": round(pipe_right / len(calls), 4) if calls else None,
                     "cheap_calls": len(cheap_calls), "cheap_right": round(cheap_right / len(cheap_calls), 4) if cheap_calls else None,
                     "confirmed": sum(st["confirmed"] for st in read), "share_of_reads": round(len(room_read) / n, 4)},
        "curve": curve,
    }
    if detail:
        out["trades"] = trades
        out["trace"] = trace
    return out


# ---------------------------------------------------------------- the checkers

def _room_matches(seed: int, honest: float, ticks: int = 400) -> float:
    """Run the real ReadingRoom on the same prices and headlines; return the biggest gap in the news score."""
    prices, _, _ = _market(seed)
    stories, arrivals = _stories(seed, honest)
    room = ReadingRoom(list(SYMBOLS))
    mine = {s: 0.0 for s in SYMBOLS}
    gap = 0.0
    for t in range(ticks):
        heads = []
        for s in SYMBOLS:
            mine[s] *= NEWS_KEEP
        for i, k in arrivals.get(t, ()):
            st = stories[i]
            heads.append({"symbol": st["symbol"], "text": st["text"], "weight": 0.5})
            if st["push"] is not None:
                mine[st["symbol"]] = max(-1.0, min(1.0, mine[st["symbol"]] + st["push"] * 0.5))
        views = room.update({s: prices[s][t] for s in SYMBOLS}, heads)
        for s in SYMBOLS:
            gap = max(gap, abs(views[s].news - mine[s]))
    return gap


def checks(res: dict, unseen: dict, chart_only: dict) -> list[dict]:
    s, u = res["summary"], unseen["summary"]
    out = []

    def add(name, ok, detail, level=None):
        out.append({"check": name, "ok": bool(ok), "level": level or ("ok" if ok else "fail"), "detail": detail})

    gap = _room_matches(res["params"]["seed"], res["params"]["honest"])
    add("News maths matches the real reading room", gap < 1e-9,
        f"replayed 400 ticks through trading/reading_room.py: biggest gap {gap:.1e}")
    trades = res.get("trades") or []
    total = round(sum(t["pnl"] for t in trades), 2)
    add("P&L adds up", abs(total - s["pnl"]) < 0.01 and abs(res["curve"][-1] - s["pnl"]) < 0.02,
        f"{len(trades)} trades sum to ${total:,.2f}; the curve ends at ${res['curve'][-1]:,.2f}")
    add("Costs are paid on every trade", abs(s["gross"] - s["fees"] - s["pnl"]) < 0.02 and all(t["fee"] >= 0 for t in trades),
        f"gross ${s['gross']:,.2f} minus costs ${s['fees']:,.2f} = ${s['pnl']:,.2f}")
    peek = [t for t in trades if t["open_tick"] <= t["decide_tick"]]
    add("No trade peeks at the future", not peek,
        "every trade is decided on one tick and filled on the next" if not peek else f"{len(peek)} trades filled on the tick they were decided")
    rows = res.get("trace") or []
    bill = round(sum(r["cost"] for r in rows), 4)
    add("The bill adds up", abs(bill - res["pipeline"]["bill"]["hybrid"]) < 1e-6,
        f"{len(rows)} headlines cost ${bill:.4f}, cheap ${np_.CHEAP_COST} each plus ${np_.DEEP_COST} for each deep read")
    enough = s["trades"] >= 30
    add("Enough trades to judge", enough, f"{s['trades']} trades" + ("" if enough else ": too few, results are mostly luck"),
        None if enough else "warn")
    if s["pnl"] > 0 and u["pnl"] <= 0:
        add("Works on an unseen market", False, f"made ${s['pnl']:,.0f} here but ${u['pnl']:,.0f} on a market it never saw: tuned to this one", "warn")
    else:
        add("Works on an unseen market", u["pnl"] > 0 or s["pnl"] <= 0,
            f"same settings on a new market: ${u['pnl']:,.0f}, win rate {_pct(u['win_rate'])}", None if u["pnl"] > 0 else "warn")
    better = s["pnl"] > chart_only["summary"]["pnl"]
    add("News adds something over chart only", better,
        f"with news ${s['pnl']:,.0f} vs chart only ${chart_only['summary']['pnl']:,.0f}", None if better else "warn")
    add("Paper only", True, "the lab has no order code; real money stays locked")
    return out


def _pct(x):
    return "–" if x is None else f"{x * 100:.1f}%"


# ---------------------------------------------------------------- what the page asks for

SWEEPS = {
    "news_share": [0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 1.0],
    "half_life": [3, 6, 12, 23, 45, 90, 180],
    "gate": [0.5, 0.6, 0.7, 0.8, 0.85, 0.9, 0.95],
}


@lru_cache(maxsize=256)
def _cached(key: tuple) -> dict:
    return run(dict(key), detail=False)


def _quick(p: dict) -> dict:
    return _cached(tuple(sorted(p.items())))


def lab(params: dict | None = None) -> dict:
    """Everything the News Lab page shows for one set of controls."""
    p = clean(params)
    res = run(p)
    unseen = _quick({**p, "seed": p["seed"] + 1000})
    chart_only = _quick({**p, "news_share": 0.0})
    today = _quick({**DEFAULTS, "seed": p["seed"], "honest": p["honest"]})
    sweeps = {k: [{"x": x, "pnl": (r := _quick({**p, k: x}))["summary"]["pnl"], "win_rate": r["summary"]["win_rate"],
                   "bill": r["pipeline"]["bill"]["hybrid"]} for x in xs] for k, xs in SWEEPS.items()}
    trades = res.pop("trades")
    trace = res.pop("trace")
    res["checks"] = checks({**res, "trades": trades, "trace": trace}, unseen, chart_only)
    res["compare"] = {"yours": res["summary"], "today": today["summary"], "chart_only": chart_only["summary"],
                      "unseen": unseen["summary"], "chart_only_curve": chart_only["curve"], "today_curve": today["curve"]}
    res["sweeps"] = sweeps
    res["trace"] = trace[-60:][::-1]
    res["recent_trades"] = trades[-12:][::-1]
    res["defaults"], res["limits"] = DEFAULTS, LIMITS
    res["formulas"] = [
        {"name": "news fade", "text": f"news x {0.5 ** (1 / p['half_life']):.4f} each tick (half gone in {p['half_life']:g} ticks)"},
        {"name": "headline push", "text": "news + read x weight (" + ("0.5 first site, 0.5 second site" if p["sources"] == 1 else "0 until a second site confirms, then 1.0") + ")"},
        {"name": "view", "text": f"{1 - p['news_share']:.0%} chart + {p['news_share']:.0%} news"},
        {"name": "open", "text": f"|view| at least {p['confident']:g}, filled next tick, ${NOTIONAL:,.0f} a trade"},
        {"name": "hold", "text": f"while the view still backs it by {p['confident'] * 0.5:g}"},
        {"name": "cost", "text": f"{p['cost_bps']:g} bps a round trip = ${NOTIONAL * p['cost_bps'] / 1e4:,.2f} a trade"},
        {"name": "pipeline", "text": f"cheap read; under {p['gate']:g} sure or big news -> deep read (50% headline, 30% news, 20% chart)"
                                     + ("; used as a filter on what reaches the view" if p["use_pipeline"] else "; advice only, like the live app")},
    ]
    return res


def reroute(log: list[dict], gate: float) -> list[dict]:
    """The live app's latest headlines, sent through the gate the user picked (routing only, no P&L)."""
    gate = clean({"gate": gate})["gate"]
    out = []
    for r in log[:15]:
        tri = r.get("triage") or {}
        conf = float(tri.get("confidence") or 0)
        mine = "deep" if r.get("overrule") or conf < gate else "kept"
        out.append({"symbol": r.get("symbol"), "text": r.get("text"), "confidence": conf, "app_route": r.get("route"),
                    "app_gate": r.get("gate"), "your_route": mine, "changed": mine != ("deep" if r.get("route") == "deep" else "kept"),
                    "call": r.get("call")})
    return out
