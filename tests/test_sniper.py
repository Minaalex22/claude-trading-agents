"""Sniper mode (batch 6): A+ entries only, banked at the mean."""

import types
import unittest
from collections import deque

from trading import exits, sniper


class SniperGateTests(unittest.TestCase):
    def setUp(self):
        self.addCleanup(setattr, sniper, "SNIPER", sniper.SNIPER)
        sniper.SNIPER = True
        flat = [100.0 + (i % 3) * 0.1 for i in range(30)]
        self.reclaim = flat + [98.0, 98.5, 99.0, 100.1]  # swept the 20-bar low, then closed back inside

    def gate(self, **kw):
        args = dict(symbol="X", style="reversion", side=1.0, score=0.9, hist={"X": self.reclaim}, view=None,
                    bar_seconds=3600, hour=14)
        args.update(kw)
        return sniper.gate(**args)

    def test_a_plus_setup_passes(self):
        self.assertIsNone(self.gate())

    def test_each_pillar_blocks(self):
        self.assertIn("0.82", self.gate(score=0.7))
        self.assertIn("1h-4h", self.gate(bar_seconds=900))
        self.assertIn("UTC", self.gate(hour=20))
        self.assertIn("reversion", self.gate(style="trend"))
        self.assertIn("reclaim", self.gate(hist={"X": self.reclaim[:-1]}))  # still below the old low
        self.assertIn("news", self.gate(view=types.SimpleNamespace(news=-0.3)))
        self.assertIn("SMT", self.gate(symbol="BTC", hist={"BTC": self.reclaim, "ETH": self.reclaim}))

    def test_simulator_has_no_sniper(self):
        self.assertIsNone(self.gate(score=0.1, bar_seconds=None))


class MeanSnapExitTests(unittest.TestCase):
    def setUp(self):
        for k in ("TREND_TRAIL", "BAR_SECONDS"):
            self.addCleanup(setattr, exits, k, getattr(exits, k))
        self.addCleanup(setattr, sniper, "SNIPER", sniper.SNIPER)
        exits.TREND_TRAIL, exits.BAR_SECONDS, sniper.SNIPER = False, 3600, True

    def test_banks_80_percent_at_0_9r_runner_to_2r(self):
        a = types.SimpleNamespace(position=1.0, prices=deque([100.0, 100.1] * 30), style="reversion", window=50,
                                  must_practice=False, workspace={"entry": 100.1})
        exits.open_trade(a)
        plan = a.workspace["exit_plan"]
        self.assertAlmostEqual(plan["stop_r"], -1.35)
        a.prices.append(100.1 + 0.95 * plan["r"])
        self.assertEqual(exits.check(a), "scale")
        self.assertAlmostEqual(plan["share"], 0.8)
        self.assertAlmostEqual(plan["stop_r"], 0.10)
        a.prices.append(100.1 + 2.05 * plan["r"])
        self.assertEqual(exits.check(a), "target")


if __name__ == "__main__":
    unittest.main()
