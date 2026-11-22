"""Polymarket practice desk for the /polymarket page.

Runs the simulated Polymarket books and the bot's three strategies (trading/polymarket.py) on paper:
neg-risk squeeze (all YES outcomes of one event cost under $1), cross-book arbitrage (YES on one book +
NO on the other cost under $1) and maker rebates (quote both sides near 50c and earn part of the fee).
Nothing is sent to Polymarket: books are simulated and fills are paper. Polymarket is not legal for
Australians, so this stays a simulation.
"""

from __future__ import annotations

import os
import threading
import time
from collections import deque

from . import config as ats_config
from .polymarket import PolymarketBot, SimulatedPolymarket

STEP_SECONDS = 2.0  # one simulated book update at most every 2 seconds
HISTORY = 300


class PolymarketDesk:
    def __init__(self, seed: int = 7, env=os.environ):
        self.cfg = ats_config.load(env.get("ATS_CONFIG"))
        self.market = SimulatedPolymarket(seed=seed)
        self.bot = PolymarketBot(self.cfg, seed=seed)
        self.weights = {s: 1 / 3 for s in PolymarketBot.STRATEGIES}
        self.lock = threading.Lock()
        self.last = 0.0
        self.snap = None
        self.pnl = {s: 0.0 for s in PolymarketBot.STRATEGIES}
        self.count = {s: 0 for s in PolymarketBot.STRATEGIES}
        self.curve: deque[dict] = deque(maxlen=HISTORY)
        self.trades: deque[dict] = deque(maxlen=60)
        self.scanned = 0

    def step(self, now: float | None = None) -> None:
        now = time.time() if now is None else now
        with self.lock:
            if self.snap is not None and now - self.last < STEP_SECONDS:
                return
            self.last = now
            self.snap = self.market.tick()
            self.scanned += len(self.snap.neg_risk) + len(self.snap.cross_book) + len(self.snap.binaries)
            for o in self.bot.scan(self.snap, self.weights):
                self.pnl[o.strategy] += o.locked_pnl
                self.count[o.strategy] += 1
                self.trades.append({"ts": round(now, 1), "strategy": o.strategy, "pnl": round(o.locked_pnl, 2),
                                    "legs": [{k: leg[k] for k in ("instrument", "side", "qty", "price", "expected_slippage_pct")} for leg in o.legs]})
            self.curve.append({"ts": round(now, 1), "tick": self.market.ticks, "pnl": round(sum(self.pnl.values()), 2),
                               **{s: round(v, 2) for s, v in self.pnl.items()}})

    def snapshot(self) -> dict:
        self.step()
        with self.lock:
            s = self.snap
            events = [{"id": e.event_id, "category": e.category, "sum": round(sum(o.yes_ask for o in e.outcomes), 4),
                       "outcomes": [{"name": o.name, "ask": o.yes_ask, "depth": round(o.depth_usdc)} for o in e.outcomes]} for e in s.neg_risk]
            pairs = [{"id": p.market_id, "category": p.category, "yes_a": p.yes_ask_a, "no_b": p.no_ask_b,
                      "cost": round(p.yes_ask_a + p.no_ask_b, 4), "depth": round(min(p.depth_a, p.depth_b))} for p in s.cross_book]
            lo, hi = self.cfg.thresholds.maker_band
            binaries = [{"id": b.market_id, "category": b.category, "mid": b.mid, "depth": round(b.depth_usdc),
                         "in_band": lo <= b.mid <= hi} for b in s.binaries]
            return {"mode": "simulated", "tick": self.market.ticks, "scanned": self.scanned,
                    "pnl": {k: round(v, 2) for k, v in self.pnl.items()}, "total": round(sum(self.pnl.values()), 2),
                    "count": dict(self.count), "curve": list(self.curve), "trades": list(self.trades)[::-1][:30],
                    "events": events, "pairs": pairs, "binaries": binaries,
                    "rules": {"neg_risk_epsilon": self.cfg.thresholds.neg_risk_epsilon,
                              "cross_book_min_spread": self.cfg.thresholds.cross_book_min_spread,
                              "maker_band": [lo, hi], "min_depth_usdc": self.cfg.risk.min_depth_usdc,
                              "max_trade_usdc": self.cfg.risk.max_trade_usdc}}
