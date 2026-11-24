"""mina's four fixes (batch 7), as her regression tests asked, on our own functions."""

import types
import unittest
from collections import deque

from trading import agents, dual_brain, hooks
from trading.agents import MeanReversion, Momentum
from trading.desk import Desk
from trading.reading_room import View


class ReversionDipTests(unittest.TestCase):
    def test_reversion_dip_buy_allowed_when_chart_down_and_news_old(self):
        view = View(direction=-0.8, chart=-0.8, news=-0.5, news_age_mins=150)
        c = hooks.OrderCheck("X", 1.0, 0.0, 100.0, view=view, style="reversion")
        self.assertEqual(hooks.against_the_room(c), (1.0, None))
        fresh = View(direction=-0.3, chart=0.0, news=-0.5, news_age_mins=80)  # inside 90 minutes
        self.assertEqual(hooks.against_the_room(hooks.OrderCheck("X", 1.0, 0.0, 100.0, view=fresh, style="reversion"))[0], 0.0)

    def test_trend_needs_chart_with_it_and_news_not_against(self):
        a = Momentum("trend-0001-momentum", "X", 5)
        a.department = "trend"
        from trading import review
        a.rules = review.starting_rules("trend")
        a.prices.extend([100, 101, 102, 103, 104, 105])
        a.view = View(direction=0.6, chart=0.6, news=-0.2)
        self.assertEqual(a._confirm(1.0, 1.0), 0.0)  # news -0.2 is against it
        a.view = View(direction=0.6, chart=0.6, news=0.0)
        self.assertEqual(a._confirm(1.0, 1.0), 1.0)


class HorizonTests(unittest.TestCase):
    def test_fast_cluster_cannot_fire_without_medium_or_slow(self):
        import trading.desk as d
        self.addCleanup(setattr, d, "CONSENSUS", d.CONSENSUS)
        d.CONSENSUS = False
        self.assertEqual(Desk._horizon_vote({"fast": [0.95], "higher": [0.0]}, 0.9), 0.0)
        self.assertAlmostEqual(Desk._horizon_vote({"fast": [], "higher": [0.6]}, 0.6), 0.6 * 0.85)
        self.assertAlmostEqual(Desk._horizon_vote({"fast": [0.8], "higher": [0.6]}, 0.7), 0.45 * 0.8 + 0.55 * 0.6)
        self.assertEqual(Desk._horizon_vote({"fast": [0.8], "higher": [-0.6]}, 0.1), 0.0)  # they disagree
        d.CONSENSUS = True
        self.assertEqual(Desk._horizon_vote({"fast": [0.8], "higher": [0.6]}, 0.5), 0.5)  # consensus value stands


class ProbationTests(unittest.TestCase):
    def test_dual_brain_shadow_probation_recovers(self):
        s = {"recent": {}, "probation": {}}
        for _ in range(12):
            dual_brain._remember(s, "trend:chop", -1.0)
        self.assertTrue(dual_brain.on_probation(s, "trend:chop"))
        for _ in range(6):
            dual_brain._remember(s, "trend:chop", 2.5)
        self.assertFalse(dual_brain.on_probation(s, "trend:chop"))


class LearnOnCloseTests(unittest.TestCase):
    def test_trust_frozen_during_a_chosen_trade_then_learns_once(self):
        from trading import exits
        self.addCleanup(setattr, exits, "ENABLED", exits.ENABLED)
        exits.ENABLED = False
        self.addCleanup(setattr, agents, "LEARN_ON_CLOSE", agents.LEARN_ON_CLOSE)
        agents.LEARN_ON_CLOSE = True
        m = Momentum("m", "X", 10)
        for p in [100 + i for i in range(30)]:
            m.observe(p)
        self.assertTrue(m.position)
        frozen = m.brain.weight
        m.observe(131)
        self.assertEqual(m.brain.weight, frozen)  # still in the trade: no per-bar lesson
        m._trade(0.0)  # closes in profit
        self.assertGreater(m.brain.weight, frozen)

    def test_practice_trades_never_teach(self):
        r = MeanReversion("r", "X", 10)
        r.prices.extend([100.0] * 11)
        r.workspace["entry"] = 100.0
        r.workspace["entry_note"] = {"forced": True}
        r.position = 1.0
        r.prices.append(110.0)
        before = r.brain.weight
        r._close_trade()
        self.assertEqual(r.brain.weight, before)


if __name__ == "__main__":
    unittest.main()
