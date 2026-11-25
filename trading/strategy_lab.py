"""Live-promotion checklist: three gates every strategy arm must pass before real money can follow it.

Real money stays locked until mina is happy with the win rate. This makes "happy" concrete and the same every
time. Per Orbit arm (trading/orbit_portfolio.py: FX majors, gold, US equities, crypto, USDJPY), using the desk's
own closed trades on real prices (the paper desk on MARKET=live), after costs:

1. **Out-of-sample proof**: at least MIN_TRADES (50) chosen trades, profit factor >= MIN_PROFIT_FACTOR (1.35),
   drawdown <= MAX_DRAWDOWN (8%), and the win-rate bar: win rate >= MIN_WIN_RATE (60%), OR win rate >=
   ALT_MIN_WIN_RATE (42%) with a payoff ratio (average win / average loss, implied as PF x (1 - win) / win)
   >= MIN_PAYOFF (1.85). mina, 25 Nov 2026:
   a trend follower wins less often but its winners are about twice its losers (replay 2023-26: 44.6% wins, PF
   1.72, payoff ~2.1), and forcing it up to 60% would wreck the profit factor. Each trade is charged COST_BPS
   (spread + fees + rollover/swap) round trip.
   Practice-quota trades never count: the desk journal only holds chosen trades.
2. **Sim vs real**: the arm's win rate in practice (simulator/backtest, written to promotion.json as
   `sim_win_rate` by a backtest run) differs from its real-price win rate by at most MAX_DRIFT (15 points; mina raised it from 8 on 25 Nov 2026).
   No sim figure yet = not passed.
3. **Owner sign-off**: mina says "sign off <arm>" to JARVIS. JARVIS only records it when gates 1 and 2 pass at
   that moment; "withdraw <arm>" takes it back. The sign-off is written to promotion.json with the numbers it
   was given on.

The engine reads promotion.json: a real-money proposal (trading/live_orders.py) is only made for a symbol whose
arm is promoted (PROMOTION_GATE=off skips this check; every proposal still needs the owner's approval as before).
"""

import json
import os
import time
from pathlib import Path

MIN_TRADES = 50
MIN_WIN_RATE = 0.60
ALT_MIN_WIN_RATE = 0.42  # trend-following route: lower win rate allowed when winners are big
MIN_PAYOFF = 1.85  # average win / average loss needed on that route
MIN_PROFIT_FACTOR = 1.35
MAX_DRIFT = 0.15  # mina, 25 Nov 2026: was 0.08
MAX_DRAWDOWN = 0.08  # of the arm's 20% sleeve, on real prices (mina's live-unlock rule)
SLEEVE_USD = 20_000.0
COST_BPS = 6.0  # round trip: spread + fees + rollover/swap, charged on every trade
FILE = "promotion.json"


def payoff_ratio(win_rate, profit_factor):
    """Implied average win / average loss from win rate and profit factor: PF x (1 - win) / win (mina's formula,
    win rate clamped to 1-99%)."""
    if win_rate is None or profit_factor is None:
        return None
    w = max(0.01, min(0.99, float(win_rate)))
    return round(float(profit_factor) * (1 - w) / w, 3)


def win_bar_ok(win_rate, payoff) -> bool:
    """mina's win-rate bar: 60%+ wins, or 42%+ wins with winners at least 1.85x the losers."""
    w = win_rate or 0.0
    return w >= MIN_WIN_RATE or (w >= ALT_MIN_WIN_RATE and payoff is not None and payoff >= MIN_PAYOFF)


def _arms():
    from .orbit_portfolio import ARMS
    return ARMS


def arm_of(symbol: str):
    s = str(symbol).upper()
    return next((a for a in _arms() if s in a.symbols), None)


