"""Unlock audit: the Orbit replay split into 2020-22 (where settings were chosen) and unseen 2023-26, checked
against the live-unlock bar (trading/strategy_lab.py: 50+ trades, PF 1.35+, max drawdown 8%,
and 60%+ wins or 42%+ wins with payoff ratio 1.85+).

  python tools/audit_2023_2026_unlock.py                 # reads trading/data/orbit_backtest.json
  python tools/audit_2023_2026_unlock.py --file X.json

Run tools/orbit_backtest.py first to refresh the replay. This only reports: the real unlock still needs the
same numbers on the paper desk's own real-price trades, sim-vs-real within 15 points, and mina's sign-off.
Drawdown is per arm on its desk cash (closed trades in order); combined is the weighted Orbit account.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from trading import orbit_portfolio, strategy_lab  # noqa: E402

DATA = Path(__file__).resolve().parent.parent / "trading" / "data" / "orbit_backtest.json"
OOS_START = "2023-01-01"


def metrics(pnls: list[float], cash: float) -> dict:
    if not pnls:
        return {"trades": 0, "win_rate_pct": 0.0, "profit_factor": 0.0, "payoff": None, "net_pnl": 0.0,
                "max_dd_pct": 0.0}
    wins = sum(x for x in pnls if x > 0)
    losses = -sum(x for x in pnls if x <= 0)
    n_w = sum(x > 0 for x in pnls)
    eq = peak = cash
    dd = 0.0
    for x in pnls:
        eq += x
        peak = max(peak, eq)
        dd = max(dd, (peak - eq) / peak)
    return {"trades": len(pnls), "win_rate_pct": round(100 * sum(x > 0 for x in pnls) / len(pnls), 1),
            "profit_factor": round(wins / losses, 2) if losses else 99.0,
            "payoff": strategy_lab.payoff_ratio(n_w / len(pnls), wins / losses if losses else 99.0),
            "net_pnl": round(wins - losses, 2),
            "max_dd_pct": round(100 * dd, 2)}


def blockers(m: dict) -> list[str]:
    out = []
    if m["trades"] < strategy_lab.MIN_TRADES:
        out.append(f"trades {m['trades']} < {strategy_lab.MIN_TRADES}")
    if not strategy_lab.win_bar_ok(m["win_rate_pct"] / 100, m.get("payoff")):
        out.append(f"win rate {m['win_rate_pct']}% with payoff {m.get('payoff')}: needs "
                   f"{100 * strategy_lab.MIN_WIN_RATE:.0f}%+ or {100 * strategy_lab.ALT_MIN_WIN_RATE:.0f}%+ "
                   f"with payoff {strategy_lab.MIN_PAYOFF}+")
    if m["profit_factor"] < strategy_lab.MIN_PROFIT_FACTOR:
        out.append(f"profit factor {m['profit_factor']} < {strategy_lab.MIN_PROFIT_FACTOR}")
    if m["max_dd_pct"] > 100 * strategy_lab.MAX_DRAWDOWN:
        out.append(f"max drawdown {m['max_dd_pct']}% > {100 * strategy_lab.MAX_DRAWDOWN:.0f}%")
    return out


def audit(data: dict, oos_start: str = OOS_START) -> dict:
    cut = datetime.strptime(oos_start, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp()
    cash = float(data.get("desk_cash_per_arm") or 100_000.0)
    trades = sorted(data.get("trades", []), key=lambda t: t["t"])
    ins = [t for t in trades if t["t"] < cut]
    oos = [t for t in trades if t["t"] >= cut]
    arms = {}
    for arm in orbit_portfolio.ARMS:
        a_in = metrics([t["pnl"] for t in ins if t["arm"] == arm.key], cash)
        a_oos = metrics([t["pnl"] for t in oos if t["arm"] == arm.key], cash)
        arms[arm.key] = {"weight": arm.base_weight, "in_sample_2020_2022": a_in, "unseen_2023_2026": a_oos,
                         "unseen_blockers": blockers(a_oos) if arm.base_weight else ["benched (0% sleeve)"]}

    def combined(rows):
        c = orbit_portfolio.report(rows, use_heat=False, desk_cash=cash)["combined"]
        return {"trades": len(rows), "profit_factor": c.get("profit_factor"), "net_account_usd": round(c.get("net", 0), 2),
                "max_dd_pct": c.get("max_drawdown_pct")}

    oos_all = metrics([t["pnl"] for t in oos], cash * len(orbit_portfolio.ARMS))
    return {"source": data.get("source"), "made": data.get("made"), "oos_start": oos_start,
            "all_2020_2026": metrics([t["pnl"] for t in trades], cash * len(orbit_portfolio.ARMS)),
            "in_sample_2020_2022": metrics([t["pnl"] for t in ins], cash * len(orbit_portfolio.ARMS)),
            "unseen_2023_2026": oos_all,
            "weighted_account": {"in_sample_2020_2022": combined(ins), "unseen_2023_2026": combined(oos)},
            "arms": arms,
            "unseen_blockers": blockers(oos_all),
            "replay_passes_unlock_bar": not blockers(oos_all),
            "note": "Replay only. Real unlock also needs the paper desk's real-price trades, sim vs real and mina's sign-off."}


def main(argv=None) -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--file", default=str(DATA))
    p.add_argument("--oos-start", default=OOS_START)
    a = p.parse_args(argv)
    print(json.dumps(audit(json.loads(Path(a.file).read_text()), a.oos_start), indent=2))


if __name__ == "__main__":
    main()
