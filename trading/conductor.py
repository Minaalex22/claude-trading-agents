"""Six agents and one Conductor, on paper, for the /conductor page.

Built from the "AI Trading Agents, Full Breakdown" PDF (the 6 agents, 6 charts, 1 conductor reel).
That reel is a promo simulation; this is the real architecture behind it, run as a practice desk:

  Perception  each asset gets a new 1-minute bar every step (simulated prices, labelled so)
  Decision    Entry, Scalper, Hedge, Exit and News agents send structured messages (PROPOSE, UPDATE_SL,
              HEDGE, CLOSE, PAUSE). The Conductor collects them, asks the Risk agent, resolves conflicts
              (priority Risk > Hedge > Exit > Entry, then higher confidence) and moves each trade through
              one state machine. No agent can place an order itself.
  Risk        deterministic rules, no AI: 0.5% of equity at risk per trade, size = risk$ / |entry - SL|,
              max 3 open trades, no new trade on an asset more than 0.7 correlated with an open one,
              daily loss of 2% pauses entries and the kill switch closes everything.
  Action      paper fills only, with fees and slippage charged, which the reel never shows.
  Memory      every message is kept in an audit log, so each decision can be replayed.

Real money is never touched here.
"""

from __future__ import annotations

import math
import random
import threading
import time
from collections import deque

from .markets.indicators import ema, rsi

STEP_SECONDS = 1.5          # one simulated minute per step at most every 1.5 seconds
START_EQUITY = 100_000.0
RISK_PCT = 0.005            # 0.5% of equity at risk per trade
DAILY_LOSS_PCT = 0.02       # 2% daily loss pauses entries
MAX_OPEN = 3
MAX_CORR = 0.7
MAX_NOTIONAL_PCT = 0.25     # one trade never controls more than 25% of equity
FEE_PCT = 0.0002            # 0.02% per side
SLIP_PCT = 0.0001           # 0.01% worse fill each side
SCORE_GATE = 7              # propose only when the score is 7 out of 10 or more
DAY_BARS = 480              # a practice "day" is 480 bars, then the daily loss resets
BARS_KEPT = 160
NEWS_EVENTS = ("US CPI", "US jobs (NFP)", "ECB rate decision", "Fed rate decision", "UK CPI", "China PMI")

ASSETS = [
    # symbol, name, start price, volatility per bar, strategy, agent, session
    ("DAX", "DAX 40", 19300.0, 0.0011, "trend", "entry"),
    ("GOLD", "Gold", 2650.0, 0.0010, "trend", "entry"),
    ("DOGE", "Dogecoin", 0.30, 0.0035, "breakout", "entry"),
    ("XRP", "XRP", 1.10, 0.0030, "breakout", "entry"),
    ("EURUSD", "Euro / US Dollar", 1.08, 0.0004, "scalp", "scalper"),
    ("BTC", "Bitcoin", 95000.0, 0.0020, "revert", "scalper"),
]
FACTORS = {"DOGE": "risk", "XRP": "risk", "BTC": "risk", "DAX": "macro", "EURUSD": "macro", "GOLD": "gold"}
STRATEGY_NAMES = {"trend": "Trend following", "breakout": "Breakout + retest", "scalp": "Scalping",
                  "revert": "Mean reversion", "hedge": "Hedge overlay"}
AGENTS = {
    "conductor": "Conductor: collects, asks Risk, resolves conflicts, logs every step",
    "entry": "Entry: when to get in (trend and breakout setups). Proposes only",
    "scalper": "Scalper: fast fades at stretched RSI and VWAP, tiny targets",
    "hedge": "Hedge: insurance when open drawdown or correlation gets too high",
    "risk": "Risk: the safety boss. Sizes every trade and can veto anyone. Rules, not AI",
    "exit": "Exit: breakeven after +1R, trails the stop, time exits, trend-flip exits",
    "news": "News: pauses new entries 15 minutes before high-impact news",
}
STATES = ["PROPOSED", "RISK_CHECK", "APPROVED", "REJECTED", "EXECUTING", "OPEN", "MANAGING",
          "HEDGED", "CLOSING", "CLOSED", "LOGGED", "REVIEWED"]


def _corr(a: list[float], b: list[float]) -> float:
    n = min(len(a), len(b))
    if n < 10:
        return 0.0
    a, b = a[-n:], b[-n:]
    ma, mb = sum(a) / n, sum(b) / n
    sa = math.sqrt(sum((x - ma) ** 2 for x in a))
    sb = math.sqrt(sum((y - mb) ** 2 for y in b))
    if sa == 0 or sb == 0:
        return 0.0
    return sum((x - ma) * (y - mb) for x, y in zip(a, b)) / (sa * sb)


