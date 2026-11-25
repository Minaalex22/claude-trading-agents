"""News pipeline: triage -> gate -> deep read -> tags -> bill, for every headline the desk reads.

The pattern (a model cascade): a cheap first pass reads every headline and says how sure it is.
A gate keeps the sure ones and sends the unsure ones to a slower, deeper read. Every decision
is logged with what it cost, so the page can show the bill: cheap-only vs deep-only vs hybrid.

Demo mode (no model keys): the cheap pass is a rule-based reader and the deep read uses the
reading room's news + chart view for that market. Costs are the configured estimates per
headline, not a real invoice. The gate logic is the same either way.

Steps for each headline:
1. DECOMPILE: normalise it to {symbol, kind, event, value, text}. Seen before? Use the cache.
2. TRIAGE: cheap read -> lean (-1 sell .. +1 buy), confidence 0..1, reason, evidence.
   Verifier rules can force confidence down: unknown event, no market named, answers disagree.
3. GATE (tiered, starts at 0.8):  >= gate+0.1 accept; >= gate accept (5% audited by the deep read);
   below gate escalate; below 0.4 escalate and flag for a human. Overrule: high-impact headlines
   (central banks, sanctions, war, elections...) always go to the deep read.
4. DEEP READ: lean + tag each finding as observation / inference / unknown. Still unsure -> human queue.
5. TAGS + BILL: tag the headline, add its cost. Audits move the gate: if the deep read disagrees with
   kept answers too often the gate goes up; if it always agrees the gate comes down.
"""

from __future__ import annotations

import hashlib
import random
from collections import deque

START_GATE = 0.8
GATE_MIN, GATE_MAX = 0.7, 0.9
AUDIT_RATE = 0.05
HUMAN_BELOW = 0.4
CHEAP_COST = 0.0025   # $ per headline, estimate for the cheap model
DEEP_COST = 0.0235    # $ per headline, estimate for the deep model
CHEAP_MODEL = "claude-haiku-5-5"
DEEP_MODEL = "claude-opus-5-5"
LOG_KEEP = 200

# Always send these to the deep read, however sure the cheap pass is.
OVERRULE = ("central bank", "interest rate", "rate decision", "sanction", "war", "election", "crackdown",
            "default", "bankrupt", "fed ", "ecb", "rba", "tariff")
# What the cheap pass knows: word -> lean.
BULL = {"above forecast": 0.7, "beats": 0.7, "upgrade": 0.6, "record": 0.5, "approval": 0.6, "deal": 0.4,
        "cut rates": 0.5, "stimulus": 0.6, "growth": 0.4, "rally": 0.5, "buyback": 0.5, "eases": 0.4}
BEAR = {"below forecast": -0.7, "misses": -0.7, "downgrade": -0.6, "crackdown": -0.6, "sanction": -0.6,
        "tariff": -0.5, "lawsuit": -0.5, "recall": -0.5, "war": -0.7, "hike": -0.4, "probe": -0.5, "too close": -0.3,
        "default": -0.8, "layoffs": -0.4, "above forecast": 0.0}


def _key(h: dict) -> str:
    return hashlib.sha1(f"{h.get('symbol')}|{h.get('text')}".encode()).hexdigest()[:16]


def cheap_read(h: dict) -> dict:
    """Rule-based first pass. Returns lean, confidence, reason, evidence (the JSON the cheap model would return)."""
    text = str(h.get("text", "")).lower()
    hits = [(w, v) for w, v in {**BULL, **{k: v for k, v in BEAR.items() if v}}.items() if w in text]
    if not hits:
        return {"lean": 0.0, "confidence": 0.25, "reason": "no known wording", "evidence": "", "valid": True}
    lean = max(-1.0, min(1.0, sum(v for _, v in hits)))
    mixed = any(v > 0 for _, v in hits) and any(v < 0 for _, v in hits)
    conf = min(0.97, 0.55 + 0.2 * len(hits) + 0.25 * abs(lean)) * (0.6 if mixed else 1.0)
    return {"lean": round(lean, 3), "confidence": round(conf, 3), "reason": "mixed wording" if mixed else "",
            "evidence": ", ".join(w for w, _ in hits), "valid": True}


def verify(h: dict, t: dict) -> list[str]:
    """Cheap deterministic checks. Any failure forces an escalation, whatever confidence was claimed."""
    fails = []
    if not t.get("valid"):
        fails.append("bad answer")
    if not h.get("symbol"):
        fails.append("no market named")
    if not t.get("evidence"):
        fails.append("no evidence")
    if cheap_read(h)["lean"] != t["lean"]:  # self-consistency: a second identical read must agree
        fails.append("answers differ")
    return fails


