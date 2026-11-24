import types
import unittest

from trading import dual_brain as db
from trading import review
from trading.agents import MeanReversion, Momentum


def feed(agent, prices):
    for p in prices:
        agent.observe(p)


class DualBrainTests(unittest.TestCase):
    def setUp(self):
        self.addCleanup(setattr, db, "ENABLED", db.ENABLED)
        db.ENABLED = True
        db.reset()

    def _agent(self):
        t = MeanReversion("r", "BTC", 10)
        feed(t, [100 + (3 if i % 2 else -3) for i in range(45)])  # choppy, big enough moves to pay costs
        t.must_practice = False
        t.position = 0.0
        t.workspace.pop("dual", None)  # a clean slate: feeding the prices may already have traded
        return t

    def test_fast_half_proposes_a_concrete_setup(self):
        t = self._agent()
        p = db.propose(t, 1.0)
        self.assertEqual(p["side"], 1.0)
        self.assertTrue(p["tag"].startswith("reversion:"))
        self.assertGreater(p["expected_move"], 0)
        self.assertGreater(p["stop"], 0)

    def test_slow_half_vetoes_a_setup_that_loses_in_this_market_type(self):
        t = self._agent()
        tag = db.propose(t, 1.0)["tag"]
        db.state(t)["ledger"][tag] = [2, 12, -10.0]  # 14 trades, expectancy -0.7R
        self.assertEqual(db.check(t, 1.0, 1.0), 0.0)
        self.assertEqual(db.state(t)["vetoes"], {"ledger": 1})

    def test_tilt_cooldown_after_three_losses_in_a_row(self):
        t = self._agent()
        for _ in range(3):
            db.closed(t, False, -0.01, forced=False)
        self.assertEqual(db.check(t, 1.0, 1.0), 0.0)
        self.assertIn("tilt", db.state(t)["vetoes"])

    def test_practice_trades_teach_nothing(self):
        t = self._agent()
        t.workspace["dual_tag"] = "reversion:chop"
        for _ in range(20):
            db.closed(t, False, -0.01, forced=True)
        self.assertEqual(dict(db.state(t)["ledger"]), {})
        self.assertEqual(db.state(t)["streak"], 0)

    def test_practice_and_exits_pass_straight_through(self):
        t = self._agent()
        db.state(t)["tilt_left"] = 5
        t.must_practice = True
        self.assertEqual(db.check(t, 1.0, 1.0), 1.0)  # practice trades are never gated (and never reach the desk)
        t.must_practice = False
        t.position = 1.0
        self.assertEqual(db.check(t, 0.5, 0.5), 0.5)  # trimming

    def test_friction_veto_on_a_flat_market(self):
        t = Momentum("m", "BTC", 10)
        feed(t, [100 + 0.0001 * i for i in range(45)])
        t.must_practice = False
        self.assertEqual(db.audit(t, db.propose(t, 1.0)), "friction")

    def test_share_best_passes_the_ledger_on_at_half_weight(self):
        best, weak = self._agent(), self._agent()
        db.state(best)["ledger"]["reversion:chop"] = [10, 4, 6.0]
        db.state(weak)["streak"] = 2
        db.copy(best, weak)
        self.assertEqual(db.state(weak)["ledger"]["reversion:chop"], [5, 2, 3.0])
        self.assertEqual(db.state(weak)["streak"], 0)


if __name__ == "__main__":
    unittest.main()