class Asset:
    def __init__(self, rng: random.Random, symbol, name, price, vol, strategy, agent, factor):
        self.symbol, self.name, self.vol, self.strategy, self.agent = symbol, name, vol, strategy, agent
        self.rng, self.factor = rng, factor  # factor links assets so correlation is real, not faked
        self.drift = 0.0
        self.bars: deque[dict] = deque(maxlen=400)
        self.rets: deque[float] = deque(maxlen=60)
        self.price = price
        self.t = 0

    def next_bar(self, t: int, common: dict[str, float]) -> dict:
        # Regimes: the drift wanders, so some stretches trend and some chop.
        if self.rng.random() < 0.02:
            self.drift = self.rng.gauss(0, 0.35) * self.vol
        shock = 0.6 * common[self.factor] + 0.8 * self.rng.gauss(0, 1)
        o = self.price
        c = o * (1 + self.drift + shock * self.vol)
        h = max(o, c) * (1 + abs(self.rng.gauss(0, self.vol * 0.5)))
        lo = min(o, c) * (1 - abs(self.rng.gauss(0, self.vol * 0.5)))
        v = abs(self.rng.gauss(1000, 250)) * (1 + 3 * abs(c - o) / o / self.vol / 4)
        if self.rng.random() < 0.04:
            v *= 2.5  # volume spikes, the breakout agent's favourite thing
        bar = {"t": t, "o": o, "h": h, "l": lo, "c": c, "v": v}
        self.bars.append(bar)
        self.rets.append(c / o - 1)
        self.price = c
        return bar


