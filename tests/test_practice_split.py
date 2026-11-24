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


class PracticeWallTests(unittest.TestCase):
    def test_practice_trades_never_reach_the_desk_or_the_account(self):
        import io
        from trading import engine

        env = {"AGENT_COUNT": "120", "TICKS": "400", "TICK_SECONDS": "0", "SEED": "3", "MEMORY": "off",
               "READING_ROOM_CLAUDE": "off", "COACH": "off", "PRACTICE_HOUR_TICKS": "40",
               "PRACTICE_VOTES": "on"}  # an old switch: must change nothing

        real_trade = Momentum._trade.__globals__["Trader"]._trade
        practice_seen = []

        def trade_spy(agent, position):
            if agent.must_practice and agent.account is not None:
                before = agent.account.shares
                real_trade(agent, position)
                practice_seen.append(agent.account.shares == 0.0 or before == agent.account.shares)
                return
            real_trade(agent, position)

        trader_cls = Momentum._trade.__globals__["Trader"]
        self.addCleanup(setattr, trader_cls, "_trade", real_trade)
        trader_cls._trade = trade_spy
        desk = engine.run(env, stream=io.StringIO())
        self.assertTrue(practice_seen)  # the quota really made practice trades
        self.assertTrue(all(practice_seen))  # and none of them moved a practice account into a position
        self.assertFalse(hasattr(desk_mod, "PRACTICE_VOTES"))

    def test_desk_skips_votes_marked_practice(self):
        from trading.agents import Vote
        self.assertTrue(Vote("a", "trader", "BTC", 1.0, practice=True).practice)
        self.assertFalse(Vote("a", "trader", "BTC", 1.0).practice)


class ZeroLeakTests(unittest.TestCase):
    def test_no_desk_order_when_every_trader_is_practising(self):
        import io
        from trading import engine

        def all_practise(self):
            for a in self.agents:
                if a.role == "trader":
                    a.must_practice = True

        self.addCleanup(setattr, desk_mod.Desk, "_set_practice_pace", desk_mod.Desk._set_practice_pace)
        desk_mod.Desk._set_practice_pace = all_practise
        env = {"AGENT_COUNT": "120", "TICKS": "300", "TICK_SECONDS": "0", "SEED": "4", "MEMORY": "off",
               "READING_ROOM": "off", "COACH": "off", "PRACTICE_HOUR_TICKS": "40"}
        desk = engine.run(env, stream=io.StringIO())
        practised = sum(a.workspace.get("practice_wins", 0) + a.workspace.get("practice_losses", 0)
                        for a in desk.agents)
        self.assertGreater(practised, 0)  # the traders did trade (practice)
        self.assertEqual(desk.journal.closed, 0)  # yet the desk never opened and closed a single order
        self.assertTrue(all(abs(q) < 1e-12 for q in desk.broker.positions.values()))


class CostHurdleTests(unittest.TestCase):
    def test_hurdle_follows_the_bar_length(self):
        self.addCleanup(setattr, desk_mod, "COST_HURDLE", desk_mod.COST_HURDLE)
        desk_mod.COST_HURDLE = None
        stub = types.SimpleNamespace(bar_seconds=None)
        hurdle = lambda s: desk_mod.Desk._hurdle(types.SimpleNamespace(bar_seconds=s))
        self.assertEqual(desk_mod.Desk._hurdle(stub), desk_mod.FRICTION_SIM)
        self.assertEqual(hurdle(15), 5.0)
        self.assertEqual(hurdle(300), 4.0)
        self.assertEqual(hurdle(900), 2.5)
        self.assertEqual(hurdle(3600), 1.8)
        self.assertEqual(hurdle(86400), 1.5)
        desk_mod.COST_HURDLE = 0.0
        self.assertEqual(hurdle(300), 0.0)
