"""Daily close, ledger check and weekly audit (blueprint functions #7, #8 and #9). PRACTICE ONLY.

- Ledger check (#7): every chosen (desk) trade closed today must have its entry and exit inside the prices the
  desk actually saw for that symbol that day, and the P&L must add up (qty x entry x move = pnl).
- Daily close (#8): one end-of-day report in Melbourne time (written at 08:15, after the US close): chosen trades,
  wins, losses, win rate, P&L, the worst drop along the day's trades, Sentinel's blocks from the audit log, and
  what the managers did.
- Weekly audit (#9): the last 7 days' chosen-trade win rate against the practice benchmark (SIM_WIN_RATE, 62.9%
  on the unseen seeds). A gap over 10 points with at least 30 trades flags overfitting: the practice market
  flatters the strategy, so nothing should be promoted on its practice numbers.
The desk's chosen trades only: agents' practice-quota trades never reach the desk's trade record.
"""
from __future__ import annotations

from datetime import timedelta

from .night_shift import _local

CLOSE_AT = (8, 15)              # Melbourne, after the New York close
WEEKLY_DAY = 5                  # Saturday
SIM_WIN_RATE = 62.9             # tools/win_rate_bench.py unseen seeds (PR #22)
DRIFT_POINTS = 10.0
MIN_SAMPLE = 30
PRICE_SLACK = 0.002             # 0.2%: ticks are sampled, a fill can sit just outside the ticks we kept


def _day(ts, now=None) -> str:
    return _local(ts).strftime("%Y-%m-%d")


def ledger_check(trades: list[dict], history: list[dict]) -> dict:
    """Entries and exits inside the day's seen price range, and P&L that adds up."""
    lo, hi = {}, {}
    for h in history:
        for s, p in (h.get("prices") or {}).items():
            lo[s] = min(lo.get(s, p), p)
            hi[s] = max(hi.get(s, p), p)
    problems = []
    for t in trades:
        s = t.get("symbol")
        for k in ("entry", "exit"):
            p = t.get(k)
            if p is None or s not in lo:
                continue
            if not lo[s] * (1 - PRICE_SLACK) <= p <= hi[s] * (1 + PRICE_SLACK):
                problems.append(f"{s} {k} {p:g} outside the day's prices {lo[s]:g}-{hi[s]:g}")
        if None not in (t.get("qty"), t.get("entry"), t.get("move"), t.get("pnl")):
            want = t["qty"] * t["entry"] * t["move"]
            if abs(want - t["pnl"]) > max(0.05, abs(want) * 0.01):
                problems.append(f"{s} P&L {t['pnl']:g} doesn't add up (expected {want:.2f})")
    return {"checked": len(trades), "ok": not problems, "problems": problems[:20],
            "net_pnl": round(sum(float(t.get("pnl") or 0) for t in trades), 2)}


def report(trades: list[dict], history: list[dict], managers: list | None = None, audit: dict | None = None,
           now=None, day: str | None = None) -> dict:
    """The daily close for one Melbourne day (default: the day that just ended)."""
    t = _local(now)
    if day is None:
        back = 1 if (t.hour, t.minute) >= CLOSE_AT else 2
        day = (t - timedelta(days=back)).strftime("%Y-%m-%d")
    todays = sorted((x for x in trades if x.get("ts") and _day(x["ts"]) == day), key=lambda x: x["ts"])
    hist = [h for h in history if h.get("ts") and _day(h["ts"]) == day]
    ledger = ledger_check(todays, hist)
    wins = sum(1 for x in todays if x.get("won"))
    eq = peak = worst = 0.0
    for x in todays:
        eq += float(x.get("pnl") or 0)
        peak = max(peak, eq)
        worst = max(worst, peak - eq)
    rows = [r for r in (audit or {}).get("rows", []) if str(r.get("at", "")).startswith(day) or not r.get("at")]
    by_symbol: dict[str, dict] = {}
    for x in todays:
        b = by_symbol.setdefault(x["symbol"], {"trades": 0, "won": 0, "pnl": 0.0})
        b["trades"] += 1
        b["won"] += 1 if x.get("won") else 0
        b["pnl"] = round(b["pnl"] + float(x.get("pnl") or 0), 2)
    return {
        "day": day, "written": t.strftime("%Y-%m-%d %H:%M Melbourne"),
        "trades": len(todays), "wins": wins, "losses": len(todays) - wins,
        "win_rate": round(100 * wins / len(todays), 1) if todays else None,
        "pnl": ledger["net_pnl"], "worst_drop": round(worst, 2), "ledger": ledger,
        "checker": {"passed": sum(1 for r in rows if r.get("passed")), "failed": sum(1 for r in rows if not r.get("passed"))},
        "managers": [{"name": m.get("manager"), "call": m.get("call")} for m in managers or []][:3],
        "by_symbol": dict(sorted(by_symbol.items(), key=lambda kv: -kv[1]["pnl"])),
    }


def weekly_audit(trades: list[dict], now=None, sim_win_rate: float = SIM_WIN_RATE) -> dict:
    """Last 7 days of chosen trades vs the practice benchmark."""
    t = _local(now)
    start = (t - timedelta(days=7)).timestamp()
    week = [x for x in trades if (x.get("ts") or 0) >= start]
    n = len(week)
    real = round(100 * sum(1 for x in week if x.get("won")) / n, 1) if n else None
    gap = None if real is None else round(sim_win_rate - real, 1)
    if n < MIN_SAMPLE:
        verdict = f"Not enough trades yet ({n} of {MIN_SAMPLE}) to judge the gap."
    elif gap > DRIFT_POINTS:
        verdict = (f"Overfitting warning: real trades win {real}% vs {sim_win_rate}% in practice "
                   f"({gap} points lower). Don't promote anything on practice numbers.")
    else:
        verdict = f"OK: real {real}% vs practice {sim_win_rate}% ({gap:+} points)."
    return {"week_ending": t.strftime("%Y-%m-%d"), "trades": n, "real_win_rate": real,
            "practice_win_rate": sim_win_rate, "gap_points": gap, "flag": bool(n >= MIN_SAMPLE and gap > DRIFT_POINTS),
            "pnl": round(sum(float(x.get("pnl") or 0) for x in week), 2), "verdict": verdict}


def close_due(now, last_day: str | None) -> str | None:
    t = _local(now)
    if (t.hour, t.minute) >= CLOSE_AT:
        day = (t - timedelta(days=1)).strftime("%Y-%m-%d")
        return day if day != last_day else None
    return None


def text(r: dict, w: dict | None = None) -> str:
    if not r["trades"]:
        s = f"Daily close {r['day']}: no desk trades."
    else:
        s = (f"Daily close {r['day']}: {r['trades']} desk trades, {r['wins']} won ({r['win_rate']}%), "
             f"P&L ${r['pnl']:,.2f}, worst drop ${r['worst_drop']:,.2f}. "
             f"Ledger {'OK' if r['ledger']['ok'] else 'has problems: ' + r['ledger']['problems'][0]}.")
    s += f" Sentinel passed {r['checker']['passed']}, blocked {r['checker']['failed']}."
    if w:
        s += " Weekly audit: " + w["verdict"]
    return s
