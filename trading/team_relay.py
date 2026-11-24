"""Team relay: the departments hand one shared blackboard down the floor instead of voting blind.

mina's design (24 Nov 2026). Before this, every agent ran `observe()` on the same tick without knowing what the
other departments had found, and the desk summed their numbers at the end. Now each bar runs in four phases, per
symbol, passing one MarketBlackboard along:

  1. Sage's floor (News + Exit analysts): how efficiently price is moving (0..1) -> BREAKOUT, TRANSITION or CHOP,
     plus the news lean and the 0DTE whale bias.
  2. Sentinel's floor (Risk + Hedge monitors): reads Sage's regime and the spread, sets the risk multiplier and the
     exit geometry: stop width in R and the take-profit in R (breakouts run to 2.2R; chop banks the snap at 0.95R).
  3. Atlas's floor (Entry + Scalper traders): traders observe *after* phases 1 and 2, with the blackboard in hand
     (their trades open with Sentinel's stop and target). Only the department that suits the regime counts:
     Entry in BREAKOUT, Scalper in CHOP, both in TRANSITION. Fast clusters (5-20 bars) never fire alone: they need
     Medium/Slow agreement, and a fast-vs-slow conflict is a veto.
  4. The desk: whale boost, the 0.72 bar, hooks, the nine-stage pipeline, risk, passive limits, exactly as before.
     The relay does not re-apply the 0.72 bar (the desk already does).

Each order's blackboard is logged (`relay` events) and attached to the Sentinel audit record, so every decision
shows what each floor said. Env TEAM_RELAY=off goes back to the old parallel vote. Paper only.

Fitted to our code: our analysts vote clip(efficiency*4 - 1), so the efficiency comes back as (vote+1)/4 and
tops out at 0.5. The breakout band therefore starts at 0.45 (the draft's 0.60 could never be reached). Sentinel's
losing-symbol half size stays in hooks.symbol_paused, so it isn't applied twice.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

ENABLED = True  # TEAM_RELAY=off: the old blind parallel vote
BREAKOUT_EFF, CHOP_EFF = 0.45, 0.25  # efficiency ratio bands (our analysts' vote tops out at efficiency 0.5)
MIN_VOTE = 0.25  # traders below this conviction sit the bar out
STOP_R = {"BREAKOUT": 1.15, "TRANSITION": 1.25, "CHOP": 1.35}  # Sentinel's stop width, in our 1R units
TARGET_R = {"BREAKOUT": 2.2, "TRANSITION": 1.5, "CHOP": 0.95}  # Sentinel's take-profit in R (reversion trades)
WIDE_SPREAD = 0.15  # spread above 15% of 1R: size x0.65


@dataclass
class MarketBlackboard:
    symbol: str
    timeframe: str = "1h"
    # phase 1: Sage
    regime_efficiency: float = 0.0
    regime_label: str = "CHOP"
    news_lean: float = 0.0
    whale_bias: float = 0.0
    # phase 2: Sentinel
    risk_multiplier: float = 1.0
    suggested_stop_r: float = 1.25
    suggested_target_r: float = 1.5
    # phase 3: Atlas
    active_style: str = "NONE"
    cluster_consensus: float = 0.0
    participating_agents: int = 0
    veto_reasons: list[str] = field(default_factory=list)

    def snapshot(self) -> dict:
        d = asdict(self)
        for k in ("regime_efficiency", "news_lean", "whale_bias", "cluster_consensus"):
            d[k] = round(d[k], 4)
        return d


def efficiency(analyst_vote: float) -> float:
    """Our RegimeAnalyst votes clip(efficiency*4 - 1); turn the average vote back into an efficiency ratio."""
    return max(0.0, min(1.0, (analyst_vote + 1) / 4))


def phase1_sage(bb: MarketBlackboard, analyst_votes: list[float], news_lean: float = 0.0,
                whale_bias: float = 0.0) -> MarketBlackboard:
    if analyst_votes:
        bb.regime_efficiency = efficiency(sum(analyst_votes) / len(analyst_votes))
    bb.regime_label = ("BREAKOUT" if bb.regime_efficiency >= BREAKOUT_EFF
                       else "CHOP" if bb.regime_efficiency < CHOP_EFF else "TRANSITION")
    bb.news_lean, bb.whale_bias = news_lean, whale_bias
    return bb


def phase2_sentinel(bb: MarketBlackboard, risk_votes: list[float], spread_to_r: float = 0.0) -> MarketBlackboard:
    raw = sum(risk_votes) / len(risk_votes) if risk_votes else 1.0
    if spread_to_r > WIDE_SPREAD:
        raw *= 0.65
    bb.risk_multiplier = round(max(0.0, min(1.0, raw)), 3)
    bb.suggested_stop_r = STOP_R[bb.regime_label]
    bb.suggested_target_r = TARGET_R[bb.regime_label]
    return bb


def phase3_atlas(bb: MarketBlackboard, horizons: dict) -> MarketBlackboard:
    """horizons: {"trend": {"fast": [...], "higher": [...]}, "reversion": {...}} (the desk's own split)."""
    if bb.risk_multiplier <= 0.10:
        bb.veto_reasons.append("Sentinel: risk floor shut (volatility)")
        return bb
    styles = {"BREAKOUT": ("trend",), "CHOP": ("reversion",)}.get(bb.regime_label, ("trend", "reversion"))
    bb.active_style = {"trend": "Entry", "reversion": "Scalper"}[styles[0]] if len(styles) == 1 else "Hybrid"
    fast = [v for s in styles for v in (horizons.get(s) or {}).get("fast", []) if abs(v) >= MIN_VOTE]
    higher = [v for s in styles for v in (horizons.get(s) or {}).get("higher", []) if abs(v) >= MIN_VOTE]
    bb.participating_agents = len(fast) + len(higher)
    if not higher:
        bb.veto_reasons.append("no Medium/Slow cluster confirmation")
        return bb
    h = sum(higher) / len(higher)
    if not fast:
        bb.cluster_consensus = h * 0.85
        return bb
    f = sum(fast) / len(fast)
    if f * h <= 0:
        bb.veto_reasons.append("Fast vs Medium/Slow clusters disagree")
        return bb
    bb.cluster_consensus = 0.45 * f + 0.55 * h
    return bb


def score(bb: MarketBlackboard) -> float:
    """The relay's desk score: Atlas's consensus sized by Sentinel. No 0.72 re-gate (the desk applies it)."""
    return 0.0 if bb.veto_reasons else max(-1.0, min(1.0, bb.cluster_consensus * bb.risk_multiplier))
