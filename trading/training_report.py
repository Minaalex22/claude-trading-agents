"""Training report: how the agents' practice is going, read off the merged department report.

- overall and per-department trade win rate, P&L and exam average
- each department's weakest course, and the departments ranked weakest first
- how traders' trades ended (trading/exits.py: stop, breakeven, target, time)
- the confidence-gate test from the "gate 0.8" reel mina shared: don't guess the gate, test 0.7, 0.8 and 0.9
  against the agents' own closed practice trades and pick the lowest gate that is within `GATE_TOLERANCE`
  of the best win rate while keeping at least `GATE_MIN_SHARE` of the trades.

- the loss audit (trading/loss_audit.py): why the losing trades lost, each cause's share of the losses, per
  department and for the whole desk, plus how many trades each entry filter (trading/entry_filters.py) stopped

Served at GET /api/training and inside GET /api/report. Read-only, paper only.
"""

from . import loss_audit

GATES = (0.0, 0.5, 0.7, 0.8, 0.9)
GATE_TOLERANCE = 0.01
GATE_MIN_SHARE = 0.1
GATE_MIN_TRADES = 30


def _rate(w: int, n: int) -> float | None:
    return round(w / n, 4) if n else None


def gate_test(by_sure: dict) -> dict:
    """Win rate of the trades that would pass each gate (sureness >= gate), and the gate to use."""
    total = sum(n for _, n in by_sure.values())
    tests = []
    for g in GATES:
        w = sum(v[0] for b, v in by_sure.items() if float(b) >= g)
        n = sum(v[1] for b, v in by_sure.items() if float(b) >= g)
        tests.append({"gate": g, "win_rate": _rate(w, n), "trades": n, "share": _rate(n, total)})
    usable = [t for t in tests if t["trades"] >= GATE_MIN_TRADES and (t["share"] or 0) >= GATE_MIN_SHARE]
    best = max((t["win_rate"] for t in usable), default=None)
    pick = next((t["gate"] for t in usable if best is not None and t["win_rate"] >= best - GATE_TOLERANCE), None)
    return {"tests": tests, "recommended": pick, "trades": total}


def build(org: dict) -> dict:
    depts = org.get("departments", {})
    rows, exits, by_sure, causes, filtered = [], {}, {}, {}, {}
    wins = losses = 0
    for key, d in depts.items():
        school = d.get("school") or {}
        courses = {k: v.get("avg") for k, v in (school.get("courses") or {}).items() if v.get("avg") is not None}
        weakest_course = min(courses, key=courses.get) if courses else None
        w, l = d.get("trade_wins", 0), d.get("trade_losses", 0)
        wins, losses = wins + w, losses + l
        for reason, n in (d.get("exits") or {}).items():
            exits[reason] = exits.get(reason, 0) + n
        for k, n in (d.get("loss_causes") or {}).items():
            causes[k] = causes.get(k, 0) + n
        for k, n in (d.get("filtered") or {}).items():
            filtered[k] = filtered.get(k, 0) + n
        for b, (bw, bn) in (d.get("by_sure") or {}).items():
            row = by_sure.setdefault(b, [0, 0])
            row[0] += bw
            row[1] += bn
        rows.append({"department": key, "name": d.get("name", key), "agents": d.get("agents", 0),
                     "trades": w + l, "win_rate": _rate(w, w + l), "pnl": d.get("pnl"),
                     "exam": school.get("avg_exam"), "graduates": school.get("graduates"),
                     "trained": school.get("trained"), "sitting_out": d.get("sitting_out", 0),
                     "weakest_course": (school.get("courses") or {}).get(weakest_course, {}).get("name"),
                     "weakest_course_score": courses.get(weakest_course),
                     "calibration": d.get("calibration"),
                     "loss_causes": loss_audit.summarize(d.get("loss_causes") or {}, w, l)["top"]})

    def weakness(r):  # traders by win rate, the rest by exam; either way lower is weaker
        return (r["win_rate"] if r["win_rate"] is not None else (r["exam"] or 0)), r["pnl"] or 0

    ranked = sorted(rows, key=weakness)
    closed = sum(exits.values())
    return {"tick": org.get("tick", 0), "win_rate": _rate(wins, wins + losses), "trades": wins + losses,
            "departments": rows, "weakest_first": [r["department"] for r in ranked],
            "exits": {"counts": exits, "share": {k: _rate(v, closed) for k, v in exits.items()}},
            "gate": gate_test(by_sure),
            "loss_audit": {**loss_audit.summarize(causes, wins, losses), "filtered": filtered},
            "mode": "paper"}
