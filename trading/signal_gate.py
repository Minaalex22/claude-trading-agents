"""Gate pipeline on trade signals: the News Pipeline's cascade (cheap triage -> gate 0.8 -> deep read), applied to
every new trade a trader wants to open.

1. TRIAGE (cheap): the trader's own sureness (its mind's belief, or its signal strength) is the confidence.
   Below `REJECT_BELOW` the trade is dropped at once.
2. GATE (starts at 0.8, moves between 0.7 and 0.9): sure enough -> the trade goes ahead.
3. DEEP READ (only for the unsure ones): three independent checks vote: the reading room (news + chart) backs
   the side, the higher timeframe backs it, and the market type suits the trader's style. The trade goes ahead
   only if at least `DEEP_NEED` of the checks agree.
4. AUDIT: `AUDIT_RATE` of the trades the gate let straight through also get the deep read. If the deep read
   disagrees with them too often the gate goes up; if it always agrees the gate comes down (as in the News
   Pipeline).

Exits are never gated. Off by default (env SIGNAL_GATE=on). Measured: unseen practice runs (seeds 11-14) 62.9% ->
63.4% win rate with about 4% less desk profit; real BTC, ETH and SOL bars 47.4% -> 46.2% (15m) and 52.5% -> 50.2%
(5m). It helps the simulator a little and hurts on real prices, so it stays off.
Paper only.
"""

import random

from . import entry_filters

ENABLED = False
START_GATE = 0.8
GATE_MIN, GATE_MAX = 0.7, 0.9
REJECT_BELOW = 0.1
DEEP_NEED = 2
AUDIT_RATE = 0.05
AUDIT_WINDOW = 50

STATE = {"gate": START_GATE, "fast": 0, "deep_pass": 0, "deep_fail": 0, "rejected": 0, "audits": []}
_rng = random.Random(8)


def reset() -> None:
    STATE.update(gate=START_GATE, fast=0, deep_pass=0, deep_fail=0, rejected=0, audits=[])
    _rng.seed(8)


def deep_read(agent, direction: float) -> int:
    """How many of the three independent checks back a trade in `direction` (0..3)."""
    note = entry_filters.features(agent, direction)
    reversion = agent.style == "reversion"
    votes = 0
    votes += agent._agreement(direction) > 0
    votes += note["htf"] > 0 if not reversion else note["htf"] > -entry_filters.HTF_AGAINST
    votes += note["er"] >= entry_filters.ER_TREND if not reversion else note["er"] <= entry_filters.ER_RANGE
    return votes


def _audit(agent, direction: float) -> None:
    STATE["audits"].append(deep_read(agent, direction) >= DEEP_NEED)
    if len(STATE["audits"]) >= AUDIT_WINDOW:
        rate = sum(STATE["audits"]) / len(STATE["audits"])
        if rate < 0.8:
            STATE["gate"] = min(GATE_MAX, round(STATE["gate"] + 0.05, 2))
        elif rate > 0.95:
            STATE["gate"] = max(GATE_MIN, round(STATE["gate"] - 0.05, 2))
        STATE["audits"] = []


def check(agent, value: float, signal: float) -> float:
    """Gate the vote `value`: new exposure must pass triage, the gate or the deep read. Exits always pass."""
    if not ENABLED or not value:
        return value
    held = 1.0 if agent.position > 0 else -1.0 if agent.position < 0 else 0.0
    d = 1.0 if value > 0 else -1.0
    if d == held and abs(value) <= abs(agent.position):
        return value
    keep = agent.position if d == held else 0.0
    conf = agent.workspace.get("sureness", abs(signal))
    ws = agent.workspace.setdefault("gated", {})
    if conf < REJECT_BELOW:
        STATE["rejected"] += 1
        ws["rejected"] = ws.get("rejected", 0) + 1
        return keep
    if conf >= STATE["gate"]:
        STATE["fast"] += 1
        if _rng.random() < AUDIT_RATE:
            _audit(agent, d)
        return value
    if deep_read(agent, d) >= DEEP_NEED:
        STATE["deep_pass"] += 1
        return value
    STATE["deep_fail"] += 1
    ws["deep_fail"] = ws.get("deep_fail", 0) + 1
    return keep


def snapshot() -> dict:
    return {k: v for k, v in STATE.items() if k != "audits"} | {"on": ENABLED}
