"""Orbit portfolio: five strategy arms sharing one account, each in its own sleeve (mina's design).

The lesson from the ALGO COURT // ORBIT reel: a 46% win rate with 0.6 R:R loses money as one strategy, yet a
portfolio of arms that don't lose at the same time, sized by how each arm is doing, can still grow. So the edge
is measured on the MIX: per-arm and combined profit factor, drawdown, and how alike the arms move.

  arm 1  USDJPY 4H macro           USDJPY                         trend + macro rate divergence
  arm 2  FX majors SMT reversion   EURUSD GBPUSD AUDUSD           reversion + SMT sister-pair check
  arm 3  Gold PAXG reversion       GOLD (PAXG in the replay)      reversion + macro shock guard
  arm 4  US equity whale flow      SPX NDX DJI + big tech shares  reversion + option whale flow
  arm 5  Crypto bicameral          BTC ETH                        dual brain regime router + night capsule

Rules:
- sleeves of account equity: crypto 30%, US shares 30%, gold 20%, USDJPY 20%, forex majors 0% (benched 25 Nov);
  arms never share positions (no interference);
- heat: an arm's size shrinks to 0.35x while it is cold (losing, win rate < 45% over its last 25 trades) and
  grows to 1.45x while it is hot (making money, win rate >= 58%); 0.85x until it has 5 trades;
- timeframe floor: only 1h bars or slower count (1h, 4h, 1d). 5m/15m noise is refused;
- practice-quota trades never count (they never reach the desk either: the hard wall in desk.py).

ORBIT_HEAT=off turns the heat sizing off on the live paper desk (on by default).
"""
from __future__ import annotations

import json
import math
import os
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

START_CASH = 100_000.0
WINDOW = 25
ALLOWED_TF = ("1h", "4h", "1d")
DATA = Path(__file__).resolve().parent / "data" / "orbit_backtest.json"


@dataclass
class OrbitArm:
    arm_id: int
    key: str
    name: str
    label: str
    symbols: tuple[str, ...]
    timeframe: str
    core: str
    replay_symbols: tuple[str, ...] = ()
    base_weight: float = 0.20
    recent_pnl_usd: deque = field(default_factory=lambda: deque(maxlen=WINDOW))

    def dynamic_heat_multiplier(self) -> float:
        """0.35x when cold, 1.45x when hot, 1.0x otherwise; 0.85x until 5 trades are in."""
        return heat(self.recent_pnl_usd)


def heat(recent) -> float:
    n = len(recent)
    if n < 5:
        return 0.85
    net = sum(recent)
    win_rate = sum(1 for x in recent if x > 0) / n
    if net < 0 and win_rate < 0.45:
        return 0.35
    if net > 0 and win_rate >= 0.58:
        return 1.45
    return 1.0


# Sleeves (mina, 25 Nov 2026): forex majors benched (0%), their 20% moved to crypto and US shares (30% each); gold
# and USDJPY stay at 20%. On 2020-26 real daily prices the forex majors were the only arm still losing in 2023-26.
def _arms() -> list[OrbitArm]:
    return [
        OrbitArm(1, "usdjpy", "USDJPY_4H_MACRO", "USDJPY 4H macro", ("USDJPY",), "4h",
                 "Trend + macro rate divergence", ("USDJPY",), 0.20),
        OrbitArm(2, "fx", "FX_MAJOR_SMT_REV", "FX majors SMT", ("EURUSD", "GBPUSD", "AUDUSD"), "1h",
                 "Reversion + SMT sister-pair check", ("EURUSD", "GBPUSD", "AUDUSD"), 0.0),
        OrbitArm(3, "gold", "GOLD_PAXG_REV", "Gold PAXG", ("GOLD", "PAXG", "XAUUSD"), "1h",
                 "Reversion + macro shock guard", ("GOLD",), 0.20),
        OrbitArm(4, "us", "US_EQUITY_WHALE_FLOW", "US equity whale flow",
                 ("SPX", "SPY", "NDX", "QQQ", "DJI", "NVDA", "AAPL", "MSFT", "AMZN", "META", "GOOG", "GOOGL", "TSLA"),
                 "1h", "Reversion + option whale flow", ("SPX", "AAPL", "MSFT", "NVDA", "TSLA", "AMZN"), 0.30),
        OrbitArm(5, "crypto", "CRYPTO_BICAMERAL", "Crypto bicameral", ("BTC", "ETH"), "4h",
                 "Dual brain regime router + night capsule", ("BTC", "ETH"), 0.30),
    ]


ARMS = _arms()
BY_KEY = {a.key: a for a in ARMS}


