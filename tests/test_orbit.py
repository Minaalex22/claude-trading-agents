"""Orbit portfolio: five arms, 20% sleeves, heat sizing, practice trades out, the page's numbers."""
import unittest
from collections import deque

from trading import orbit_portfolio as orbit


def trade(t, arm, pnl, s="X"):
    return {"t": t, "arm": arm, "s": s, "pnl": pnl}


class Heat(unittest.TestCase):
    def test_warmup_cold_hot_and_normal(self):
        self.assertEqual(orbit.heat(deque([1, 2])), 0.85)
        self.assertEqual(orbit.heat(deque([-5, -5, -5, 1, -1, -2])), 0.35)
        self.assertEqual(orbit.heat(deque([5, 5, 5, -1, 2])), 1.45)
        self.assertEqual(orbit.heat(deque([5, -1, 5, -1, -1, 1])), 1.0)


class Coordinator(unittest.TestCase):
    def test_five_arms_twenty_percent_each(self):
        c = orbit.OrbitPortfolioCoordinator()
        self.assertEqual(len(c.arms), 5)
        self.assertAlmostEqual(sum(a.base_weight for a in c.arms.values()), 1.0)

    def test_routing_and_timeframe_floor(self):
        c = orbit.OrbitPortfolioCoordinator()
        self.assertEqual(c.route_arm_for_trade("usdjpy", "4h").key, "usdjpy")
        self.assertEqual(c.route_arm_for_trade("NVDA", "1h").key, "us")
        self.assertIsNone(c.route_arm_for_trade("BTC", "5m"))   # noise timeframes refused
        self.assertIsNone(c.route_arm_for_trade("BTC", "15m"))
        self.assertIsNone(c.route_arm_for_trade("OIL", "1h"))   # not in any arm

    def test_practice_never_counts(self):
        c = orbit.OrbitPortfolioCoordinator()
        c.record_arm_result("BTC", "4h", 500, is_practice=True)
        self.assertEqual(c.account_equity, orbit.START_CASH)
        self.assertEqual(c.skipped_practice, 1)

    def test_budget_and_switch(self):
        c = orbit.OrbitPortfolioCoordinator()
        self.assertEqual(c.compute_arm_order_budget("BTC", "4h"), round(100_000 * 0.375 * 0.01 * 0.85, 2))  # crypto sleeve 37.5% since 25 Nov
        off = orbit.OrbitPortfolioCoordinator.from_env({"ORBIT_HEAT": "off"})
        self.assertEqual(off.heat("BTC"), 1.0)
        self.assertEqual(orbit.OrbitPortfolioCoordinator.from_env({}).heat("BTC"), 0.85)


class Book(unittest.TestCase):
    def test_sleeve_scaling_and_dollar_profit_factor(self):
        r = orbit.report([trade(1, "gold", 100.0), trade(2, "gold", -50.0)], use_heat=False)
        c = r["combined"]
        self.assertAlmostEqual(c["net"], 25.0 - 12.5 * 1.00025, places=2)  # 25% gold sleeve, compounding
        self.assertAlmostEqual(c["profit_factor"], 2.0, places=2)

    def test_heat_shrinks_a_cold_arm(self):
        losers = [trade(i, "gold", -100.0) for i in range(10)]
        on = orbit.report(losers, use_heat=True)["combined"]
        off = orbit.report(losers, use_heat=False)["combined"]
        self.assertGreater(on["net"], off["net"])
        self.assertLess(on["max_drawdown_pct"], off["max_drawdown_pct"])

    def test_practice_rows_dropped_from_book(self):
        r = orbit.report([trade(1, "fx", 10.0), {**trade(2, "fx", 999.0), "practice": True}], use_heat=False)
        self.assertEqual(r["combined"]["trades"], 1)

    def test_live_trades_mapped_with_costs(self):
        rows = orbit.live_trades([{"ts": 5, "symbol": "BTC", "qty": 1, "entry": 100, "exit": 110, "pnl": 10, "side": "long"},
                                  {"ts": 6, "symbol": "OIL", "qty": 1, "entry": 1, "exit": 2, "pnl": 1}])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["arm"], "crypto")
        self.assertAlmostEqual(rows[0]["pnl"], 10 - 210 * 1 / 10_000, places=2)

    def test_dashboard_from_saved_replay(self):
        d = orbit.dashboard([])
        self.assertEqual([a["key"] for a in d["arms"]], ["usdjpy", "fx", "gold", "us", "crypto"])
        self.assertFalse(d["source"]["practice_trades_included"])
        self.assertGreater(d["source"]["backtest_trades"], 0)
        self.assertEqual(len(d["points"]), len(d["points_off"]))
        for key in ("with_heat", "without_heat"):
            self.assertIn("max_drawdown_pct", d[key]["combined"])
            self.assertIn("correlation", d[key])
        self.assertIn("same_direction_share", d["floating"])
        csv = orbit.chosen_trades_csv(d).splitlines()
        self.assertEqual(csv[0], "date,arm,symbol,pnl_usd,is_practice")
        self.assertTrue(all(line.endswith(",0") for line in csv[1:]))


if __name__ == "__main__":
    unittest.main()
