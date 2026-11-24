"""Two-speed brain for every trader: a fast half that proposes, a slow half that audits (System 1 + System 2).

From mina's "bicameral brain" plan, keeping only what fits our desk. Pure Python, no model calls, a few
arithmetic steps per agent per bar, so 1,400 agents stay fast.

**Fast half (`propose`)**: turns the agent's own signal into a concrete setup instead of a bare impulse: the
side, a setup tag (style + market type: trend, chop or volatility spike), the move it expects (its average
bar move x sqrt(its window)) and where it is wrong (the exit plan's 1R).

**Slow half (`audit`)**, before any NEW exposure leaves the agent (exits are never blocked):
1. Anti-tilt: after `TILT_LOSSES` chosen losses in a row the agent waits `TILT_BARS` bars before opening.
2. Friction: the expected move must cover `FRICTION_MULT` round-trip costs (agents pay `accounts.COST_BPS`).
3. Regime ledger: every closed chosen trade is written under its (setup tag, market type). With a Bayesian
   prior (48% wins, worth 6 trades) the ledger gives a posterior win rate and an expectancy in R. Once a setup
   has `MIN_SAMPLES` trades, a negative expectancy (below `MIN_EXPECTED_R`) vetoes it in that market type.
4. Premortem: a trade against a strong news + chart read (`PREMORTEM_VIEW`) during a volatility spike is
   vetoed: that is how such a trade usually fails.

Practice-quota trades never reach the ledger or the tilt counter. `share_best` copies the best agent's ledger
along with its rules. The fast half's "gene mutation" from the plan is left out: our strategies already tune
their own rules in `review.py`, and two tuners on one rule fight each other. Switch: env DUAL_BRAIN (on/off).
Paper only.
"""

from __future__ import annotations

import math

ENABLED = True
TILT_LOSSES = 3
TILT_BARS = 5
FRICTION_MULT = 2.0
PRIOR_WIN, PRIOR_N = 0.48, 6.0
MIN_SAMPLES = 12
MIN_EXPECTED_R = 0.0
PREMORTEM_VIEW = 0.6
VOL_SPIKE = 1.6
TREND_ER, CHOP_ER = 0.35, 0.2

COUNTS = {"tilt": 0, "friction": 0, "ledger": 0, "premortem": 0, "passed": 0}


def _regime(note: dict) -> str:
    if note.get("vol_ratio", 1.0) > VOL_SPIKE:
        return "vol_spike"
    return "trend" if note.get("er", 0.0) >= TREND_ER else "chop" if note.get("er", 0.0) < CHOP_ER else "mixed"


class Ledger(dict):
    """(setup tag, market type) -> [wins, losses, sum of R]."""

    def row(self, key: str) -> list:
        return self.setdefault(key, [0, 0, 0.0])

    def posterior(self, key: str) -> float:
        w, lost, _ = self.get(key, (0, 0, 0.0))
        return (w + PRIOR_WIN * PRIOR_N) / (w + lost + PRIOR_N)

    def expectancy(self, key: str) -> float | None:
        w, lost, r = self.get(key, (0, 0, 0.0))
        return r / (w + lost) if w + lost else None


def state(agent) -> dict:
    return agent.workspace.setdefault("dual", {"ledger": Ledger(), "streak": 0, "tilt_left": 0, "vetoes": {}})


def propose(agent, direction: float) -> dict:
    """Fast half: the setup this signal means, with its expected move and where it is wrong."""
    from . import entry_filters, exits

    note = entry_filters.features(agent, direction)
    p = list(agent.prices)
    step = sum(abs(b / a - 1) for a, b in zip(p, p[1:])) / max(1, len(p) - 1) if len(p) > 1 else 0.0
    regime = _regime(note)
    return {"side": 1.0 if direction > 0 else -1.0, "tag": f"{agent.style}:{regime}", "regime": regime,
            "expected_move": step * math.sqrt(max(1, agent.window)),
            "stop": exits.risk_unit(p) / p[-1] if p else 0.0, "note": note}


def audit(agent, proposal: dict) -> str | None:
    """Slow half: the reason to veto this setup, or None to let it go."""
    from . import accounts

    s = state(agent)
    if s["tilt_left"] > 0:
        return "tilt"
    if proposal["expected_move"] < FRICTION_MULT * 2 * accounts.COST_BPS / 10_000:
        return "friction"
    ledger = s["ledger"]
    w, lost, _ = ledger.get(proposal["tag"], (0, 0, 0.0))
    if w + lost >= MIN_SAMPLES:
        exp = ledger.expectancy(proposal["tag"])
        if exp is not None and exp < MIN_EXPECTED_R:
            return "ledger"
    view = getattr(agent, "view", None)
    if view is not None and view.direction * proposal["side"] <= -PREMORTEM_VIEW and proposal["regime"] == "vol_spike":
        return "premortem"
    return None


def check(agent, value: float, signal: float) -> float:
    """Gate a vote: new exposure needs the slow half's yes. Holding, trimming and exits always pass."""
    if not ENABLED or not value or agent.role != "trader" or agent.must_practice:
        return value
    s = state(agent)
    if s["tilt_left"] > 0:
        s["tilt_left"] -= 1
    held = 1.0 if agent.position > 0 else -1.0 if agent.position < 0 else 0.0
    d = 1.0 if value > 0 else -1.0
    if d == held and abs(value) <= abs(agent.position):
        return value
    prop = propose(agent, d)
    why = audit(agent, prop)
    if why is None:
        COUNTS["passed"] += 1
        agent.workspace["dual_tag"] = prop["tag"]
        return value
    COUNTS[why] += 1
    s["vetoes"][why] = s["vetoes"].get(why, 0) + 1
    return agent.position if d == held else 0.0


def closed(agent, won: bool, move: float, forced: bool) -> None:
    """A trade closed: chosen trades teach the ledger and the tilt counter; practice trades teach nothing."""
    tag = agent.workspace.pop("dual_tag", None)
    if forced or not ENABLED:
        return
    s = state(agent)
    plan_r = agent.workspace.get("last_r_pct") or 0.0
    r = move / plan_r if plan_r else (1.0 if won else -1.0)
    if tag:
        row = s["ledger"].row(tag)
        row[0] += won
        row[1] += not won
        row[2] += max(-3.0, min(6.0, r))
    s["streak"] = 0 if won else s["streak"] + 1
    if s["streak"] >= TILT_LOSSES:
        s["tilt_left"] = TILT_BARS
        s["streak"] = 0


def copy(best, weak) -> None:
    """share_best: the weak agent takes the best agent's ledger at half weight (what works where), and a clean
    tilt counter, so it learns caution at once but its own chosen trades soon outweigh the mentor's."""
    src = state(best)["ledger"]
    dst = state(weak)
    dst["ledger"] = Ledger({k: [w // 2, lost // 2, r * 0.5] for k, (w, lost, r) in src.items()})
    dst["streak"] = 0
    dst["tilt_left"] = 0


def reset() -> None:
    for k in COUNTS:
        COUNTS[k] = 0
