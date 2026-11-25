"""Orbit backtest: replay ~7 years of real daily prices through our desk and save its closed trades.

  python tools/orbit_backtest.py                  # fetch, run, write trading/data/orbit_backtest.json
  python tools/orbit_backtest.py --raw DIR        # use CSVs already downloaded into DIR

Prices come from free public CSVs on raw.githubusercontent.com (the only free multi-year source reachable
from the cloud): coinmetrics PriceUSD (BTC, ETH, PAXG as gold), datasets/exchange-rates (FX) and daily closes for
SPY and the big shares. The run uses the same agents, desk, hooks and risk limits as the live paper desk.

Only desk orders count: practice-quota trades never become desk orders (the hard wall in desk.py), so they
are not in this file. Costs are included: every trade's P&L is net of the broker fee plus slippage on both
fills (backtest.SLIPPAGE_BPS).
"""
import argparse
import csv
import io
import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from trading import backtest, engine  # noqa: E402

RAW = "https://raw.githubusercontent.com/"
SOURCES = {
    "btc.csv": "coinmetrics/data/master/csv/btc.csv",
    "eth.csv": "coinmetrics/data/master/csv/eth.csv",
    "paxg.csv": "coinmetrics/data/master/csv/paxg.csv",
    "fx.csv": "datasets/exchange-rates/main/data/daily.csv",
    **{f"{s}.csv": f"zandy700/supply-chain-shock-map/main/data/prices/{s}.csv"
       for s in ("SPY", "AAPL", "MSFT", "NVDA", "TSLA", "AMZN")},
}
# Our symbol -> (file, how to read it). FX file is "units of currency per US dollar".
FX = {"EURUSD": ("Euro", True), "GBPUSD": ("United Kingdom", True), "AUDUSD": ("Australia", True),
      "USDJPY": ("Japan", False)}
START = "2020-01-02"
OUT = Path(__file__).resolve().parent.parent / "trading" / "data" / "orbit_backtest.json"