class Conductor:
    """The practice trading floor. Call step() to advance; snapshot() for the page."""

    def __init__(self, seed: int = 11, warmup: int = 120):
        self.rng = random.Random(seed)
        self.lock = threading.Lock()
        self.assets = {a[0]: Asset(random.Random(seed * 31 + i), *a, factor=FACTORS[a[0]])
                       for i, a in enumerate(ASSETS)}
        self.t = 1_700_000_000 // 60 * 60
        self.bar = 0
        self.balance = START_EQUITY
        self.day_start_equity = START_EQUITY
        self.peak = START_EQUITY
        self.max_dd = 0.0
        self.paused_until = -1
        self.pause_reason = ""
        self.killed_day = -1
        self.next_news = (self.rng.randint(60, 140), self.rng.choice(NEWS_EVENTS))
        self.trades: dict[int, dict] = {}
        self.closed: deque[dict] = deque(maxlen=400)
        self.log: deque[dict] = deque(maxlen=300)
        self.toasts: deque[dict] = deque(maxlen=12)
        self.state_counts = {s: 0 for s in STATES}
        self.rejects: dict[str, int] = {}
        self.agent_counts = {a: 0 for a in AGENTS}
        self.equity_curve: deque[dict] = deque(maxlen=400)
        self.day_pnls: list[float] = []
        self.ids = 0
        self.benched: dict[str, int] = {}  # strategy -> bar it comes back (daily review benches losers)
        self.cooldown: dict[str, int] = {}  # asset -> bar until which a rejected idea is not re-sent
        self.last_step = 0.0
        self.approved_pulse = 0
        self.warming = True
        for _ in range(warmup):
            self._advance()
        self.warming = False

    # ---------- messages ----------

    def _msg(self, agent: str, kind: str, text: str, **data) -> None:
        self.agent_counts[agent] = self.agent_counts.get(agent, 0) + 1
        self.log.append({"t": self.t, "agent": agent, "type": kind, "text": text, "data": data})

    def _state(self, tr: dict, state: str) -> None:
        tr["states"].append(state)
        self.state_counts[state] += 1

    # ---------- analysis ----------

    @staticmethod
    def _vwap(bars: list[dict], n: int = 60) -> float:
        b = bars[-n:]
        vol = sum(x["v"] for x in b) or 1
        return sum((x["h"] + x["l"] + x["c"]) / 3 * x["v"] for x in b) / vol

    @staticmethod
    def _atr(bars: list[dict], n: int = 14) -> float:
        trs = [max(b["h"] - b["l"], abs(b["h"] - a["c"]), abs(b["l"] - a["c"])) for a, b in zip(bars[-n - 1:-1], bars[-n:])]
        return sum(trs) / len(trs) if trs else 0.0

    def read(self, a: Asset) -> dict:
        """Score = Trend(0-3) + Momentum(0-2) + Volume(0-2) + Structure(0-2) + Risk fit(0-1)."""
        bars = list(a.bars)
        closes = [b["c"] for b in bars]
        e9, e21, e50 = ema(closes, 9)[-1], ema(closes, 21)[-1], ema(closes, 50)[-1]
        r = rsi(closes, 14)[-1] or 50
        atr = self._atr(bars)
        vwap = self._vwap(bars)
        price = closes[-1]
        vol_avg = sum(b["v"] for b in bars[-21:-1]) / 20
        hi20 = max(b["h"] for b in bars[-21:-1])
        lo20 = min(b["l"] for b in bars[-21:-1])
        side = 1 if (price > e50 and e9 > e21) else -1 if (price < e50 and e9 < e21) else 0
        trend = 0
        if side:
            trend = 1 + (abs(e9 - e21) > 0.15 * atr) + ((e21 - e50) * side > 0)
        momentum = 0
        if side:
            momentum = (1 if (r - 50) * side > 5 else 0) + (1 if (closes[-1] - closes[-6]) * side > 0.5 * atr else 0)
        volume = (1 if bars[-1]["v"] > vol_avg else 0) + (1 if bars[-1]["v"] > 1.5 * vol_avg else 0)
        broke = price > hi20 if side > 0 else price < lo20 if side < 0 else False
        structure = (1 if broke else 0) + (1 if side and (price - vwap) * side > 0 else 0)
        risk_fit = 1 if 0 < atr < price * a.vol * 3 else 0
        score = trend + momentum + volume + structure + risk_fit
        return {"side": side, "score": score, "parts": {"trend": trend, "momentum": momentum, "volume": volume,
                "structure": structure, "risk_fit": risk_fit}, "rsi": r, "atr": atr, "vwap": vwap, "price": price,
                "ema9": e9, "ema21": e21, "ema50": e50, "broke": broke, "hi20": hi20, "lo20": lo20}

    # ---------- agents ----------

    def agent_entry(self, a: Asset, rd: dict) -> dict | None:
        if not rd["side"] or rd["score"] < SCORE_GATE:
            return None
        if a.strategy == "breakout" and not rd["broke"]:
            return None
        side, price, atr = rd["side"], rd["price"], rd["atr"]
        sl = price - side * 1.5 * atr
        tp = price + side * (2.5 if a.strategy == "trend" else 2.0) * 1.5 * atr
        return {"agent": "entry", "asset": a.symbol, "direction": "LONG" if side > 0 else "SHORT", "side": side,
                "entry": price, "sl": sl, "tp": tp, "confidence": round(min(0.95, rd["score"] / 10), 2),
                "strategy": a.strategy, "max_bars": 90,
                "reason": f"score {rd['score']}/10 " + ("breakout on volume" if a.strategy == "breakout" else "trend + structure")}

    def agent_scalper(self, a: Asset, rd: dict) -> dict | None:
        price, atr, vwap, r = rd["price"], rd["atr"], rd["vwap"], rd["rsi"]
        if atr <= 0:
            return None
        stretch = (price - vwap) / atr
        if a.strategy == "scalp":
            fire = (r < 30 and stretch < -0.5) or (r > 70 and stretch > 0.5)
        else:  # mean reversion: 2 ATR from the mean and RSI at an extreme
            fire = (r < 32 and stretch < -2) or (r > 68 and stretch > 2)
        if not fire:
            return None
        side = 1 if stretch < 0 else -1
        # Small targets: about one ATR for the scalp, back to VWAP for mean reversion.
        tp = price + side * 1.0 * atr if a.strategy == "scalp" else vwap
        sl = price - side * (1.0 if a.strategy == "scalp" else 1.5) * atr
        if (tp - price) * side <= 0:
            return None
        conf = round(min(0.9, 0.55 + abs(r - 50) / 100), 2)
        return {"agent": "scalper", "asset": a.symbol, "direction": "LONG" if side > 0 else "SHORT", "side": side,
                "entry": price, "sl": sl, "tp": tp, "confidence": conf, "strategy": a.strategy, "max_bars": 10 if a.strategy == "scalp" else 30,
                "reason": f"RSI {r:.0f}, {abs(stretch):.1f} ATR from VWAP, fade back"}

    def agent_news(self) -> None:
        at, name = self.next_news
        if self.bar == at - 15:
            self.paused_until = at + 5
            self.pause_reason = name
            self._msg("news", "PAUSE", f"{name} in 15 min: pause new entries", duration="15m", reason=name)
        if self.bar == at:
            self._msg("news", "EVENT", f"{name} out now, volatility up")
            for a in self.assets.values():
                a.drift = self.rng.gauss(0, 0.6) * a.vol
        if self.bar >= at + 5:
            self.next_news = (self.bar + self.rng.randint(90, 200), self.rng.choice(NEWS_EVENTS))
            self._msg("news", "RESUME", f"News window over. Next: {self.next_news[1]} in {self.next_news[0] - self.bar} min")

    def risk_check(self, p: dict) -> tuple[str, dict]:
        """Deterministic. Returns (APPROVED | RESIZED | REJECTED, details)."""
        equity = self.equity()
        if self.killed_day == self.bar // DAY_BARS:
            return "REJECTED", {"reason": "kill switch hit today"}
        if equity - self.day_start_equity <= -DAILY_LOSS_PCT * self.day_start_equity:
            return "REJECTED", {"reason": "daily loss limit"}
        if self.paused_until > self.bar:
            return "REJECTED", {"reason": f"news pause ({self.pause_reason})"}
        opens = [t for t in self.trades.values() if t["kind"] == "main"]
        if any(t["asset"] == p["asset"] for t in opens):
            return "REJECTED", {"reason": "already in this asset"}
        if len(opens) >= MAX_OPEN:
            return "REJECTED", {"reason": f"max {MAX_OPEN} open trades"}
        for t in opens:
            c = _corr(list(self.assets[p["asset"]].rets), list(self.assets[t["asset"]].rets))
            if c * p["side"] * t["side"] > MAX_CORR:
                return "REJECTED", {"reason": f"{c:.2f} correlated with open {t['asset']}"}
        risk_usd = equity * RISK_PCT
        dist = abs(p["entry"] - p["sl"])
        if dist <= 0:
            return "REJECTED", {"reason": "no stop distance"}
        qty = risk_usd / dist
        cap = equity * MAX_NOTIONAL_PCT / p["entry"]
        verdict = "APPROVED"
        if qty > cap:
            qty, verdict = cap, "RESIZED"
        return verdict, {"qty": qty, "risk_usd": round(qty * dist, 2), "size_pct": round(qty * dist / equity * 100, 2)}

    def agent_exit(self, tr: dict, a: Asset, rd: dict) -> list[dict]:
        out = []
        bar = a.bars[-1]
        side, r_dist = tr["side"], tr["r_dist"]
        hit_sl = bar["l"] <= tr["sl"] if side > 0 else bar["h"] >= tr["sl"]
        hit_tp = bar["h"] >= tr["tp"] if side > 0 else bar["l"] <= tr["tp"]
        if hit_sl:
            return [{"type": "CLOSE", "price": tr["sl"], "why": "stop hit" if not tr["be"] else "breakeven stop"}]
        if hit_tp:
            return [{"type": "CLOSE", "price": tr["tp"], "why": "take profit hit"}]
        r_now = (bar["c"] - tr["entry"]) * side / r_dist
        if not tr["be"] and r_now >= 1:
            out.append({"type": "UPDATE_SL", "sl": tr["entry"] + side * tr["entry"] * (FEE_PCT * 2), "why": "+1R, stop to breakeven", "be": True})
        elif tr["be"] and r_now >= 1.5:
            trail = bar["c"] - side * 1.0 * rd["atr"]
            if (trail - tr["sl"]) * side > 0.1 * rd["atr"]:
                out.append({"type": "UPDATE_SL", "sl": trail, "why": f"trail at +{r_now:.1f}R"})
        if self.bar - tr["opened_bar"] >= tr["max_bars"]:
            out.append({"type": "CLOSE", "price": bar["c"], "why": "time exit"})
        elif tr["strategy"] == "trend" and rd["side"] == -side and rd["score"] >= 5:
            out.append({"type": "CLOSE", "price": bar["c"], "why": "trend flipped"})
        return out

    def agent_hedge(self) -> dict | None:
        mains = [t for t in self.trades.values() if t["kind"] == "main" and not t.get("hedge_id")]
        if not mains:
            return None
        equity = self.equity()
        worst = min(mains, key=lambda t: t["upnl"])
        open_dd = sum(t["upnl"] for t in self.trades.values()) / equity
        corr_hi = False
        if len(mains) >= 2:
            cs = [_corr(list(self.assets[x["asset"]].rets), list(self.assets[y["asset"]].rets)) * x["side"] * y["side"]
                  for i, x in enumerate(mains) for y in mains[i + 1:]]
            corr_hi = sum(cs) / len(cs) > MAX_CORR
        if open_dd < -0.004 or corr_hi:
            return {"agent": "hedge", "of": worst["id"], "asset": worst["asset"], "side": -worst["side"],
                    "qty": worst["qty"] * 0.4, "confidence": 0.8,
                    "reason": f"open drawdown {open_dd * 100:.2f}%" if not corr_hi else "open trades too correlated"}
        return None

    # ---------- money ----------

    def _fill(self, price: float, side: int, opening: bool) -> float:
        slip = price * SLIP_PCT
        return price + slip * side if opening else price - slip * side

    def equity(self) -> float:
        return self.balance + sum(t["upnl"] for t in self.trades.values())

    def _open(self, p: dict, qty: float, kind: str = "main", of: int | None = None) -> dict:
        self.ids += 1
        price = self._fill(p["entry"], p["side"], True)
        fee = price * qty * FEE_PCT
        self.balance -= fee
        tr = {"id": self.ids, "asset": p["asset"], "side": p["side"], "qty": qty, "entry": price, "sl": p.get("sl"),
              "tp": p.get("tp"), "r_dist": abs(price - p["sl"]) if p.get("sl") else 0, "be": False, "kind": kind,
              "strategy": p["strategy"], "agent": p["agent"], "opened_bar": self.bar, "opened_t": self.t,
              "max_bars": p.get("max_bars", 90), "fees": fee, "upnl": -fee * 0, "states": [], "of": of,
              "confidence": p.get("confidence"), "reason": p.get("reason", "")}
        return tr

    def _close(self, tr: dict, price: float, why: str) -> None:
        exit_px = self._fill(price, tr["side"], False)
        fee = exit_px * tr["qty"] * FEE_PCT
        gross = (exit_px - tr["entry"]) * tr["side"] * tr["qty"]
        pnl = gross - fee - tr["fees"]
        self.balance += gross - fee
        r_mult = (exit_px - tr["entry"]) * tr["side"] / tr["r_dist"] if tr["r_dist"] else 0.0
        self._state(tr, "CLOSING")
        self._state(tr, "CLOSED")
        self._state(tr, "LOGGED")
        self._state(tr, "REVIEWED")
        row = {"id": tr["id"], "asset": tr["asset"], "side": "LONG" if tr["side"] > 0 else "SHORT", "kind": tr["kind"],
               "strategy": tr["strategy"], "agent": tr["agent"], "entry": tr["entry"], "exit": exit_px, "qty": tr["qty"],
               "pnl": round(pnl, 2), "r": round(r_mult, 2), "bars": self.bar - tr["opened_bar"], "why": why,
               "fees": round(fee + tr["fees"], 2), "won": pnl > 0, "opened": tr["opened_t"], "closed": self.t,
               "states": tr["states"]}
        self.closed.append(row)
        del self.trades[tr["id"]]
        self._msg("exit", "CLOSED", f"{tr['asset']} {row['side']} closed: {why}, {'+' if pnl >= 0 else '-'}${abs(pnl):,.2f} ({r_mult:+.1f}R)",
                  pnl=row["pnl"], r=row["r"])
        recent = [r["pnl"] for r in self.closed if r["strategy"] == tr["strategy"] and r["kind"] == "main"][-15:]
        if tr["kind"] == "main" and len(recent) >= 15 and sum(recent) < 0 and self.benched.get(tr["strategy"], 0) <= self.bar:
            self.benched[tr["strategy"]] = self.bar + DAY_BARS
            self._msg("conductor", "BENCH", f"Review: {STRATEGY_NAMES[tr['strategy']]} lost ${-sum(recent):,.0f} over its last 15 trades, "
                      "benched for a practice day", strategy=tr["strategy"])
        if not self.warming:
            self.toasts.append({"id": tr["id"], "asset": tr["asset"], "pnl": row["pnl"], "r": row["r"],
                                "mins": row["bars"], "strategy": STRATEGY_NAMES[tr["strategy"]], "t": self.t})
        if tr["kind"] == "main" and tr.get("hedge_id") in self.trades:
            h = self.trades[tr["hedge_id"]]
            self._close(h, self.assets[h["asset"]].price, "original trade closed")

    # ---------- the loop ----------

    def _advance(self) -> None:
        self.bar += 1
        self.t += 60
        if self.bar % DAY_BARS == 0:
            self.day_pnls.append(self.equity() - self.day_start_equity)
            self.day_start_equity = self.equity()
            self._msg("conductor", "NEW_DAY", "New practice day: risk budget reset, levels re-marked")
        common = {f: self.rng.gauss(0, 1) for f in ("risk", "macro", "gold")}
        for a in self.assets.values():
            a.next_bar(self.t, common)
        if len(next(iter(self.assets.values())).bars) < 60:
            return
        reads = {s: self.read(a) for s, a in self.assets.items()}
        for tr in self.trades.values():
            px = self.assets[tr["asset"]].price
            tr["upnl"] = (px - tr["entry"]) * tr["side"] * tr["qty"] - tr["fees"]

        self.agent_news()
        # Kill switch: daily loss limit closes everything and pauses entries for the day.
        if self.equity() - self.day_start_equity <= -DAILY_LOSS_PCT * self.day_start_equity and self.killed_day != self.bar // DAY_BARS:
            self.killed_day = self.bar // DAY_BARS
            self._msg("risk", "KILL_SWITCH", "Daily loss limit hit: close all, pause entries for the day")
            for tr in list(self.trades.values()):
                if tr["id"] in self.trades:
                    self._close(tr, self.assets[tr["asset"]].price, "kill switch")

        # Priority Risk > Hedge > Exit > Entry.
        h = self.agent_hedge()
        if h and h["of"] in self.trades:
            orig = self.trades[h["of"]]
            hp = {"agent": "hedge", "asset": h["asset"], "side": h["side"], "entry": self.assets[h["asset"]].price,
                  "strategy": "hedge", "max_bars": 10_000, "confidence": h["confidence"], "reason": h["reason"]}
            tr = self._open(hp, h["qty"], kind="hedge", of=orig["id"])
            tr["r_dist"] = orig["r_dist"]
            for s in ("PROPOSED", "RISK_CHECK", "APPROVED", "EXECUTING", "OPEN"):
                self._state(tr, s)
            self.trades[tr["id"]] = tr
            orig["hedge_id"] = tr["id"]
            self._state(orig, "HEDGED")
            self._msg("hedge", "HEDGE", f"Hedge {orig['asset']}: {'SHORT' if h['side'] < 0 else 'LONG'} 40% size, {h['reason']}",
                      of=orig["id"], size="40%")
        for tr in list(self.trades.values()):
            if tr["id"] not in self.trades or tr["kind"] != "main":
                continue
            a = self.assets[tr["asset"]]
            for act in self.agent_exit(tr, a, reads[tr["asset"]]):
                if act["type"] == "CLOSE":
                    self._close(tr, act["price"], act["why"])
                    break
                tr["sl"] = act["sl"]
                tr["be"] = tr["be"] or act.get("be", False)
                if tr["states"][-1] != "MANAGING":
                    self._state(tr, "MANAGING")
                self._msg("exit", "UPDATE_SL", f"{tr['asset']}: {act['why']}", trade=tr["id"], sl=round(tr["sl"], 6))
        # Hedges come off when the drawdown recovers.
        for tr in list(self.trades.values()):
            if tr["kind"] == "hedge" and tr["of"] in self.trades and self.trades[tr["of"]]["upnl"] > 0:
                self.trades[tr["of"]]["hedge_id"] = None
                self._close(tr, self.assets[tr["asset"]].price, "drawdown recovered")

        proposals = []
        for s, a in self.assets.items():
            p = self.agent_entry(a, reads[s]) if a.agent == "entry" else self.agent_scalper(a, reads[s])
            if p and self.benched.get(p["strategy"], 0) <= self.bar:
                proposals.append(p)
        # Conflicts: one proposal per asset, higher confidence wins.
        best: dict[str, dict] = {}
        for p in proposals:
            if p["asset"] not in best or p["confidence"] > best[p["asset"]]["confidence"]:
                best[p["asset"]] = p
        for p in sorted(best.values(), key=lambda x: -x["confidence"]):
            if any(t["asset"] == p["asset"] for t in self.trades.values()) or self.cooldown.get(p["asset"], 0) > self.bar:
                continue
            self._msg(p["agent"], "PROPOSE", f"{p['asset']} {p['direction']} @ {p['entry']:.5g}, SL {p['sl']:.5g}, TP {p['tp']:.5g}, "
                      f"conf {p['confidence']:.2f} ({p['reason']})", asset=p["asset"], direction=p["direction"],
                      entry=round(p["entry"], 6), sl=round(p["sl"], 6), tp=round(p["tp"], 6), confidence=p["confidence"])
            tr_states = ["PROPOSED", "RISK_CHECK"]
            self._msg("conductor", "CHECK", f"Ask Risk about {p['asset']} {p['direction']}", equity=round(self.equity(), 2),
                      open_positions=len(self.trades))
            verdict, info = self.risk_check(p)
            if verdict == "REJECTED":
                for s in tr_states + ["REJECTED"]:
                    self.state_counts[s] += 1
                self.rejects[info["reason"].split(" (")[0].split(" with")[0]] = self.rejects.get(info["reason"].split(" (")[0].split(" with")[0], 0) + 1
                self.cooldown[p["asset"]] = self.bar + 10
                self._msg("risk", "REJECTED", f"{p['asset']} rejected: {info['reason']}", reason=info["reason"])
                continue
            self._msg("risk", verdict, f"{p['asset']} {verdict.lower()}: risk ${info['risk_usd']:,.0f} ({info['size_pct']}% of equity)", **info)
            tr = self._open(p, info["qty"])
            for s in tr_states + ["APPROVED", "EXECUTING", "OPEN"]:
                self._state(tr, s)
            self.trades[tr["id"]] = tr
            self.approved_pulse += 1
            self._msg("conductor", "EXECUTE", f"{p['asset']} {p['direction']} market order, qty {info['qty']:.4g} (paper)",
                      order="MARKET", qty=round(info["qty"], 6))

        eq = self.equity()
        self.peak = max(self.peak, eq)
        self.max_dd = max(self.max_dd, (self.peak - eq) / self.peak)
        if self.bar % 5 == 0:
            self.equity_curve.append({"t": self.t, "equity": round(eq, 2), "balance": round(self.balance, 2)})

    def step(self, now: float | None = None) -> None:
        now = time.time() if now is None else now
        with self.lock:
            if now - self.last_step < STEP_SECONDS:
                return
            self.last_step = now
            self._advance()

    # ---------- the page ----------

    def stats(self) -> dict:
        rows = list(self.closed)
        mains = [r for r in rows if r["kind"] == "main"]
        wins = [r for r in mains if r["won"]]
        gross_win = sum(r["pnl"] for r in mains if r["pnl"] > 0)
        gross_loss = -sum(r["pnl"] for r in mains if r["pnl"] < 0)
        by = {}
        for r in rows:
            s = by.setdefault(r["strategy"], {"name": STRATEGY_NAMES[r["strategy"]], "n": 0, "wins": 0, "pnl": 0.0, "r": 0.0})
            s["n"] += 1
            s["wins"] += r["won"]
            s["pnl"] = round(s["pnl"] + r["pnl"], 2)
            s["r"] = round(s["r"] + r["r"], 2)
        days = self.day_pnls + [self.equity() - self.day_start_equity]
        return {"trades": len(mains), "wins": len(wins), "win_rate": round(len(wins) / len(mains) * 100, 1) if mains else None,
                "expectancy": round(sum(r["pnl"] for r in mains) / len(mains), 2) if mains else None,
                "avg_r": round(sum(r["r"] for r in mains) / len(mains), 2) if mains else None,
                "profit_factor": round(gross_win / gross_loss, 2) if gross_loss else None,
                "max_drawdown_pct": round(self.max_dd * 100, 2), "worst_day": round(min(days), 2),
                "fees": round(sum(r["fees"] for r in rows), 2), "by_strategy": by}

    def snapshot(self, bars: int = BARS_KEPT) -> dict:
        self.step()
        with self.lock:
            reads = {s: self.read(a) for s, a in self.assets.items()}
            charts = []
            for s, a in self.assets.items():
                b = list(a.bars)[-bars:]
                closes = [x["c"] for x in a.bars]
                e9, e21 = ema(closes, 9)[-bars:], ema(closes, 21)[-bars:]
                opens = [t for t in self.trades.values() if t["asset"] == s]
                rd = reads[s]
                charts.append({
                    "symbol": s, "name": a.name, "strategy": STRATEGY_NAMES[a.strategy], "agent": a.agent,
                    "bars": [{k: (round(x[k], 6) if k != "t" else x[k]) for k in "tohlcv"} for x in b],
                    "ema9": [round(v, 6) if v is not None else None for v in e9],
                    "ema21": [round(v, 6) if v is not None else None for v in e21],
                    "price": round(a.price, 6), "change": round((a.price / b[0]["o"] - 1) * 100, 2),
                    "score": rd["score"], "parts": rd["parts"], "rsi": round(rd["rsi"], 1), "side": rd["side"],
                    "levels": {"hi20": round(rd["hi20"], 6), "lo20": round(rd["lo20"], 6), "vwap": round(rd["vwap"], 6)},
                    "trades": [{"id": t["id"], "kind": t["kind"], "side": "LONG" if t["side"] > 0 else "SHORT",
                                "entry": round(t["entry"], 6), "sl": round(t["sl"], 6) if t.get("sl") else None,
                                "tp": round(t["tp"], 6) if t.get("tp") else None, "upnl": round(t["upnl"], 2),
                                "be": t["be"], "opened": t["opened_t"], "state": t["states"][-1]} for t in opens],
                    "marks": [{"t": r["opened"], "side": r["side"], "kind": "in"} for r in self.closed if r["asset"] == s and r["opened"] >= b[0]["t"]]
                             + [{"t": r["closed"], "side": r["side"], "kind": "out", "won": r["won"]} for r in self.closed
                                if r["asset"] == s and r["closed"] >= b[0]["t"]]
                             + [{"t": t["opened_t"], "side": "LONG" if t["side"] > 0 else "SHORT", "kind": "in"} for t in opens],
                })
            eq = self.equity()
            realized = self.balance - START_EQUITY
            day = eq - self.day_start_equity
            at, name = self.next_news
            return {
                "mode": "practice simulation", "time": self.t, "bar": self.bar,
                "account": {"balance": round(self.balance, 2), "equity": round(eq, 2), "realized": round(realized, 2),
                            "unrealized": round(eq - self.balance, 2), "today": round(day, 2),
                            "margin_used": round(sum(t["qty"] * self.assets[t["asset"]].price for t in self.trades.values()), 2),
                            "open": len(self.trades), "start": START_EQUITY},
                "risk": {"risk_pct": RISK_PCT * 100, "daily_loss_pct": DAILY_LOSS_PCT * 100, "max_open": MAX_OPEN,
                         "max_corr": MAX_CORR, "score_gate": SCORE_GATE, "fee_pct": FEE_PCT * 100, "slip_pct": SLIP_PCT * 100,
                         "daily_used_pct": round(max(0.0, -day) / self.day_start_equity * 100, 2),
                         "paused": self.paused_until > self.bar, "pause_reason": self.pause_reason if self.paused_until > self.bar else "",
                         "killed": self.killed_day == self.bar // DAY_BARS,
                         "next_news": {"name": name, "in_min": at - self.bar}},
                "charts": charts, "log": list(self.log)[-80:], "toasts": list(self.toasts),
                "closed": list(self.closed)[::-1][:40], "stats": self.stats(), "states": self.state_counts,
                "rejects": dict(sorted(self.rejects.items(), key=lambda kv: -kv[1])), "agents": AGENTS,
                "agent_counts": self.agent_counts,
                "benched": {STRATEGY_NAMES[k]: v - self.bar for k, v in self.benched.items() if v > self.bar}, "pulse": self.approved_pulse,
                "equity_curve": list(self.equity_curve),
            }


