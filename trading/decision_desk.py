"""Decision desk: runs the fast gate for every symbol from what our desk already knows. PRACTICE ONLY.

Feeds the /decision page ("Decision layout" from the Practice trading scorecard app: one big 3D picture on top,
ten explaining panels underneath).
"""

from __future__ import annotations

from . import fast_gate as fg
from . import money_rules
from . import smart_money
from .atlas_sources import SYMBOL_TO_MARKET

PER_TRADE_CAP = 10.0  # $ risk per trade until the owner raises it (money rules)


def _risk_checks(org: dict, pnls: list[float]) -> list[tuple[str, bool, str]]:
    risk = org.get("departments", {}).get("risk", {})
    exposure = risk.get("avg_vote")
    last2 = [p for p in pnls[-2:]]
    checks = [
        ("Sentinel size", exposure is None or exposure >= 0.3,
         "warming up" if exposure is None else f"run {exposure:.0%} of normal size"),
        ("2 losses in a row", not (len(last2) == 2 and all(p < 0 for p in last2)),
         "pause 5 min and review" if len(last2) == 2 and all(p < 0 for p in last2) else "ok"),
        ("real money", True, "locked: practice orders only"),
    ]
    return checks


def build(org: dict, trades: dict, prices: dict, sources, smart=None) -> dict:
    deps = org.get("departments", {})
    closed = list(reversed(trades.get("trades", [])))  # oldest first
    pnls = [float(t.get("pnl") or 0.0) for t in closed]
    by_symbol = trades.get("by_symbol", {})
    hist = prices.get("history") or []
    symbols = sorted((hist[-1]["prices"] if hist else {}).keys()) or sorted(by_symbol) or []
    regime = deps.get("research", {}).get("avg_vote")
    macro = deps.get("research", {}).get("macro")
    sizing = fg.half_kelly(pnls, PER_TRADE_CAP)
    drift = fg.drift_breaker(pnls)
    risk = _risk_checks(org, pnls)
    calib = fg.Calibration()
    out = []
    for sym in symbols:
        heads = [
            fg.head_from_value("trend", deps.get("trend", {}).get("avg_vote"), 2.0, why="Entry dept (trend traders)"),
            fg.head_from_value("reversion", deps.get("reversion", {}).get("avg_vote"), 2.0, why="Scalper dept (reversion)"),
            fg.head_from_value("regime", None if regime is None else regime * (deps.get("trend", {}).get("avg_vote") or 0) * 4,
                               1.0, why="Sage: trend only counts when the market trends"),
            fg.head_from_value("news", macro, 4.0, why="Sage: tone of the news"),
        ]
        try:
            fv = sources.flow.view(sym) if sources else {"ready": False}
        except Exception:  # noqa: BLE001 - a missing feed abstains, it never breaks the page
            fv = {"ready": False}
        heads.append(fg.head_from_value("flow", fv["direction"] * max(fv["confidence"], 0.2) if fv.get("ready") else None,
                                        1.0, why="; ".join(fv.get("reasons", [])[:2]) or "order flow"))
        try:
            sv = sources.street.view(SYMBOL_TO_MARKET.get(sym, sym)) if sources else {"ready": False}
        except Exception:  # noqa: BLE001
            sv = {"ready": False}
        heads.append(fg.head_from_value("street", sv["direction"] * max(sv["confidence"], 0.2) if sv.get("ready") else None,
                                        1.0, why=f"{sv.get('houses', 0)} analyst houses"))
        heads.append(fg.history_head(by_symbol.get(sym)))
        v, sc, why = smart_money.vote(smart.summary(sym) if smart else None)
        heads.append(fg.Head("insiders", v, sc, why))
        out.append(fg.decide(sym, heads, risk, calib, sizing, drift))
    out.sort(key=lambda d: -d["confidence"])
    counts = {v: sum(1 for d in out if d["verdict"] == v) for v in ("ACT", "ESCALATE", "HOLD")}
    return {"decisions": out, "counts": counts, "sizing": sizing, "drift": drift,
            "rules": [{"rule": r, "detail": d} for r, d in money_rules.RULES],
            "graduation": money_rules.graduation(pnls),
            "departments": {k: {"name": v.get("name"), "agents": v.get("agents", 0), "active": v.get("active", 0),
                                "avg_vote": v.get("avg_vote"), "pnl": v.get("pnl", 0.0),
                                "win_rate": v.get("trade_win_rate")} for k, v in deps.items()},
            "managers": org.get("managers", []), "totals": trades.get("totals", {}), "mode": "practice"}