def load(events_dir) -> dict:
    try:
        data = json.loads((Path(events_dir) / FILE).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def _save(events_dir, data: dict) -> None:
    path = Path(events_dir) / FILE
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def arm_stats(trades: list[dict], cost_bps: float = COST_BPS) -> dict[str, dict]:
    """Per arm: trades, win rate and profit factor after costs, from closed desk trades."""
    rows: dict[str, list[float]] = {}
    for t in trades:
        arm = arm_of(t.get("symbol", ""))
        if arm is None:
            continue
        notional = abs(float(t.get("qty") or 0) * float(t.get("entry") or 0))
        pnl = float(t.get("pnl") or 0.0) - notional * cost_bps / 10_000
        rows.setdefault(arm.key, []).append(pnl)
    out = {}
    for key, pnls in rows.items():
        wins = [p for p in pnls if p > 0]
        losses = [-p for p in pnls if p < 0]
        eq = peak = dd = 0.0
        for p in pnls:
            eq += p
            peak = max(peak, eq)
            dd = max(dd, peak - eq)
        out[key] = {"trades": len(pnls), "win_rate": round(len(wins) / len(pnls), 4),
                    "max_drawdown": round(dd / SLEEVE_USD, 4),
                    "profit_factor": round(sum(wins) / sum(losses), 3) if losses else (None if not wins else 99.0),
                    "net_pnl": round(sum(pnls), 2)}
    return out


def check(trades: list[dict], events_dir=None) -> dict:
    """Every arm's three gates, as {arm key: {...}}."""
    data = load(events_dir) if events_dir else {}
    stats = arm_stats(trades)
    sims = data.get("sim_win_rate") or {}
    signoffs = data.get("signoffs") or {}
    out = {}
    for arm in _arms():
        st = stats.get(arm.key, {"trades": 0, "win_rate": None, "profit_factor": None, "net_pnl": 0.0,
                                 "max_drawdown": 0.0})
        pf = st["profit_factor"]
        st = {**st, "payoff": payoff_ratio(st["win_rate"], pf) if st["trades"] else None}
        g1 = (st["trades"] >= MIN_TRADES and win_bar_ok(st["win_rate"], st.get("payoff"))
              and pf is not None and pf >= MIN_PROFIT_FACTOR and st.get("max_drawdown", 0.0) <= MAX_DRAWDOWN)
        sim = sims.get(arm.key)
        drift = abs(float(sim) - st["win_rate"]) if sim is not None and st["win_rate"] is not None else None
        g2 = drift is not None and drift <= MAX_DRIFT
        g3 = arm.key in signoffs
        missing = []
        if not g1:
            missing.append(f"needs {MIN_TRADES}+ real-price trades, profit factor {MIN_PROFIT_FACTOR}+, "
                           f"{MIN_WIN_RATE:.0%}+ wins (or {ALT_MIN_WIN_RATE:.0%}+ wins with payoff {MIN_PAYOFF}+), "
                           f"drawdown under {MAX_DRAWDOWN:.0%} (has {st['trades']} trades"
                           + (f", {st['win_rate']:.0%} wins, payoff {st.get('payoff')}, PF {pf}, "
                              f"drawdown {st.get('max_drawdown', 0):.0%}"
                              if st["trades"] else "") + ")")
        if not g2:
            missing.append("needs a sim win rate to compare" if sim is None else
                           f"sim {float(sim):.0%} vs real {(st['win_rate'] or 0):.0%}: drift over {MAX_DRIFT:.0%}")
        if not g3:
            missing.append("needs your sign-off (say 'sign off " + arm.key + "' to JARVIS)")
        out[arm.key] = {"name": arm.label, **st, "sim_win_rate": sim,
                        "drift": None if drift is None else round(drift, 4),
                        "gates": {"proof": g1, "sim_vs_real": g2, "signed_off": g3},
                        "promoted": g1 and g2 and g3, "missing": missing}
    return out


def sign_off(events_dir, arm_key: str, trades: list[dict], by: str = "owner", clock=time.time) -> str:
    """Record the owner's sign-off for an arm, only if its first two gates pass now. Returns JARVIS's reply."""
    key = arm_key.strip().lower()
    status = check(trades, events_dir)
    if key not in status:
        return "I don't know that arm. The arms are " + ", ".join(status) + "."
    st = status[key]
    if not (st["gates"]["proof"] and st["gates"]["sim_vs_real"]):
        return f"I can't sign off {st['name']} yet: " + "; ".join(m for m in st["missing"] if "sign-off" not in m) + "."
    data = load(events_dir)
    data.setdefault("signoffs", {})[key] = {"by": by, "at": round(clock()), "win_rate": st["win_rate"],
                                           "profit_factor": st["profit_factor"], "trades": st["trades"]}
    data["promoted"] = sorted(k for k in data["signoffs"])
    _save(events_dir, data)
    return f"Signed off. {st['name']} can now send real-money proposals for your approval."


def withdraw(events_dir, arm_key: str) -> str:
    data = load(events_dir)
    if data.get("signoffs", {}).pop(arm_key.strip().lower(), None) is None:
        return "That arm wasn't signed off."
    data["promoted"] = sorted(data["signoffs"])
    _save(events_dir, data)
    return "Sign-off withdrawn. That arm is paper only again."


def record_sim(events_dir, sim_win_rates: dict[str, float]) -> None:
    """A backtest writes each arm's practice win rate here, for gate 2."""
    data = load(events_dir)
    data.setdefault("sim_win_rate", {}).update({k: round(float(v), 4) for k, v in sim_win_rates.items()})
    _save(events_dir, data)


def promoted(events_dir, symbol: str) -> bool:
    """Has this symbol's arm been signed off (which only happens after gates 1 and 2 passed)?"""
    arm = arm_of(symbol)
    return arm is not None and arm.key in (load(events_dir).get("promoted") or [])