def day(s: str) -> float:
    return datetime.strptime(s[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp()


def fetch(raw_dir: Path) -> None:
    raw_dir.mkdir(parents=True, exist_ok=True)
    for name, path in SOURCES.items():
        if not (raw_dir / name).exists():
            with urllib.request.urlopen(RAW + path, timeout=60) as r:
                (raw_dir / name).write_bytes(r.read())


def load(raw_dir: Path) -> dict[str, list[tuple[float, float]]]:
    bars: dict[str, list] = {}
    for sym, f in (("BTC", "btc.csv"), ("ETH", "eth.csv"), ("GOLD", "paxg.csv")):
        rows = csv.DictReader(io.StringIO((raw_dir / f).read_text()))
        bars[sym] = [(day(r["time"]), float(r.get("PriceUSD") or r["ReferenceRateUSD"])) for r in rows
                     if (r.get("PriceUSD") or r.get("ReferenceRateUSD")) and r["time"][:10] >= START]
    fx = {k: [] for k in FX}
    want = {country: (sym, inv) for sym, (country, inv) in FX.items()}
    for r in csv.DictReader(io.StringIO((raw_dir / "fx.csv").read_text())):
        if r["Country"] in want and r["Date"] >= START and r["Exchange rate"]:
            sym, inv = want[r["Country"]]
            v = float(r["Exchange rate"])
            if v > 0:
                fx[sym].append((day(r["Date"]), 1 / v if inv else v))
    bars.update(fx)
    for sym, f in (("SPX", "SPY.csv"), ("AAPL", "AAPL.csv"), ("MSFT", "MSFT.csv"), ("NVDA", "NVDA.csv"),
                   ("TSLA", "TSLA.csv"), ("AMZN", "AMZN.csv")):
        rows = csv.DictReader(io.StringIO((raw_dir / f).read_text()))
        bars[sym] = [(day(r["date"]), float(r["adjusted_close"])) for r in rows
                     if r["date"] >= START and r["adjusted_close"]]
    return bars


class _MonthlyRearm:
    """Replay only: re-arm the 10% drawdown halt at the start of each month, as a risk manager reviewing the
    book would (live, the halt stays until a person clears it). Without it one bad month ends a 7-year replay."""

    def __enter__(self):
        from trading import risk
        self.risk, self.orig = risk, risk.RiskManager.update
        orig = self.orig

        def update(rm, prices, now=None):
            month = time.strftime("%Y-%m", time.gmtime(now)) if now else None
            if month and getattr(rm, "_orbit_month", None) != month:
                rm._orbit_month = month
                if rm.halted:
                    rm.halted, rm.peak_equity = False, rm.broker.equity(prices)
                    rm._orbit_rearms = getattr(rm, "_orbit_rearms", 0) + 1
            return orig(rm, prices, now)

        risk.RiskManager.update = update
        return self

    def __exit__(self, *exc):
        self.risk.RiskManager.update = self.orig


def realized(orders: list[dict], cost_bps: float) -> list[dict]:
    """One trade per fill that shrinks or flips a position: the P&L it locks in, net of both fills' costs."""
    pos: dict[str, float] = {}
    avg: dict[str, float] = {}
    out = []
    for o in orders:
        s, q, px = o["symbol"], o["qty"], o["price"]
        before = pos.get(s, 0.0)
        after = before + q
        if before and (after == 0 or (after > 0) != (before > 0) or abs(after) < abs(before)):
            closed = abs(before) if (after == 0 or (after > 0) != (before > 0)) else abs(q)
            side = 1 if before > 0 else -1
            gross = closed * (px - avg[s]) * side
            cost = closed * (avg[s] + px) * cost_bps / 10_000
            out.append({"t": o["t"], "s": s, "side": "l" if side > 0 else "s",
                        "pnl": round(gross - cost, 2), "notional": round(closed * avg[s], 2),
                        "move": round((px / avg[s] - 1) * side, 5)})
        if after and (not before or (after > 0) != (before > 0)):
            avg[s] = px
        elif after and abs(after) > abs(before):
            avg[s] = (avg[s] * abs(before) + px * abs(q)) / abs(after)
        elif not after:
            avg.pop(s, None)
        pos[s] = after
    return out


def run_arm(bars: dict, agents: int, cost_bps: float) -> tuple[list[dict], dict]:
    market = backtest.HistoricalMarket(bars, max_gap=5 * 86_400)  # daily bars; weekends and holidays allowed
    out = io.StringIO()
    env = {"MARKET": "history", "FEE_BPS": str(cost_bps), "TICKS": str(len(market)), "TICK_SECONDS": "0",
           "SYMBOLS": ",".join(bars), "MEMORY": "off", "COACH": "off", "READING_ROOM_CLAUDE": "off",
           "AGENT_COUNT": str(agents), "SEED": "42",
           "ORBIT_HEAT": "off",
           **{k: v for k, v in os.environ.items() if k.startswith(("TREND_ANCHOR", "RSI2_SHARE"))}}  # heat is applied once, in orbit_portfolio.simulate, not twice
    with _MonthlyRearm():
        desk = engine.run(env, stream=out, market=market)
    orders, pending, marks, pos = [], [], [], {}
    for line in out.getvalue().splitlines():
        if '"type":"order"' in line:
            pending.append(json.loads(line))
        elif '"type":"tick"' in line:
            ev = json.loads(line)
            tick = ev["tick"]
            at = int(market.times[min(tick, len(market.times)) - 1])
            for e in pending:
                orders.append({"t": at, "symbol": e["symbol"], "qty": e["qty"], "price": e["price"]})
                pos[e["symbol"]] = pos.get(e["symbol"], 0.0) + e["qty"]
            pending = []
            if tick % 5 == 0:  # weekly-ish marks: floating equity (open trades marked to market) and direction
                lean = sum((q > 1e-9) - (q < -1e-9) for q in pos.values())
                marks.append([at, round(ev["equity"], 2), (lean > 0) - (lean < 0)])
    info = {"bars": len(market), "first_bar": int(market.times[0]), "last_bar": int(market.times[-1]),
            "orders": len(orders), "rearms": getattr(desk.risk, "_orbit_rearms", 0),
            "end_equity": round(desk.broker.equity(desk.last_prices), 2), "marks": marks}
    return realized(orders, cost_bps), info


def run(bars: dict, agents: int = 400) -> dict:
    """Each orbit arm is its own desk on its own symbols (no interference), as trading/orbit_portfolio.py runs them."""
    from trading.orbit_portfolio import ARMS

    cost_bps = 1.0 + backtest.SLIPPAGE_BPS
    trades, arms = [], {}
    for arm in ARMS:
        have = {s: bars[s] for s in arm.replay_symbols if s in bars}
        if not have:
            continue
        got, info = run_arm(have, agents, cost_bps)
        for t in got:
            t["arm"] = arm.key
        trades += got
        arms[arm.key] = {**info, "symbols": list(have), "trades": len(got)}
        print(f"  {arm.key}: {len(got)} trades, {info['orders']} orders", file=sys.stderr)
    trades.sort(key=lambda t: t["t"])
    return {"source": "tools/orbit_backtest.py", "made": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            "kind": "backtest", "bar": "1d", "desk_cash_per_arm": 100_000.0, "cost_bps_per_fill": cost_bps,
            "agents_per_arm": agents, "practice_trades_included": False, "arms": arms, "trades": trades}


def main(argv=None) -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--raw", default=os.path.join(os.environ.get("TMPDIR", "/tmp"), "orbit-raw"))
    p.add_argument("--agents", type=int, default=400)
    p.add_argument("--out", default=str(OUT))
    a = p.parse_args(argv)
    raw = Path(a.raw)
    fetch(raw)
    started = time.time()
    result = run(load(raw), a.agents)
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(result, separators=(",", ":")))
    print(f"{len(result['trades'])} trades in {time.time() - started:.0f}s", file=sys.stderr)


if __name__ == "__main__":
    main()