def arm_for(symbol: str) -> OrbitArm | None:
    sym = str(symbol).upper()
    for arm in ARMS:
        if sym in arm.symbols:
            return arm
    return None


class OrbitPortfolioCoordinator:
    """Five arms on one account. Each arm sizes inside its own sleeve and scales by its own recent results."""

    def __init__(self, account_equity: float = START_CASH, enabled: bool = True) -> None:
        self.account_equity = account_equity
        self.enabled = enabled
        self.arms = {a.arm_id: a for a in _arms()}
        self.recorded = 0
        self.skipped_practice = 0

    @classmethod
    def from_env(cls, env=None, account_equity: float = START_CASH) -> "OrbitPortfolioCoordinator":
        env = os.environ if env is None else env
        return cls(account_equity, str(env.get("ORBIT_HEAT", "on")).lower() not in ("off", "0", "false", "no"))

    def route_arm_for_trade(self, symbol: str, timeframe: str = "1h") -> OrbitArm | None:
        if str(timeframe).lower() not in ALLOWED_TF:
            return None  # timeframe floor: no 5m/15m noise
        sym = str(symbol).upper()
        for arm in self.arms.values():
            if sym in arm.symbols:
                return arm
        return None

    def heat(self, symbol: str, timeframe: str = "1h") -> float:
        """Size multiplier for an order on this symbol (1.0 outside the orbit or when switched off)."""
        arm = self.route_arm_for_trade(symbol, timeframe)
        return arm.dynamic_heat_multiplier() if (arm and self.enabled) else 1.0

    def compute_arm_order_budget(self, symbol: str, timeframe: str = "1h", base_risk_pct: float = 0.01) -> float:
        arm = self.route_arm_for_trade(symbol, timeframe)
        if arm is None:
            return 0.0
        sleeve = self.account_equity * arm.base_weight
        return round(sleeve * base_risk_pct * (arm.dynamic_heat_multiplier() if self.enabled else 1.0), 2)

    def record_arm_result(self, symbol: str, timeframe: str, pnl_usd: float, is_practice: bool = False) -> None:
        if is_practice:
            self.skipped_practice += 1
            return
        self.account_equity += pnl_usd
        arm = self.route_arm_for_trade(symbol, timeframe)
        if arm is not None:
            arm.recent_pnl_usd.append(pnl_usd)
            self.recorded += 1

    def snapshot(self) -> dict:
        return {"enabled": self.enabled, "recorded": self.recorded, "skipped_practice": self.skipped_practice,
                "arms": {a.key: {"heat": a.dynamic_heat_multiplier(), "recent": len(a.recent_pnl_usd)}
                         for a in self.arms.values()}}


# ---------------------------------------------------------------------------------------------------------
# The book: replay real trades into one account and measure the mix.

def load_backtest(path: Path = DATA) -> dict:
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return {"trades": [], "arms": {}}


def live_trades(closed: list[dict], fee_bps: float = 1.0) -> list[dict]:
    """The paper desk's closed round trips, mapped to arms, net of the broker fee on both fills."""
    out = []
    for c in closed:
        arm = arm_for(c.get("symbol", ""))
        if arm is None or c.get("practice"):
            continue
        qty, entry, exit_ = float(c.get("qty") or 0), float(c.get("entry") or 0), float(c.get("exit") or 0)
        pnl = float(c.get("pnl") or 0) - qty * (entry + exit_) * fee_bps / 10_000
        out.append({"t": int(float(c.get("ts") or time.time())), "s": c.get("symbol"), "arm": arm.key,
                    "side": str(c.get("side") or "l")[0], "pnl": round(pnl, 2), "kind": "paper"})
    return out


def _pf(pnls: list[float]) -> float | None:
    gain = sum(p for p in pnls if p > 0)
    loss = -sum(p for p in pnls if p < 0)
    return round(gain / loss, 3) if loss else (None if not gain else 99.0)


def _stats(pnls: list[float]) -> dict:
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    avg_w = sum(wins) / len(wins) if wins else 0.0
    avg_l = -sum(losses) / len(losses) if losses else 0.0
    n = len(pnls)
    return {"trades": n, "wins": len(wins), "losses": len(losses),
            "win_rate": round(len(wins) / n, 4) if n else None,
            "net": round(sum(pnls), 2), "profit_factor": _pf(pnls),
            "avg_win": round(avg_w, 2), "avg_loss": round(avg_l, 2),
            "rr": round(avg_w / avg_l, 3) if avg_l else None}


def _max_dd(curve: list[float]) -> float:
    peak, dd = curve[0] if curve else START_CASH, 0.0
    for v in curve:
        peak = max(peak, v)
        dd = max(dd, (peak - v) / peak if peak else 0.0)
    return round(100 * dd, 2)


