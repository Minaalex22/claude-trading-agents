"""Backtest: replay real price history through the same agents, desk and risk limits, with costs.

The simulated market can't tell whether a strategy would make money (its drift is easy to ride). This
replays real bars instead:

  python -m trading.backtest --interval 1h --range 730d            # fetch Yahoo history, run, report
  python -m trading.backtest --file history.json                   # {"bars": {"AAPL": [[time, close], ...]}}

Every shard setting the engine accepts works here (AGENT_COUNT, READING_ROOM, ...). There is no news in a
backtest: past headlines aren't available, so views come from the charts.

The report splits the run in two halves. The first half is where the agents warm up and learn; the second
half is the honest test (walk-forward): did what they learned keep working on prices they hadn't seen?
Each half is compared with simply buying every symbol and holding it.

Costs: the paper broker's 1 bp fee plus SLIPPAGE_BPS on every fill.
"""

import argparse
import io
import json
import math
import os
import sys
import time
from pathlib import Path

SLIPPAGE_BPS = 2.0
YEAR_SECONDS = 365 * 86_400


def normalize_bar(row: dict) -> dict:
    """One replay bar with lowercase keys (Open/High/Low/Close/Datetime -> open/high/low/close/timestamp) and a
    timezone-aware UTC timestamp, whatever the CSV or parquet export used."""
    from datetime import datetime, timezone
    bar = {str(k).strip().lower(): v for k, v in row.items()}
    for alias in ("datetime", "date", "time", "ts"):
        if "timestamp" not in bar and alias in bar:
            bar["timestamp"] = bar.pop(alias)
    t = bar.get("timestamp")
    if isinstance(t, (int, float)):
        t = datetime.fromtimestamp(t / 1000 if t > 1e11 else t, timezone.utc)
    elif isinstance(t, str) and t:
        t = datetime.fromisoformat(t.strip().replace("Z", "+00:00"))
    if isinstance(t, datetime):
        bar["timestamp"] = t if t.tzinfo else t.replace(tzinfo=timezone.utc)
    for k in ("open", "high", "low", "close", "volume"):
        if k in bar and bar[k] not in (None, ""):
            bar[k] = float(bar[k])
    return bar


class HistoricalMarket:
    """Drop-in for SimulatedMarket: tick() returns the next bar's closes. `drift` is empty (no hidden truth)."""

    def __init__(self, bars: dict[str, list[tuple[float, float]]], max_gap: float = 3 * 3600):
        # Bars from different markets don't share timestamps (stocks at :30, crypto at :00), so each bar time
        # of the slowest-trading symbols takes every symbol's latest close at or before it, if recent enough.
        series = {s: sorted(v) for s, v in bars.items() if v}
        counts = {s: len(v) for s, v in series.items()}
        base = min(counts, key=counts.get) if counts else None
        self.times, self.rows = [], []
        idx = {s: 0 for s in series}
        for t, _ in series.get(base, []):
            row = {}
            for s, v in series.items():
                while idx[s] + 1 < len(v) and v[idx[s] + 1][0] <= t:
                    idx[s] += 1
                bt, close = v[idx[s]]
                if bt <= t and t - bt <= max_gap:
                    row[s] = close
            if len(row) == len(series):
                self.times.append(t)
                self.rows.append(row)
        self.i = 0
        self.prices: dict[str, float] = dict(self.rows[0]) if self.rows else {}
        self.drift: dict[str, float] = {}

    def now(self) -> float | None:
        """The time of the bar last handed out by tick()."""
        return self.times[min(max(self.i - 1, 0), len(self.times) - 1)] if self.times else None

    def __len__(self) -> int:
        return len(self.rows)

    def tick(self) -> dict[str, float]:
        self.prices = dict(self.rows[min(self.i, len(self.rows) - 1)])
        self.i += 1
        return dict(self.prices)


