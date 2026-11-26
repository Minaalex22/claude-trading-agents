"""Noise band backtest: replay real 30-minute bars through trading/noise_band.py, per market.

  python tools/noise_band_backtest.py --dir DIR      # DIR holds SPX.csv, GOLD.csv, EURUSD.csv, BTC.csv

Each CSV: t_end,open,high,low,close,volume (t_end = bar end, unix seconds UTC). The data used on 25 Nov 2026:
  SPX, GOLD (XAUUSD), EURUSD: FTMO MT5 1-minute mid prices, 2 Jan 2025 - 15 May 2026, resampled to 30 minutes
    (github.com/Gianniskas/mt5-m1-bars-us500-eurusd-xauusd; its clock is New York + 7h, not UTC as its README says)
  BTC: Binance BTCUSDT 1-minute klines, Jan 2022 - Nov 2026, resampled to 30 minutes
    (github.com/parshant231234-eng/BTCUSDT-Historical-Data)
Costs: two runs. "desk" charges the desk's own 3 bps per fill (1 fee + 2 slippage, as tools/orbit_backtest.py);
"broker" charges each market's real half-spread + commission per fill (BROKER_BPS).
"""
import argparse
import csv
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from trading import noise_band  # noqa: E402

DESK_BPS = 3.0
BROKER_BPS = {"SPX": 0.6, "GOLD": 1.0, "EURUSD": 0.5, "BTC": 5.0}  # half-spread + commission per fill


def load(path: str) -> list[tuple[float, float]]:
    return [(float(r["t_end"]), float(r["close"])) for r in csv.DictReader(open(path))]


def by_year(trades: list[dict]) -> dict:
    out = {}
    for t in trades:
        y = time.strftime("%Y", time.gmtime(t["t"]))
        out.setdefault(y, []).append(t)
    return {y: noise_band.stats(v) for y, v in sorted(out.items())}


def main(argv=None) -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--dir", required=True)
    p.add_argument("--out", default="")
    p.add_argument("--unsized", action="store_true", help="1x position, no volatility targeting")
    a = p.parse_args(argv)
    result = {}
    for sym in ("SPX", "GOLD", "EURUSD", "BTC"):
        path = os.path.join(a.dir, f"{sym}.csv")
        if not os.path.exists(path):
            continue
        bars = load(path)
        row = {"bars": len(bars), "from": time.strftime("%Y-%m-%d", time.gmtime(bars[0][0])),
               "to": time.strftime("%Y-%m-%d", time.gmtime(bars[-1][0]))}
        for name, bps in (("desk", DESK_BPS), ("broker", BROKER_BPS[sym])):
            r = noise_band.replay(bars, sym, cost_bps=bps, sized=not a.unsized)
            row[name] = {k: r[k] for k in ("n", "win_rate", "profit_factor", "payoff", "net", "end_equity", "max_dd")}
            row[name]["years"] = by_year(r["trades"])
        result[sym] = row
        d, b = row["desk"], row["broker"]
        print(f"{sym:7s} {row['from']}..{row['to']}  trades {d['n']:4d}  wins {d['win_rate']:.1%}  "
              f"PF desk {d['profit_factor']}  broker {b['profit_factor']}  net desk {d['net']:>10,.0f}  "
              f"broker {b['net']:>10,.0f}  maxDD {d['max_dd']:.1%}/{b['max_dd']:.1%}", file=sys.stderr)
    if a.out:
        with open(a.out, "w") as f:
            json.dump(result, f, indent=1)


if __name__ == "__main__":
    main()


def desk_replay(bars: dict, noise: str, agents: int = 100) -> dict:
    """The whole paper desk (agents, gates, risk) on the same 30-minute bars, with NOISE_BAND on or off."""
    import io
    sys.path.insert(0, os.path.dirname(__file__))
    from orbit_backtest import _MonthlyRearm, realized
    from trading import backtest, engine
    market = backtest.HistoricalMarket(bars, max_gap=4 * 86_400)
    out = io.StringIO()
    cost = 1.0 + backtest.SLIPPAGE_BPS
    env = {"MARKET": "history", "FEE_BPS": "1.0", "TICKS": str(len(market)), "TICK_SECONDS": "0",
           "SYMBOLS": ",".join(bars), "MEMORY": "off", "COACH": "off", "READING_ROOM_CLAUDE": "off",
           "AGENT_COUNT": str(agents), "SEED": "42", "ORBIT_HEAT": "off", "NOISE_BAND": noise,
           **{k: v for k, v in os.environ.items() if k.startswith("NOISE_BAND_")}}
    with _MonthlyRearm():
        desk = engine.run(env, stream=out, market=market)
    orders, pending = [], []
    for line in out.getvalue().splitlines():
        if '"type":"order"' in line:
            pending.append(json.loads(line))
        elif '"type":"tick"' in line:
            at = int(market.times[min(json.loads(line)["tick"], len(market.times)) - 1])
            orders += [{"t": at, "symbol": e["symbol"], "qty": e["qty"], "price": e["price"]} for e in pending]
            pending = []
    trades = realized(orders, cost)
    eq = desk.broker.equity(desk.last_prices)
    return {**noise_band.stats(trades), "orders": len(orders), "end_equity": round(eq, 2),
            "per_symbol": {s: noise_band.stats([t for t in trades if t["s"] == s]) for s in bars}}
