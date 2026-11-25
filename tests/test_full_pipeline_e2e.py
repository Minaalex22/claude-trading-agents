"""One fast regression suite for the rules that must never break (mina, 25 Nov 2026).

`pytest -q tests/test_full_pipeline_e2e.py` after any change to desk.py, agents.py, hooks.py or risk.py:
real-price fills (never Heikin Ashi), the practice-quota wall, 0DTE put polarity, the rollover blackout, the net-USD
guard and the live-unlock gates. Matched to the real module names in this repo.
"""
import io
import json
import unittest
from datetime import datetime, timezone

from trading import correlation_guard, engine, execution_integrity, option_flows, ops_guard, strategy_lab


class FullPipelineE2E(unittest.TestCase):
    def test_heikin_ashi_never_sets_the_fill(self):
        bars = [{"open": 100.0, "high": 105.0, "low": 99.0, "close": 104.0},
                {"open": 104.5, "high": 106.0, "low": 103.5, "close": 105.0}]
        b = execution_integrity.heikin_ashi(bars)[1]
        self.assertNotEqual(b.ha_open, b.real_open)  # the smoothed open is somewhere else ...
        fill = execution_integrity.fill_price(+1, b.real_open)
        self.assertAlmostEqual(fill, 104.5 * (1 + execution_integrity.friction()))  # ... the fill is the real open

    def test_put_bought_to_open_is_bearish(self):
        p = option_flows.FlowPrint(underlying="SPX", option_type="PUT", action="BUY_TO_OPEN", premium=4_200_000,
                                   dte=0, strike=5920, volume=950, open_interest=200, spot=5921, key_level=5920)
        d = option_flows.decode(p)
        self.assertEqual(d.bias, "BEARISH")
        self.assertLess(d.score, -0.8)

    def test_rollover_blackout(self):
        now = datetime(2026, 11, 24, 22, 15, tzinfo=timezone.utc)  # 09:15 Melbourne: NY close to Asia open
        h = ops_guard.TickHealth("EURUSD", "1h", now, bid=1.0800, ask=1.0802, atr_price=0.0025)
        ok, why = ops_guard.check_feed_and_rollover_health(h, now_utc=now)
        self.assertFalse(ok)
        self.assertTrue(why.startswith("rollover_spread_blackout_utc"))

    def test_net_usd_guard_blocks_stacked_dollar_shorts(self):
        held = [{"symbol": "EURUSD", "side": "BUY", "risk_pct": 0.01}, {"symbol": "PAXG", "side": "BUY", "risk_pct": 0.01},
                {"symbol": "BTC", "side": "BUY", "risk_pct": 0.01}]
        ok, why = correlation_guard.check({"symbol": "AUDUSD", "side": "BUY", "risk_pct": 0.01}, held)
        self.assertFalse(ok)
        self.assertIn("dollar cluster", why)
        ok, _ = correlation_guard.check({"symbol": "USDJPY", "side": "BUY", "risk_pct": 0.01}, held)
        self.assertTrue(ok)  # a long-dollar trade hedges the cluster: always allowed

    def test_live_unlock_needs_every_gate(self):
        good = [{"symbol": "EURUSD", "qty": 10_000, "entry": 1.0, "pnl": 30.0 if i % 10 < 7 else -20.0}
                for i in range(60)]
        st = strategy_lab.check(good)["fx"]
        self.assertTrue(st["gates"]["proof"])
        self.assertFalse(st["promoted"])  # no sim figure and no sign-off yet
        deep = [{"symbol": "EURUSD", "qty": 10_000, "entry": 1.0, "pnl": 500.0 if i < 40 else -300.0}
                for i in range(70)]  # wins first, then a 30-loss run: a $9k drawdown on a $20k sleeve
        self.assertGreater(strategy_lab.arm_stats(deep)["fx"]["max_drawdown"], strategy_lab.MAX_DRAWDOWN)
        self.assertFalse(strategy_lab.check(deep)["fx"]["gates"]["proof"])

    def test_core_modules_import_without_cycles(self):
        import subprocess
        import sys
        mods = ("desk", "hooks", "risk", "master_pipeline", "team_relay", "correlation_guard", "ops_guard",
                "execution_guard", "strategy_lab", "trade_checker", "live_orders", "bridge", "engine", "dashboard")
        code = "import " + ", ".join(f"trading.{m}" for m in mods) + "; print('OK')"
        for first in mods:  # each one imported first, in a fresh interpreter, so an import cycle can't hide
            out = subprocess.run([sys.executable, "-c", f"import trading.{first}; " + code], capture_output=True,
                                 text=True, cwd=__file__.rsplit("/tests/", 1)[0] if "/tests/" in __file__ else None)
            self.assertEqual(out.stdout.strip(), "OK", f"importing trading.{first} first: {out.stderr[-400:]}")

    def test_audit_log_folder_is_made_when_missing(self):
        import tempfile
        from pathlib import Path
        from trading.trade_checker import FILE, TradeChecker
        with tempfile.TemporaryDirectory() as d:
            folder = Path(d) / "logs" / "new"
            TradeChecker(folder).review(symbol="EURUSD", qty=1, price=1.1, desk_score=0.9, threshold=0.72)
            self.assertTrue((folder / FILE).exists())

    def test_replay_bars_any_case_and_naive_times(self):
        from trading.backtest import normalize_bar
        b = normalize_bar({"Datetime": "2026-11-24 22:15:00", "Open": "1.08", "High": 1.09, "Low": 1.07, "Close": 1.085})
        self.assertEqual(b["timestamp"].tzinfo, timezone.utc)
        self.assertEqual((b["open"], b["close"]), (1.08, 1.085))
        self.assertEqual(execution_integrity.heikin_ashi([{"Open": 1, "High": 2, "Low": 0.5, "Close": 1.5}])[0].real_open, 1)
        naive = datetime(2026, 11, 24, 22, 15)
        h = ops_guard.TickHealth("EURUSD", "1h", naive, bid=1.08, ask=1.0802, atr_price=0.0025)
        ok, why = ops_guard.check_feed_and_rollover_health(h, now_utc=naive)  # no naive/aware TypeError
        self.assertFalse(ok)
        self.assertTrue(why.startswith("rollover"))

    def test_calendar_always_watches_big_tech_earnings(self):
        from trading import event_guard
        cal = event_guard.Calendar(["EURUSD"], get=lambda url: {})
        self.assertTrue({"AAPL", "NVDA", "TSLA", "MSFT", "AMZN", "META", "GOOGL"} <= cal.symbols)
        self.assertLessEqual(event_guard.REFRESH_SECONDS, 86_400)  # reloaded at least daily (hourly today)

    def test_practice_quota_never_reaches_the_desk(self):
        out = io.StringIO()
        desk = engine.run({"AGENT_COUNT": "300", "TICKS": "60", "TICK_SECONDS": "0"}, stream=out)
        events = [json.loads(line) for line in out.getvalue().splitlines()]
        orders = [e for e in events if e["type"] == "order"]
        self.assertTrue(all(not e.get("practice") for e in orders))
        self.assertEqual(len(orders), sum(e["type"] == "pipeline" for e in events))
        self.assertIn("blocked", desk.usd_guard)


if __name__ == "__main__":
    unittest.main()
