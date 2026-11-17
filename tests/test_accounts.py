"""Practice accounts: each trader's own paper book, its record, and the proven bar."""

import io
import unittest

from trading import accounts, engine
from trading.accounts import PracticeAccount, proven


class AccountTests(unittest.TestCase):
    def test_round_trip_pays_costs_and_records_the_trade(self):
        a = PracticeAccount()
        a.rebalance(1.0, 100.0)   # all in
        a.rebalance(0.0, 110.0)   # out at +10%
        self.assertEqual((a.wins, a.losses), (1, 0))
        self.assertAlmostEqual(a.equity(110.0), 10_000 * 1.1 - a.costs, places=6)
        self.assertGreater(a.costs, 0)

    def test_short_and_loss(self):
        a = PracticeAccount()
        a.rebalance(-0.5, 100.0)
        a.rebalance(0.0, 110.0)   # short lost
        self.assertEqual((a.wins, a.losses), (0, 1))
        self.assertLess(a.equity(110.0), accounts.START)

    def test_drawdown_tracked(self):
        a = PracticeAccount()
        a.rebalance(1.0, 100.0)
        a.mark(80.0)
        self.assertAlmostEqual(a.max_dd, 0.2, places=2)

    def test_proven_bar(self):
        a = PracticeAccount()
        self.assertFalse(proven(a))  # no record yet
        price = 100.0
        for i in range(accounts.MIN_TRADES):
            a.rebalance(1.0, price)
            price *= 1.01 if i % 4 else 0.995  # mostly winners, a few small losers
            a.rebalance(0.0, price)
        self.assertGreaterEqual(a.trades, accounts.MIN_TRADES)
        self.assertTrue(proven(a))
        a.rebalance(1.0, price)
        a.mark(price * 0.7)  # a 30% drop breaks the drawdown bar
        self.assertFalse(proven(a))

    def test_state_round_trip(self):
        a = PracticeAccount()
        a.rebalance(1.0, 50.0)
        b = PracticeAccount()
        b.load(a.state())
        self.assertEqual(b.state(), a.state())


class DeskTests(unittest.TestCase):
    def test_every_trader_has_an_account_and_leaderboard_shows_it(self):
        desk = engine.run({"TICKS": "120", "TICK_SECONDS": "0", "AGENT_COUNT": "200"}, stream=io.StringIO())
        traders = [a for a in desk.agents if a.role == "trader"]
        self.assertTrue(traders and all(a.account is not None for a in traders))
        self.assertTrue(any(a.account.trades for a in traders))
        top = desk.leaderboard(5)
        self.assertIn("proven", top[0]["account"])

    def test_proven_only_desk_trades_nothing_without_proven_agents(self):
        out = io.StringIO()
        desk = engine.run({"TICKS": "40", "TICK_SECONDS": "0", "AGENT_COUNT": "200", "PROVEN_ONLY": "on"}, stream=out)
        self.assertEqual(desk.proven_count(), 0)  # 40 ticks is too short to prove anything
        self.assertNotIn('"type":"order"', out.getvalue())


if __name__ == "__main__":
    unittest.main()