def fetch_history(symbols: list[str], interval: str = "1h", range_: str = "730d", get=None) -> dict:
    """Yahoo chart history for every symbol (crypto as SYMBOL-USD): {symbol: [(unix time, close), ...]}."""
    from .live_market import CRYPTO, _get_json

    get = get or _get_json
    out = {}
    for s in symbols:
        ticker = CRYPTO.get(s, s)
        data = get(f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?interval={interval}&range={range_}")
        r = data["chart"]["result"][0]
        closes = r["indicators"]["quote"][0]["close"]
        out[s] = [(float(t), float(c)) for t, c in zip(r["timestamp"], closes) if c is not None and c > 0]
    return out


def metrics(times: list[float], equity: list[float]) -> dict:
    """Return, annualised Sharpe and Sortino from bar returns, max drawdown and Calmar."""
    if len(equity) < 2:
        return {}
    rets = [b / a - 1 for a, b in zip(equity, equity[1:]) if a > 0]
    years = max((times[-1] - times[0]) / YEAR_SECONDS, 1e-9)
    per_year = len(rets) / years
    mean = sum(rets) / len(rets)
    sd = math.sqrt(sum((r - mean) ** 2 for r in rets) / len(rets)) or 1e-12
    down = math.sqrt(sum(min(r, 0) ** 2 for r in rets) / len(rets)) or 1e-12
    peak, mdd = equity[0], 0.0
    for e in equity:
        peak = max(peak, e)
        mdd = max(mdd, (peak - e) / peak)
    total = equity[-1] / equity[0] - 1
    annual = (1 + total) ** (1 / years) - 1 if total > -1 else -1.0
    return {"return_pct": round(100 * total, 2), "annual_pct": round(100 * annual, 2),
            "sharpe": round(mean / sd * math.sqrt(per_year), 2), "sortino": round(mean / down * math.sqrt(per_year), 2),
            "max_drawdown_pct": round(100 * mdd, 2), "calmar": round(annual / mdd, 2) if mdd else None,
            "days": round(years * 365, 1)}


def buy_and_hold(market: HistoricalMarket, start: int, end: int) -> list[float]:
    first = market.rows[start]
    return [sum(row[s] / first[s] for s in first) / len(first) for row in market.rows[start:end]]


def run(bars: dict, env: dict | None = None) -> dict:
    from . import engine

    market = HistoricalMarket(bars)
    if len(market) < 200:
        raise SystemExit(f"only {len(market)} bars line up across the symbols; need at least 200")
    out = io.StringIO()
    env = dict(env or {})
    env.update({"MARKET": "history", "FEE_BPS": str(1.0 + SLIPPAGE_BPS), "TICKS": str(len(market)), "TICK_SECONDS": "0",
                "SYMBOLS": ",".join(bars), "MEMORY": "off", "COACH": "off", "READING_ROOM_CLAUDE": "off"})
    env.setdefault("AGENT_COUNT", "400")
    desk = engine.run(env, stream=out, market=market)
    ticks = [json.loads(line) for line in out.getvalue().splitlines() if '"type":"tick"' in line]
    equity = [t["equity"] for t in ticks]
    times = market.times[:len(equity)]
    half = len(equity) // 2
    report = {"bars": len(equity), "symbols": list(bars), "agents": len(desk.agents),
              "costs_paid": round(desk.broker.fees_paid, 2), "cost_bps_per_fill": 1.0 + SLIPPAGE_BPS,
              "whole": metrics(times, equity),
              "learning_half": metrics(times[:half], equity[:half]),
              "test_half": metrics(times[half:], equity[half:]),
              "buy_and_hold_test_half": metrics(times[half:], buy_and_hold(market, half, len(equity))),
              "stopped": ticks[-1].get("stopped") if ticks else None,
              "passive_limits": desk.passive.status(), "vol_sizing_shrunk": desk.sizing["shrunk"]}
    from . import strategy_lab
    closed = [{"symbol": sym, "qty": t["qty"], "entry": t["entry"], "pnl": t["qty"] * t["entry"] * t["move"]}
              for sym, rows in desk.journal.since(0).items() for t in rows]
    report["arms"] = strategy_lab.arm_stats(closed)
    if env.get("PROMOTION_DIR"):  # gate 2 of the live-promotion check: each arm's win rate in practice
        strategy_lab.record_sim(env["PROMOTION_DIR"], {k: v["win_rate"] for k, v in report["arms"].items()})
    return report


def main(argv=None) -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--symbols", default=os.environ.get("SYMBOLS", "AAPL,MSFT,NVDA,AMZN,GOOG,TSLA,META,AMD,BTC,ETH"))
    p.add_argument("--interval", default="1h")
    p.add_argument("--range", dest="range_", default="730d")
    p.add_argument("--file", help="saved history JSON instead of fetching")
    p.add_argument("--save", help="write the fetched history to this file")
    a = p.parse_args(argv)
    if a.file:
        bars = {s: [tuple(x) for x in v] for s, v in json.loads(Path(a.file).read_text())["bars"].items()}
    else:
        started = time.time()
        bars = fetch_history([s.strip() for s in a.symbols.split(",") if s.strip()], a.interval, a.range_)
        print(f"fetched {sum(map(len, bars.values()))} bars in {time.time() - started:.1f}s", file=sys.stderr)
        if a.save:
            Path(a.save).write_text(json.dumps({"bars": bars}))
    print(json.dumps(run(bars, dict(os.environ)), indent=2))


if __name__ == "__main__":
    main()
