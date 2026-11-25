"""One-pass replay / desk inspector (mina, 25 Nov 2026).

    python tools/inspect_replay_stats.py [EVENTS_DIR]        # default: $EVENTS_DIR or ./events

Reads what the desk already writes into its events folder:
  * events-*.jsonl   "closed" events = the desk's chosen trades (practice-quota trades never reach the desk book, so
                     none are in here; any row flagged practice is still skipped and counted)
  * desk_audit*.jsonl the checker's PASS/FAIL verdicts (trading/trade_checker.py)
  * promotion.json   sim win rates and sign-offs for the live-unlock gates (trading/strategy_lab.py)

Prints the checker summary (top FAIL reasons), the chosen-trade win rate / profit factor / max drawdown per Orbit arm
and overall, and each arm's live-unlock blockers from the real 3-gate promotion. Writes chosen_trades_orbit.csv
(date, arm, symbol, pnl_usd) next to the logs. Read-only: it never signs anything off.
"""
from __future__ import annotations

import csv
import json
import os
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from trading import strategy_lab  # noqa: E402
from trading.trade_checker import FILE as AUDIT_FILE  # noqa: E402

START_EQUITY = 100_000.0


def _jsonl(path: Path):
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    yield json.loads(line)
                except ValueError:
                    continue  # a half-written last line while the desk is running


def summarize_audit(events_dir: Path) -> dict:
    passed = failed = 0
    reasons: Counter[str] = Counter()
    stem = AUDIT_FILE.rsplit(".", 1)[0]
    for path in sorted(events_dir.glob(f"{stem}*.jsonl")):
        for rec in _jsonl(path):
            if rec.get("passed"):
                passed += 1
                continue
            failed += 1
            for part in str(rec.get("verdict", "FAIL: unknown")).removeprefix("FAIL: ").split("; "):
                reasons[re.sub(r"\s*\([+-]?[\d.]+\)$", "", part.strip()) or "unknown"] += 1  # group "(+0.27)" variants
    return {"checked": passed + failed, "passed": passed, "failed": failed,
            "top_fail_reasons": reasons.most_common(5)}


def closed_trades(events_dir: Path) -> tuple[list[dict], int]:
    rows, practice = [], 0
    for path in sorted(events_dir.glob("events-*.jsonl")):
        for ev in _jsonl(path):
            if ev.get("type") != "closed":
                continue
            if ev.get("practice"):
                practice += 1
                continue
            rows.append(ev)
    rows.sort(key=lambda e: float(e.get("ts") or 0))
    return rows, practice


def summarize_trades(rows: list[dict], start_equity: float = START_EQUITY) -> dict:
    eq = peak = start_equity
    dd = gw = gl = 0.0
    wins = 0
    for t in rows:
        p = float(t.get("pnl") or 0.0)
        eq += p
        peak = max(peak, eq)
        dd = max(dd, (peak - eq) / peak)
        if p > 0:
            wins += 1
            gw += p
        else:
            gl -= p
    n = len(rows)
    return {"chosen_trades": n, "win_rate_pct": round(wins / n * 100, 2) if n else None,
            "profit_factor": round(gw / gl, 3) if gl else (None if not gw else 99.0),
            "net_pnl_usd": round(eq - start_equity, 2), "max_drawdown_pct": round(dd * 100, 3)}


def export_orbit_csv(rows: list[dict], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["date", "arm", "symbol", "pnl_usd"])
        for t in rows:
            arm = strategy_lab.arm_of(t.get("symbol", ""))
            ts = t.get("ts")
            day = datetime.fromtimestamp(float(ts), timezone.utc).strftime("%Y-%m-%d") if ts else ""
            w.writerow([day, arm.key if arm else "", t.get("symbol", ""), f"{float(t.get('pnl') or 0):.2f}"])
    return path


def inspect(events_dir: Path) -> dict:
    rows, practice = closed_trades(events_dir)
    gates = strategy_lab.check(rows, events_dir)
    return {
        "events_dir": str(events_dir),
        "checker": summarize_audit(events_dir),
        "overall": {**summarize_trades(rows), "practice_rows_skipped": practice},
        "arms": {k: {"trades": g["trades"], "win_rate": g["win_rate"], "profit_factor": g["profit_factor"],
                     "max_drawdown_of_sleeve": g.get("max_drawdown"), "net_pnl": g["net_pnl"],
                     "gates": g["gates"], "ready_for_signoff": g["gates"]["proof"] and g["gates"]["sim_vs_real"],
                     "promoted": g["promoted"], "blockers": g["missing"]} for k, g in gates.items()},
        "orbit_csv": str(export_orbit_csv(rows, events_dir / "chosen_trades_orbit.csv")),
    }


if __name__ == "__main__":
    folder = Path(sys.argv[1] if len(sys.argv) > 1 else os.environ.get("EVENTS_DIR", "events"))
    if not folder.is_dir():
        sys.exit(f"no events folder at {folder} (pass it as the first argument or set EVENTS_DIR)")
    print(json.dumps(inspect(folder), indent=2, default=str))
