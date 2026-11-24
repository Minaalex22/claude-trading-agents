import unittest

from trading import exits, training_report
from trading.agents import Momentum, gate_bucket


class ExitTests(unittest.TestCase):
    def setUp(self):
        self.addCleanup(setattr, exits, "ENABLED", exits.ENABLED)
        self.addCleanup(setattr, exits, "TREND_TRAIL", exits.TREND_TRAIL)
        exits.ENABLED = True
        exits.TREND_TRAIL = False  # the fixed template; the trend trail has its own test

    def _long(self, prices):
        t = Momentum("m", "X", 5)
        t.must_practice = False
        for p in prices:
            t.prices.append(p)
        t._trade(1.0)
        return t

    def test_stop_loss_at_one_r(self):
        t = self._long([100, 101, 100, 101, 100, 101])
        r = t.workspace["exit_plan"]["r"]
        t.prices.append(101 - 1.1 * r)
        self.assertEqual(exits.check(t), "stop")

    def test_trend_trades_trail_instead_of_capping_at_the_target(self):
        exits.TREND_TRAIL = True
        t = self._long([100, 101, 100, 101, 100, 101])
        r = t.workspace["exit_plan"]["r"]
        self.assertEqual(t.workspace["exit_plan"]["stop_r"], -exits.TREND_STOP_R)  # 1.2R first stop
        t.prices.append(101 - 1.1 * r)
        self.assertIsNone(exits.check(t))  # a 1.1R wick no longer stops a trend trade
        t.prices.append(101 + 4.0 * r)
        self.assertIsNone(exits.check(t))  # no 2.4R cap: the winner runs
        self.assertAlmostEqual(t.workspace["exit_plan"]["stop_r"], 4.0 - exits.TRAIL_R, places=6)
        t.prices.append(101 + 2.9 * r)
        self.assertEqual(exits.check(t), "breakeven")  # gave back more than 1R: out, still well in profit

    def test_breakeven_after_one_r_then_target(self):
        t = self._long([100, 101, 100, 101, 100, 101])
        r = t.workspace["exit_plan"]["r"]
        t.prices.append(101 + 1.2 * r)
        self.assertIsNone(exits.check(t))
        self.assertEqual(t.workspace["exit_plan"]["stop_r"], 0.0)
        t.prices.append(101 - 0.01)
        self.assertEqual(exits.check(t), "breakeven")
        t2 = self._long([100, 101, 100, 101, 100, 101])
        t2.prices.append(101 + 2.5 * t2.workspace["exit_plan"]["r"])
        self.assertEqual(exits.check(t2), "target")

    def test_time_exit_and_cooldown(self):
        t = self._long([100, 101, 100, 101, 100, 101])
        reason = None
        for _ in range(exits.MAX_HOLD_WINDOWS * t.window):
            t.prices.append(101)
            reason = exits.check(t) or reason
        self.assertEqual(reason, "time")
        t.workspace["entry"], t.position = 101, 1.0
        t.workspace["exit_plan"] = {"r": 1.0, "stop_r": -1.0, "best_r": 0.0, "held": 0}
        t.prices.append(99)
        self.assertEqual(t._manage_exit(1.0), 0.0)
        self.assertEqual(t.workspace["exits"]["stop"], 1)
        t.position = 0.0
        self.assertEqual(t._manage_exit(1.0), 0.0)  # no revenge trade on the same side
        self.assertEqual(t._manage_exit(-1.0), -1.0)

    def test_off_switch(self):
        exits.ENABLED = False
        t = self._long([100, 101, 100, 101, 100, 101])
        t.prices.append(1)
        self.assertIsNone(exits.check(t))


class TrainingReportTests(unittest.TestCase):
    def test_buckets(self):
        self.assertEqual(gate_bucket(0.95), "0.9")
        self.assertEqual(gate_bucket(0.75), "0.7")
        self.assertEqual(gate_bucket(0.1), "0.0")

    def test_report_ranks_and_gate(self):
        school = lambda a, b: {"trained": 10, "graduates": 9, "avg_exam": 0.85,
                               "courses": {"x": {"name": "Reading numbers", "avg": a},
                                           "y": {"name": "Making decisions", "avg": b}}}
        org = {"tick": 5, "departments": {
            "trend": {"name": "Trend", "trade_wins": 60, "trade_losses": 40, "pnl": 10, "school": school(0.9, 0.8),
                      "exits": {"stop": 10, "target": 5}, "by_sure": {"0.0": [20, 50], "0.5": [10, 30], "0.7": [5, 20], "0.8": [40, 50]}},
            "reversion": {"name": "Reversion", "trade_wins": 50, "trade_losses": 50, "pnl": -5,
                          "school": school(0.7, 0.9), "exits": {"stop": 5}, "by_sure": {"0.9": [30, 40]}},
            "risk": {"name": "Risk", "school": school(0.8, 0.85)}}}
        rep = training_report.build(org)
        self.assertEqual(rep["win_rate"], 0.55)
        self.assertEqual(rep["weakest_first"][0], "reversion")
        rev = next(r for r in rep["departments"] if r["department"] == "reversion")
        self.assertEqual(rev["weakest_course"], "Reading numbers")
        self.assertEqual(rep["exits"]["counts"], {"stop": 15, "target": 5})
        self.assertEqual(rep["gate"]["recommended"], 0.8)  # 0.8 wins most; 0.9 is no better
        self.assertEqual(rep["gate"]["trades"], 190)


if __name__ == "__main__":
    unittest.main()
