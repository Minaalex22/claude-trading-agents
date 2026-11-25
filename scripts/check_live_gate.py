"""Live-gate progress: does the replay's trend profile clear mina's bar, and how far is the live paper desk from
50 chosen real-price trades? (mina, 25 Nov 2026)

    python scripts/check_live_gate.py [EVENTS_DIR]       # default: $EVENTS_DIR or ./events
    python scripts/check_live_gate.py --csv logs/closed_trades.csv   # a CSV export with pnl_usd (and is_practice)

1. Unseen 2023-26 replay (trading/data/orbit_backtest.json) through trading/live_unlock_gate.py: with no sign-off,
   only the paper lock and the sign-off should remain.
2. The live paper / MT5 demo desk's closed trades (the "closed" events the desk writes; practice-quota trades
   never count): count toward 50, win rate, profit factor, payoff, drawdown, blockers, and each Orbit arm's
   3-gate status from trading/strategy_lab.py.
Read-only: it never signs off or unlocks anything.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from trading import strategy_lab  # noqa: E402
from trading.live_unlock_gate import evaluate_live_unlock_eligibility  # noqa: E402


def replay_check(path: Path = ROOT / "trading" / "data" / "orbit_backtest.json") -> dict:
    from audit_2023_2026_unlock import audit
    u = audit(json.loads(path.read_text()))["unseen_2023_2026"]
    ok, blockers = evaluate_live_unlock_eligibility(
        chosen_trades=u["trades"], real_win_rate_pct=u["win_rate_pct"], profit_factor=u["profit_factor"],
        sim_win_rate_pct=u["win_rate_pct"], max_drawdown_pct=u["max_dd_pct"], mina_explicit_signoff=False)
    return {"unseen_2023_2026": u, "ready_for_signoff": blockers == [b for b in blockers if b.startswith(
        ("paper_only_lock", "awaiting_explicit"))], "blockers": blockers}


def load_csv(path: Path) -> list[dict]:
    rows = []
    with path.open(encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if str(r.get("is_practice", r.get("is_practice_quota", "false"))).lower() in {"true", "1"}:
                continue
            rows.append({"pnl": float(r.get("pnl_usd") or r.get("pnl") or 0), "symbol": r.get("symbol", ""),
                         "qty": r.get("qty") or 0, "entry": r.get("entry") or 0})
    return rows


def live_check(rows: list[dict], events_dir: Path | None = None) -> dict:
    from inspect_replay_stats import summarize_trades
    s = summarize_trades(rows)
    n, wr, pf = s["chosen_trades"], s["win_rate_pct"] or 0.0, s["profit_factor"] or 0.0
    ok, blockers = evaluate_live_unlock_eligibility(
        chosen_trades=n, real_win_rate_pct=wr, profit_factor=pf, sim_win_rate_pct=wr if not n else 44.6,
        max_drawdown_pct=s["max_drawdown_pct"], mina_explicit_signoff=False)
    arms = {k: {"trades": a["trades"], "promoted": a["promoted"], "missing": a["missing"]}
            for k, a in strategy_lab.check(rows, events_dir).items()}
    return {"live_chosen_trades": n, "to_go": max(0, strategy_lab.MIN_TRADES - n), "live_wr": wr, "live_pf": pf,
            "payoff": strategy_lab.payoff_ratio(wr / 100, pf) if n else None,
            "max_drawdown_pct": s["max_drawdown_pct"], "blockers": blockers, "arms": arms}


def main(argv=None) -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("events_dir", nargs="?", default=os.environ.get("EVENTS_DIR", "events"))
    p.add_argument("--csv")
    a = p.parse_args(argv)
    print("OOS 2023-2026 expectancy check:", json.dumps(replay_check(), indent=1))
    if a.csv:
        rows, ev = load_csv(Path(a.csv)), None
    else:
        from inspect_replay_stats import closed_trades
        ev = Path(a.events_dir)
        rows = closed_trades(ev)[0] if ev.is_dir() else []
    print("Live paper / MT5 demo progress:", json.dumps(live_check(rows, ev), indent=1))


if __name__ == "__main__":
    main()