def deep_read(h: dict, view: dict | None) -> dict:
    """The deeper read: the headline plus the reading room's news and chart view for that market."""
    t = cheap_read(h)
    tags = []
    if t["evidence"]:
        tags.append({"tag": "observation", "text": f"headline says: {t['evidence']}"})
    if view:
        chart, news = float(view.get("chart") or 0), float(view.get("news") or 0)
        lean = max(-1.0, min(1.0, 0.5 * t["lean"] + 0.3 * news + 0.2 * chart))
        tags.append({"tag": "inference", "text": f"news mood {news:+.2f}, chart {chart:+.2f}"})
        agree = (t["lean"] >= 0) == (chart >= 0) or not t["lean"]
        conf = min(0.97, 0.6 + 0.25 * abs(lean) + (0.1 if agree else -0.15) + (0.1 if t["evidence"] else -0.2))
    else:
        lean, conf = t["lean"], 0.5 if t["evidence"] else 0.3
    if conf < START_GATE:
        tags.append({"tag": "unknown", "text": "not enough to call it"})
    return {"lean": round(lean, 3), "confidence": round(max(0.05, conf), 3), "tags": tags}


def topic(h: dict) -> str:
    text = str(h.get("text", "")).lower()
    for word, tag in (("rate", "rates"), ("bank", "central-bank"), ("pmi", "economy"), ("tariff", "trade"),
                      ("sanction", "geopolitics"), ("election", "politics"), ("regulat", "regulation"),
                      ("earning", "earnings")):
        if word in text:
            return tag
    return str(h.get("kind") or "general")


