import types
import unittest

from trading import desk as desk_mod
from trading import loss_audit
from trading.agents import Momentum


def feed(agent, prices):
    for p in prices:
        agent.observe(p)


class PracticeSplitTests(unittest.TestCase):
    def setUp(self):
        loss_audit.reset()

    def test_practice_trades_are_scored_apart_from_chosen_ones(self):
        t = Momentum("m", "AAPL", 10)
        feed(t, [100 + (1 if i % 2 else -1) for i in range(45)])
        t.must_practice = True
        t._trade(1.0)
        t.observe(90.0)
        t._trade(0.0)  # a forced practice trade that lost
        self.assertEqual(t.workspace["practice_losses"], 1)
        self.assertEqual(t.workspace["trade_wins"] + t.workspace["trade_losses"], 0)
        t.must_practice = False
        t._trade(1.0)
        t.observe(95.0)
        t._trade(0.0)  # a chosen trade that won
        self.assertEqual(t.workspace["trade_wins"], 1)
        self.assertEqual(t.workspace["practice_losses"], 1)

    def test_trend_weight_zero_listens_to_reversion_only(self):
        self.addCleanup(setattr, desk_mod, "TREND_WEIGHT", desk_mod.TREND_WEIGHT)
        stub = types.SimpleNamespace(alpha=None)
        v = {"trend": [1.0], "reversion": [-0.5], "analyst": [1.0], "risk": [], "macro": []}
        desk_mod.TREND_WEIGHT = 1.0
        self.assertAlmostEqual(desk_mod.Desk._score(stub, v), 1.0)
        desk_mod.TREND_WEIGHT = 0.0
        self.assertAlmostEqual(desk_mod.Desk._score(stub, v), -0.5)


if __name__ == "__main__":
    unittest.main()