# ---------- our real agents, arranged as six departments ----------
# We keep our own structure: the desk's ~1,400 agents in four departments (trend, reversion, research,
# risk) under Atlas, Sage and Sentinel. The six floor roles are filled from those agents, by what each
# kind of agent already does. Research and Risk are each split in half by agent number.
FLOOR_DEPARTMENTS = [
    # key, name, source department, half (None = all of it), manager, agent kinds, job
    ("entry", "Entry", "trend", None, "Atlas", "Momentum, moving-average cross and breakout traders",
     "Find setups and propose entries"),
    ("scalper", "Scalper", "reversion", None, "Atlas", "Mean-reversion traders",
     "Fade stretched moves for small, fast targets"),
    ("news", "News", "research", 0, "Sage", "Regime analysts (even numbers) with the news and macro view",
     "Read headlines and the calendar; call a pause before big news"),
    ("exit", "Exit", "research", 1, "Sage", "Regime analysts (odd numbers) who call trending or choppy",
     "Say when a trend is over, so open trades get out in time"),
    ("risk", "Risk", "risk", 0, "Sentinel", "Volatility monitors (even numbers)",
     "Size every trade and veto anything over the limits"),
    ("hedge", "Hedge", "risk", 1, "Sentinel", "Volatility monitors (odd numbers)",
     "Vote to shrink or offset exposure when markets get wild"),
]
FLOOR_TO_ENGINE_AGENT = {"entry": "entry", "scalper": "scalper", "news": "news", "exit": "exit", "risk": "risk", "hedge": "hedge"}


