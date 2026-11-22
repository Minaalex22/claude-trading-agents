"""Fast decision gate: typed heads -> one confidence -> ACT / ESCALATE / HOLD. PRACTICE ONLY.

Ported from the "Gey" Mac system (Minaalex22/jarvis fast_gate.py) and fitted to our desk. No AI text while deciding:
every head answers a typed question (vote +1 up / -1 down / 0 abstain, score 0..1) from numbers the desk already
has, so a decision takes milliseconds. Heads come from our own departments and managers, not a new team:

  trend      Entry department (Atlas): average vote of the trend traders
  reversion  Scalper department (Atlas): average vote of the reversion traders
  regime     Research (Sage): trending (+) or choppy (-); only backs trend when the market trends
  news       Research (Sage): net tone of the news the analysts read (macro)
  flow       order flow book: aggressive buyers vs sellers for the symbol
  street     analyst houses (Atlas sources): their net call on the market
  history    our own closed practice trades in this symbol: votes only with 30+ trades and a Wilson range clear of 50%
  insiders   SEC Form 4 open-market buys and sells by company insiders (smart_money.py); abstains without data
  risk       Sentinel: never votes; a failed risk check turns any verdict into HOLD

CONFIDENCE = (scores with the direction - scores against) / heads that voted, times min(1, votes / 4), so one or two
voices can never reach the gate. ACT needs confidence >= 0.85, 3+ heads agreeing and every risk check passing.
0.60..0.85 is ESCALATE (shown to the owner, nothing placed). Below that, HOLD.

SELF-WEIGHTING: after 30 checked votes a head's weight = 1 + 4 x (hit rate - 0.5), kept within 0.25..2.
SIZING: half Kelly on the latest 30 closed trades, never above the per-trade cap; Kelly <= 0 means no edge -> HOLD.
DRIFT BREAKER: after 20 closed trades, ACT pauses if the live win rate is 15 points under the baseline or the
average net is below zero.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

ACT_AT = 0.85
ESCALATE_AT = 0.60
MIN_AGREE = 3
FULL_VOICE = 4
CALIBRATE_AFTER = 30
HEADS = ("trend", "reversion", "regime", "news", "flow", "street", "history")


@dataclass
class Head:
    name: str
    vote: int = 0  # +1 up, -1 down, 0 abstain
    score: float = 0.0
    why: str = "no data"

    def as_dict(self, weight: float) -> dict:
        return {"head": self.name, "vote": {1: "up", -1: "down", 0: "abstain"}[self.vote],
                "score": round(self.score, 3), "weight": round(weight, 3), "why": self.why}


def wilson(wins: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 1.0)
    p = wins / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, c - h), min(1.0, c + h))


def head_from_value(name: str, value: float | None, scale: float = 1.0, dead: float = 0.02, why: str = "") -> Head:
    """A department average or a -1..1 reading becomes a vote: sign = direction, size = score."""
    if value is None:
        return Head(name, why="warming up")
    if abs(value) < dead:
        return Head(name, why=f"flat ({value:+.2f})")
    return Head(name, 1 if value > 0 else -1, min(1.0, abs(value) * scale), why or f"reading {value:+.2f}")


def history_head(stats: dict | None) -> Head:
    """Our closed practice trades in this symbol: only a clear, well-sampled record may vote."""
    if not stats or stats.get("trades", 0) < CALIBRATE_AFTER:
        n = (stats or {}).get("trades", 0)
        return Head("history", why=f"{n} closed trades, needs {CALIBRATE_AFTER}")
    lo, hi = wilson(stats["won"], stats["trades"])
    if lo > 0.5:
        return Head("history", 1, min(1.0, (lo - 0.5) * 4 + 0.3), f"won {lo:.0%}-{hi:.0%} of {stats['trades']} going with the desk")
    if hi < 0.5:
        return Head("history", -1, min(1.0, (0.5 - hi) * 4 + 0.3), f"won only {lo:.0%}-{hi:.0%} of {stats['trades']}")
    return Head("history", why=f"range {lo:.0%}-{hi:.0%} straddles 50%")


@dataclass
class Calibration:
    """Hit record per head: votes checked against the price later. Feeds the self-weighting."""
    hits: dict = field(default_factory=dict)
    checked: dict = field(default_factory=dict)

    def record(self, head: str, right: bool) -> None:
        self.checked[head] = self.checked.get(head, 0) + 1
        self.hits[head] = self.hits.get(head, 0) + (1 if right else 0)

    def weight(self, head: str) -> float:
        n = self.checked.get(head, 0)
        if n < CALIBRATE_AFTER:
            return 1.0
        return max(0.25, min(2.0, 1 + 4 * (self.hits.get(head, 0) / n - 0.5)))


def half_kelly(pnls: list[float], cap: float) -> dict:
    """Half Kelly on the latest 30 closed trades: f = W - (1 - W) / (avg win / avg loss)."""
    last = [p for p in pnls[-CALIBRATE_AFTER:] if p]
    if len(last) < CALIBRATE_AFTER:
        return {"ready": False, "size": cap * 0.5, "why": f"{len(last)} closed trades, half size until {CALIBRATE_AFTER}"}
    wins, losses = [p for p in last if p > 0], [-p for p in last if p < 0]
    if not wins or not losses:
        return {"ready": True, "kelly": None, "size": cap * 0.5, "why": "one-sided record, half size"}
    w = len(wins) / len(last)
    ratio = (sum(wins) / len(wins)) / (sum(losses) / len(losses))
    f = w - (1 - w) / ratio
    if f <= 0:
        return {"ready": True, "kelly": round(f, 3), "size": 0.0, "why": "no edge: Kelly <= 0"}
    return {"ready": True, "kelly": round(f, 3), "size": round(min(cap, cap * f / 2 * 4), 2),
            "why": f"half Kelly {f / 2:.1%}"}


def drift_breaker(pnls: list[float], baseline_win: float = 0.5) -> dict:
    last = [p for p in pnls[-50:] if p]
    if len(last) < 20:
        return {"tripped": False, "why": f"{len(last)} closed trades, checks after 20"}
    win = sum(1 for p in last if p > 0) / len(last)
    avg = sum(last) / len(last)
    tripped = win < baseline_win - 0.15 or avg < 0
    why = (f"win {win:.0%} vs baseline {baseline_win:.0%}, average net {avg:+.2f}"
           + (": ACT paused" if tripped else ""))
    return {"tripped": tripped, "win": round(win, 4), "avg_net": round(avg, 4), "why": why}


def decide(symbol: str, heads: list[Head], risk_checks: list[tuple[str, bool, str]],
           calib: Calibration | None = None, sizing: dict | None = None, drift: dict | None = None) -> dict:
    calib = calib or Calibration()
    weights = {h.name: calib.weight(h.name) for h in heads}
    voting = [h for h in heads if h.vote]
    up = sum(h.score * weights[h.name] for h in voting if h.vote > 0)
    down = sum(h.score * weights[h.name] for h in voting if h.vote < 0)
    direction = 1 if up > down else -1 if down > up else 0
    wsum = sum(weights[h.name] for h in voting)
    confidence = 0.0
    if voting and direction and wsum:
        confidence = abs(up - down) / wsum * min(1.0, len(voting) / FULL_VOICE)
    agree = sum(1 for h in voting if h.vote == direction)
    risk_ok = all(ok for _, ok, _ in risk_checks)
    reasons = []
    if confidence >= ACT_AT and agree >= MIN_AGREE and risk_ok:
        verdict = "ACT"
    elif confidence >= ESCALATE_AT:
        verdict = "ESCALATE"
    else:
        verdict = "HOLD"
    if verdict == "ACT" and drift and drift.get("tripped"):
        verdict, _ = "ESCALATE", reasons.append("drift breaker: " + drift["why"])
    if verdict == "ACT" and sizing and sizing.get("size", 1) <= 0:
        verdict, _ = "HOLD", reasons.append(sizing["why"])
    if not risk_ok:
        verdict = "HOLD"
        reasons += [f"risk: {name} ({why})" for name, ok, why in risk_checks if not ok]
    if confidence >= ACT_AT and agree < MIN_AGREE:
        reasons.append(f"only {agree} heads agree, needs {MIN_AGREE}")
    if not voting:
        reasons.append("every head abstained")
    return {"symbol": symbol, "verdict": verdict, "direction": {1: "buy", -1: "sell", 0: "none"}[direction],
            "confidence": round(confidence, 4), "agree": agree, "voting": len(voting),
            "heads": [h.as_dict(weights[h.name]) for h in heads],
            "risk": [{"check": n, "ok": ok, "why": w} for n, ok, w in risk_checks],
            "sizing": sizing, "drift": drift, "reasons": reasons,
            "gates": {"act": ACT_AT, "escalate": ESCALATE_AT, "min_agree": MIN_AGREE}, "mode": "practice"}