def _corr(a: list[float], b: list[float]) -> float | None:
    n = len(a)
    if n < 3:
        return None
    ma, mb = sum(a) / n, sum(b) / n
    va = sum((x - ma) ** 2 for x in a)
    vb = sum((y - mb) ** 2 for y in b)
    if not va or not vb:
        return None
    return round(sum((x - ma) * (y - mb) for x, y in zip(a, b)) / math.sqrt(va * vb), 3)


def simulate(trades: list[dict], use_heat: bool = True, start: float = START_CASH,
             desk_cash: float = START_CASH) -> dict:
    """Play trades in time order into one account. Each arm's trade was made by a desk with `desk_cash`; in the
    orbit it runs in a sleeve of base_weight x current equity (compounding), times its heat if switched on."""
    equity = start
    recent = {a.key: deque(maxlen=WINDOW) for a in ARMS}
    book = []
    for t in sorted(trades, key=lambda x: x["t"]):
        arm = BY_KEY.get(t.get("arm"))
        if arm is None or t.get("practice"):
            continue
        mult = heat(recent[arm.key]) if use_heat else 1.0
        scale = equity * arm.base_weight / desk_cash if t.get("kind") != "paper" else 1.0
        pnl = t["pnl"] * scale * mult
        equity += pnl
        recent[arm.key].append(pnl)
        book.append({**t, "acct_pnl": round(pnl, 2), "heat": mult, "equity": round(equity, 2)})
    return {"book": book, "end": round(equity, 2)}


def report(trades: list[dict], use_heat: bool = True, desk_cash: float = START_CASH) -> dict:
    sim = simulate(trades, use_heat, desk_cash=desk_cash)
    book = sim["book"]
    curve = [START_CASH] + [b["equity"] for b in book]
    per_arm = {}
    for arm in ARMS:
        rows = [b for b in book if b["arm"] == arm.key]
        per_arm[arm.key] = {**_stats([b["acct_pnl"] for b in rows]),
                            "unweighted_pf": _pf([1.0 if b["pnl"] > 0 else -1.0 for b in rows if b["pnl"]]),
                            "heat_now": heat(deque((b["acct_pnl"] for b in rows[-WINDOW:]), maxlen=WINDOW))
                            if use_heat else 1.0}
    # Monthly P&L per arm and combined (account %), for the heatmap and the correlation between arms.
    months: dict[str, dict] = {}
    eq_at_month_start: dict[str, float] = {}
    prev = START_CASH
    for b in book:
        m = time.strftime("%Y-%m", time.gmtime(b["t"]))
        if m not in eq_at_month_start:
            eq_at_month_start[m] = prev
        row = months.setdefault(m, {a.key: 0.0 for a in ARMS})
        row[b["arm"]] += b["acct_pnl"]
        prev = b["equity"]
    keys = sorted(months)
    heatmap = {m: round(100 * sum(months[m].values()) / eq_at_month_start[m], 2) for m in keys}
    series = {a.key: [months[m][a.key] for m in keys] for a in ARMS}
    active = {a.key for a in ARMS if sum(1 for v in series[a.key] if v) >= 6}  # too few months: no correlation
    corr = {a.key: {b.key: (1.0 if a.key == b.key else _corr(series[a.key], series[b.key])
                            if a.key in active and b.key in active else None) for b in ARMS} for a in ARMS}
    pairs = [corr[a.key][b.key] for i, a in enumerate(ARMS) for b in ARMS[i + 1:] if corr[a.key][b.key] is not None]
    combined = {**_stats([b["acct_pnl"] for b in book]),
                "unweighted_pf": _pf([1.0 if b["pnl"] > 0 else -1.0 for b in book if b["pnl"]]),
                "max_drawdown_pct": _max_dd(curve), "start": START_CASH, "end": sim["end"],
                "return_pct": round(100 * (sim["end"] / START_CASH - 1), 2),
                "avg_correlation": round(sum(pairs) / len(pairs), 3) if pairs else None}
    return {"combined": combined, "arms": per_arm, "heatmap": heatmap, "correlation": corr, "book": book}