def departments(org: dict | None, floor: dict | None = None, per_department: int = 350) -> dict:
    """Our desk's departments regrouped into the six floor departments, with managers and performance.

    `org` is the dashboard's merged org chart (/api/org). When the desk has not reported yet, sizes come
    from the configured per-department count and performance is left empty.
    """
    src = (org or {}).get("departments") or {}
    floor = floor or {}
    by_agent = {}
    for r in floor.get("closed", []):
        s = by_agent.setdefault(r["agent"], {"n": 0, "wins": 0, "pnl": 0.0})
        s["n"] += 1
        s["wins"] += r["won"]
        s["pnl"] = round(s["pnl"] + r["pnl"], 2)
    out = []
    for key, name, source, half, manager, kinds, job in FLOOR_DEPARTMENTS:
        d = src.get(source) or {}
        total = d.get("agents") or per_department
        size = total if half is None else (total + 1 - half) // 2
        votes = d.get("votes") or []
        mine = votes if half is None else votes[half::2]
        live = [v for v in mine if v is not None]
        closed = (d.get("trade_wins") or 0) + (d.get("trade_losses") or 0)
        out.append({
            "key": key, "name": name, "manager": manager, "kinds": kinds, "job": job, "agents": size,
            "source": source, "shares_with": None if half is None else next(
                f[1] for f in FLOOR_DEPARTMENTS if f[2] == source and f[0] != key),
            "active": len(live), "avg_vote": round(sum(live) / len(live), 4) if live else None,
            "buy": sum(1 for v in live if v > 0.1), "sell": sum(1 for v in live if v < -0.1),
            "desk": {"pnl": d.get("pnl"), "win_rate": round(d["trade_wins"] / closed, 4) if closed else None,
                     "skill": d.get("skill"), "trades": d.get("trades_total"), "graduates": (d.get("school") or {}).get("graduates")}
            if d else None,
            "floor": by_agent.get(FLOOR_TO_ENGINE_AGENT[key]),
            "messages": (floor.get("agent_counts") or {}).get(FLOOR_TO_ENGINE_AGENT[key], 0),
        })
    managers = []
    reports = {m.get("manager", "").lower(): m for m in (org or {}).get("managers", [])}
    for mkey, mname, title in (("atlas", "Atlas", "Head of Trading"), ("sage", "Sage", "Head of Research"),
                               ("sentinel", "Sentinel", "Head of Risk")):
        own = [d for d in out if d["manager"] == mname]
        managers.append({"key": mkey, "name": mname, "title": title, "departments": [d["name"] for d in own],
                         "agents": sum(d["agents"] for d in own), "call": (reports.get(mkey) or {}).get("call")})
    return {"total": sum(d["agents"] for d in out), "departments": out, "managers": managers,
            "live": bool(src), "tick": (org or {}).get("tick")}
