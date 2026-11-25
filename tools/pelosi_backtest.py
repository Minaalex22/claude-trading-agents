"""Replay the Pelosi trader on real daily prices and save the result for the /pelosi page.

    python3 tools/pelosi_backtest.py --prices DIR --hsw all_transactions.json --capitol congress_trades_latest.csv

  --prices   folder of <TICKER>.csv files with columns date,adjusted_close (must include SPY). The 2026-11-25 run
             used zandy700/supply-chain-shock-map data/prices (Yahoo adjusted closes, 59 stocks, 2020-01 to 2026-11).
  --hsw      house-stock-watcher-data data/all_transactions.json (House PTR rows; announce date = legal deadline).
  --capitol  capitol-stock-watch-open-data congress_trades_latest.csv (real filing dates, 2026).

Entries are on the first trading day AFTER the announce date, never the trade date. Writes
trading/data/pelosi_backtest.json and prints every hold length tried.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from trading import pelosi  # noqa: E402


def load_prices(folder: Path) -> dict:
    out = {}
    for f in folder.glob("*.csv"):
        rows = list(csv.DictReader(f.open()))
        if rows and "adjusted_close" in rows[0]:
            out[f.stem.upper()] = ([r["date"] for r in rows], [float(r["adjusted_close"]) for r in rows])
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--prices", required=True, type=Path)
    ap.add_argument("--hsw", required=True, type=Path)
    ap.add_argument("--capitol", required=True, type=Path)
    ap.add_argument("--hold", type=int, default=60)
    ap.add_argument("--out", type=Path, default=pelosi.DATA)
    a = ap.parse_args(argv)
    prices = load_prices(a.prices)
    trades = pelosi.merge(pelosi.from_capitol_csv(a.capitol.read_text(encoding="utf-8")),
                          pelosi.from_hsw_json(json.loads(a.hsw.read_text(encoding="utf-8"))))
    first, last = prices["SPY"][0][0], prices["SPY"][0][-1]
    print(f"{len(trades)} disclosed trades, {len(prices)} price files, {first} to {last}")
    for hold in (20, 40, 60, 120, 250):
        for arm in ("pelosi", "congress"):
            s = pelosi.backtest(trades, prices, arm=arm, hold=hold)["stats"]
            print(f"hold {hold:3d} {arm:9s} {json.dumps(s)}")
    arms, keep = [], []
    for arm, label in (("pelosi", "Pelosi (every buy)"), ("congress", "Congress buys $250,001+")):
        r = pelosi.backtest(trades, prices, arm=arm, hold=a.hold)
        arms.append({"arm": arm, "label": label, **r["stats"]})
        keep += r["trades"]
        ran = [t for t in r["trades"] if (t["move_trade_to_announce"] or 0) > 0.05]
        flat = [t for t in r["trades"] if t["move_trade_to_announce"] is not None and t["move_trade_to_announce"] <= 0.05]
        arms.append({"arm": arm + "-ranup", "label": f"{label}: price up 5%+ between trade and announce",
                     **pelosi.stats(ran)})
        arms.append({"arm": arm + "-flat", "label": f"{label}: price flat or down before announce",
                     **pelosi.stats(flat)})
    for arm, label in (("pelosi", "Pelosi, before guards"), ("congress", "Congress $50,001+, before guards")):
        r = pelosi.backtest(trades, prices, arm=arm, hold=a.hold, chase_cap=0, trend_gate=False, congress_min=50_001)
        arms.append({"arm": arm + "-before", "label": label, **r["stats"]})
    out = {"period": f"{first} to {last}", "hold_days": a.hold, "arms": arms,
           "trades": sorted(keep, key=lambda t: t["entry"], reverse=True)[:400],
           "note": ("Bought on the first trading day after each announcement, never the trade date. Before 2026 the "
                    "announce date is the legal deadline (30 days after notification, at most 45 after the trade), "
                    "because the free House data gives the notification date, not the filing date. Prices: Yahoo "
                    "adjusted daily closes for 59 large US stocks, so many smaller tickers are not in the replay. "
                    "Guards on: no buy 15%+ above the trade-date price, only above the 200-day average. "
                    "Most of the profit is the 2020-2026 bull market: compare with the S&P 500 columns.")}
    a.out.write_text(json.dumps(out, indent=1) + "\n", encoding="utf-8")
    for x in arms:
        print(x)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