def floating(data: dict) -> dict:
    """Open trades marked to market, not just closed ones: each arm's desk equity at weekly marks, put in its 20%
    sleeve (no heat). Also how often 3+ arms leaned the same way at once (same-direction risk)."""
    desk_cash = float(data.get("desk_cash_per_arm") or START_CASH)
    marks = {k: v.get("marks") or [] for k, v in (data.get("arms") or {}).items() if k in BY_KEY}
    times = sorted({m[0] for ms in marks.values() for m in ms})
    if not times:
        return {"max_drawdown_pct": None, "curve": [], "same_direction_share": None, "same_direction_now": None}
    idx = {k: 0 for k in marks}
    curve, crowded, now_lean = [], 0, {}
    for t in times:
        eq, leans = START_CASH, []
        for k, ms in marks.items():
            while idx[k] + 1 < len(ms) and ms[idx[k] + 1][0] <= t:
                idx[k] += 1
            if ms and ms[idx[k]][0] <= t:
                eq += BY_KEY[k].base_weight * (ms[idx[k]][1] - desk_cash)
                leans.append(ms[idx[k]][2])
                now_lean[k] = ms[idx[k]][2]
        if max(leans.count(1), leans.count(-1)) >= 3:
            crowded += 1
        curve.append([t, round(eq, 2)])
    longs, shorts = list(now_lean.values()).count(1), list(now_lean.values()).count(-1)
    return {"max_drawdown_pct": _max_dd([c[1] for c in curve]), "curve": curve[:: max(1, len(curve) // 400)],
            "same_direction_share": round(crowded / len(times), 4), "lean_now": now_lean,
            "same_direction_now": "long" if longs >= 3 else "short" if shorts >= 3 else None}


def recent(book: list[dict], days: int = 365) -> dict:
    """The last year on its own: arms scored on recent prices, so we don't just pick past winners."""
    if not book:
        return {}
    cut = book[-1]["t"] - days * 86_400
    rows = [b for b in book if b["t"] >= cut]
    return {"days": days, "combined": _stats([b["acct_pnl"] for b in rows]),
            "arms": {a.key: _stats([b["acct_pnl"] for b in rows if b["arm"] == a.key]) for a in ARMS}}


def dashboard(closed_live: list[dict] | None = None, data: dict | None = None) -> dict:
    """Everything the /orbit page shows: replay book, both sizing modes, arms, heatmap, correlation."""
    data = load_backtest() if data is None else data
    trades = [{**t, "kind": "backtest"} for t in data.get("trades", [])] + live_trades(closed_live or [])
    desk_cash = float(data.get("desk_cash_per_arm") or START_CASH)
    on = report(trades, True, desk_cash)
    off = report(trades, False, desk_cash)
    book = on.pop("book")
    book_off = off.pop("book")
    ranked = sorted(on["arms"].items(), key=lambda kv: -kv[1]["net"])
    on["recent"] = recent(book)
    return {
        "arms": [{"id": a.arm_id, "key": a.key, "name": a.name, "label": a.label, "timeframe": a.timeframe,
                  "core": a.core, "symbols": list(a.symbols), "weight": a.base_weight,
                  "replay_symbols": list((data.get("arms") or {}).get(a.key, {}).get("symbols", a.replay_symbols))}
                 for a in ARMS],
        "with_heat": on, "without_heat": off, "floating": floating(data),
        "top": [k for k, _ in ranked],
        "points": [[b["t"], b["arm"], round(b["acct_pnl"], 2), b["equity"], b["s"], b.get("kind", "backtest"), b["heat"]]
                   for b in book],
        "points_off": [[b["t"], b["arm"], round(b["acct_pnl"], 2), b["equity"], b["s"], b.get("kind", "backtest"), 1.0]
                       for b in book_off],
        "source": {"backtest_trades": len(data.get("trades", [])), "paper_trades": len(trades) - len(data.get("trades", [])),
                   "bar": data.get("bar", "1d"), "made": data.get("made"), "cost_bps_per_fill": data.get("cost_bps_per_fill"),
                   "practice_trades_included": False,
                   "replay_arms": {k: {kk: vv for kk, vv in v.items() if kk != "marks"} for k, v in (data.get("arms") or {}).items()},
                   "first": book[0]["t"] if book else None, "last": book[-1]["t"] if book else None},
        "rules": {"sleeve": {a.key: a.base_weight for a in ARMS}, "cold": 0.35, "hot": 1.45, "warmup": 0.85, "window": WINDOW,
                  "timeframes": list(ALLOWED_TF)},
    }


def chosen_trades_csv(dash: dict) -> str:
    """mina's export format: date,arm,symbol,pnl_usd,is_practice (practice rows are never in the book)."""
    lines = ["date,arm,symbol,pnl_usd,is_practice"]
    names = {a.key: a.name for a in ARMS}
    for t, arm, pnl, _eq, sym, _kind, _h in dash["points"]:
        lines.append(f"{time.strftime('%Y-%m-%d', time.gmtime(t))},{names.get(arm, arm)},{sym},{pnl:.2f},0")
    return "\n".join(lines) + "\n"