class NewsPipeline:
    def __init__(self, seed: int | None = None):
        self.gate = START_GATE
        self.rng = random.Random(seed)
        self.cache: dict[str, dict] = {}
        self.log: deque[dict] = deque(maxlen=LOG_KEEP)
        self.human: deque[dict] = deque(maxlen=50)
        self.n = {"read": 0, "cached": 0, "kept": 0, "escalated": 0, "overruled": 0, "human": 0, "audited": 0,
                  "audit_agree": 0, "cheap_unknown": 0, "deep_unknown": 0, "hybrid_unknown": 0}
        self.cost = {"hybrid": 0.0, "cheap_only": 0.0, "deep_only": 0.0}
        self.gate_moves: deque[dict] = deque(maxlen=20)
        # Optional X crowd read for the deep read: symbol -> lean (-1..1) or None. Set by the dashboard when Grok
        # is on (trading/grok.py XPulse.lean); cached, so no model is called inside the pipeline.
        self.crowd = None

    def feed(self, headlines: list[dict], views: dict | None = None, now: float | None = None) -> None:
        for h in headlines or []:
            self.run(h, (views or {}).get(h.get("symbol")), now)

    def run(self, h: dict, view: dict | None = None, now: float | None = None) -> dict | None:
        key = _key(h)
        if key in self.cache:
            if h.get("confirmed"):  # the same story from a second source: the earlier read is now confirmed
                self.cache[key]["confirmed"] = True
            return None  # seen it: no model is called, nothing is billed
        self.n["read"] += 1
        t = cheap_read(h)
        fails = verify(h, t)
        conf = t["confidence"] if not fails else min(t["confidence"], 0.39)
        text = str(h.get("text", "")).lower()
        overrule = next((w.strip() for w in OVERRULE if w in text), None)
        deep = deep_read(h, view)  # demo: free to compute, so cheap-only / deep-only columns are measured too
        deep = self._with_crowd(h, deep)
        self.cost["cheap_only"] += CHEAP_COST
        self.cost["deep_only"] += DEEP_COST
        self.n["cheap_unknown"] += conf < HUMAN_BELOW
        self.n["deep_unknown"] += deep["confidence"] < START_GATE

        route, audit = "cheap", False
        if overrule:
            route = "deep"
            self.n["overruled"] += 1
        elif conf < self.gate:
            route = "deep"
        elif conf < self.gate + 0.1 and self.rng.random() < AUDIT_RATE * 4:
            audit = True  # accepted, but the deep read checks it (drift check)
        cost = CHEAP_COST + (DEEP_COST if route == "deep" or audit else 0.0)
        self.cost["hybrid"] += cost

        final = deep if route == "deep" else t
        # Human queue: the deep read is still unsure, or both reads were very unsure.
        human = route == "deep" and (deep["confidence"] < 0.6 or (conf < HUMAN_BELOW and deep["confidence"] < START_GATE))
        if route == "deep":
            self.n["escalated"] += 1
        else:
            self.n["kept"] += 1
        if human:
            self.n["human"] += 1
            self.n["hybrid_unknown"] += 1
        if audit:
            self._audit(t, deep)

        rec = {"symbol": h.get("symbol"), "text": h.get("text"), "kind": h.get("kind"), "live": h.get("kind") == "live",
               "triage": {"job": f"{h.get('symbol') or '?'} {topic(h)} headline, leans {'BUY' if t['lean'] > 0.15 else 'SELL' if t['lean'] < -0.15 else 'WAIT'}",
                          "lean": t["lean"], "confidence": conf, "why": t["reason"] or ", ".join(fails), "evidence": t["evidence"]},
               "gate": round(self.gate, 2), "route": route, "overrule": overrule, "audit": audit, "human": human,
               "deep": deep if route == "deep" or audit else None, "final_lean": final["lean"],
               "call": "BUY" if final["lean"] > 0.15 else "SELL" if final["lean"] < -0.15 else "WAIT",
               "topic": topic(h), "cost": round(cost, 4),
               "final_conf": deep["confidence"] if route == "deep" else conf,
               "confirmed": bool(h.get("confirmed")), "at": now}
        self.cache[key] = rec
        if len(self.cache) > 5000:
            self.cache.pop(next(iter(self.cache)))
        self.log.append(rec)
        if human:
            self.human.append(rec)
        return rec

    def _with_crowd(self, h: dict, deep: dict) -> dict:
        """Add Grok's read of X on this market to the deep read: one inference tag and a small share of the lean."""
        try:
            crowd = self.crowd(h.get("symbol")) if self.crowd and h.get("symbol") else None
        except Exception:  # noqa: BLE001 - a missing crowd read never breaks the pipeline
            crowd = None
        if crowd is None:
            return deep
        from .grok import CROWD_SHARE
        lean = max(-1.0, min(1.0, (1 - CROWD_SHARE) * deep["lean"] + CROWD_SHARE * crowd))
        tags = deep["tags"] + [{"tag": "inference", "text": f"X crowd (Grok) {crowd:+.2f}"}]
        return {**deep, "lean": round(lean, 3), "tags": tags, "crowd": crowd}

    def _audit(self, t: dict, deep: dict) -> None:
        self.n["audited"] += 1
        agree = (t["lean"] > 0) == (deep["lean"] > 0) or abs(t["lean"] - deep["lean"]) < 0.2
        self.n["audit_agree"] += agree
        if self.n["audited"] % 5 == 0:  # every 5 audits, move the gate
            rate = self.n["audit_agree"] / self.n["audited"]
            was = self.gate
            if rate < 0.8:
                self.gate = min(GATE_MAX, round(self.gate + 0.05, 2))
            elif rate > 0.95:
                self.gate = max(GATE_MIN, round(self.gate - 0.05, 2))
            if self.gate != was:
                self.gate_moves.append({"from": was, "to": self.gate, "agree": round(rate, 3)})

    def lean(self, symbol: str, last: int = 20) -> tuple[float | None, int]:
        """Net verdict of the pipeline's recent reads on one symbol, newest weighted most, for the agents' gate."""
        recs = [r for r in list(self.log)[::-1] if r.get("symbol") == symbol][:last]
        if not recs:
            return None, 0
        w = [0.9 ** i for i in range(len(recs))]
        return sum(r["final_lean"] * wi for r, wi in zip(recs, w)) / sum(w), len(recs)

    def desk_lean(self, symbol: str, now: float | None, need: float = 0.8, half_life_mins: float = 90.0) -> float | None:
        """The lean the desk may use: only reads at least `need` sure AND confirmed by a second source, faded
        by time (half every `half_life_mins`). None when no read qualifies."""
        best = None
        for r in reversed(self.log):
            if r.get("symbol") != symbol or not r.get("confirmed") or r.get("final_conf", 0) < need:
                continue
            age = (now - r["at"]) / 60 if now is not None and r.get("at") is not None else 0.0
            best = r["final_lean"] * 0.5 ** (max(0.0, age) / half_life_mins)
            break
        return best

    def doubtful(self, symbol: str, now: float | None, below: float = 0.6, open_mins: float = 60.0) -> bool:
        """An unsure read on this symbol is still in the human queue (filed in the last `open_mins`)."""
        for r in self.human:
            if r.get("symbol") == symbol and r.get("final_conf", 1) < below and (
                    now is None or r.get("at") is None or now - r["at"] <= open_mins * 60):
                return True
        return False

    def snapshot(self) -> dict:
        c = {k: round(v, 4) for k, v in self.cost.items()}
        save = 1 - c["hybrid"] / c["deep_only"] if c["deep_only"] else 0.0
        return {"mode": "demo", "models": {"cheap": CHEAP_MODEL, "deep": DEEP_MODEL},
                "prices": {"cheap": CHEAP_COST, "deep": DEEP_COST}, "gate": self.gate, "start_gate": START_GATE,
                "tiers": {"accept": round(self.gate + 0.1, 2), "accept_audit": self.gate, "human_below": HUMAN_BELOW},
                "counts": dict(self.n), "cost": c, "savings": round(save, 4),
                "unknowns": {"cheap_only": self.n["cheap_unknown"], "deep_only": self.n["deep_unknown"],
                             "hybrid": self.n["hybrid_unknown"]},
                "audit_agree": round(self.n["audit_agree"] / self.n["audited"], 3) if self.n["audited"] else None,
                "gate_moves": list(self.gate_moves), "log": list(self.log)[::-1][:80], "human": list(self.human)[::-1][:20]}
