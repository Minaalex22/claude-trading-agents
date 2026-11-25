import unittest

from trading import agents, exits
from trading.agents import MeanReversion, RSI2, build_team


def feed(agent, prices):
    for p in prices:
        agent.observe(p)


class RSI2Tests(unittest.TestCase):
    def setUp(self):
        self.addCleanup(setattr, exits, "ENABLED", exits.ENABLED)
        exits.ENABLED = False

    def test_rsi_of_two_down_moves_is_zero(self):
        t = RSI2("r", "AAPL", 10)
        feed(t, [100, 101, 102, 101, 100])
        self.assertEqual(t.rsi2(), 0.0)

    def test_buys_a_pullback_in_an_uptrend_and_exits_on_the_bounce(self):
        t = RSI2("r", "AAPL", 10)
        up = [100 + i for i in range(40)]
        t.prices.extend(up)
        t.history.extend(up)
        t.prices.extend([137.5, 137.0])  # two down moves: RSI(2) = 0, still above the long average
        t.history.extend([137.5, 137.0])
        self.assertGreater(t.decide(), 0)
        t.position = 1.0
        t.prices.append(137.2)  # small bounce, still under its 5-bar average and RSI(2) under 70
        self.assertEqual(t.decide(), 1.0)
        t.prices.extend([139.0, 140.0])  # bounce done
        self.assertEqual(t.decide(), 0.0)

    def test_no_long_in_a_downtrend(self):
        t = RSI2("r", "AAPL", 10)
        down = [140 - i for i in range(40)]
        t.prices.extend(down + [100.5, 100.0])
        t.history.extend(down + [100.5, 100.0])
        self.assertLessEqual(t.decide(), 0.0)

    def test_reversion_department_is_split(self):
        self.addCleanup(setattr, agents, "RSI2_SHARE", agents.RSI2_SHARE)
        agents.RSI2_SHARE = 0.5
        team = build_team(40, ["AAPL"], 0)
        rev = [a for a in team if a.department == "reversion"]
        self.assertTrue(any(isinstance(a, RSI2) for a in rev))
        self.assertTrue(any(type(a) is agents.MeanReversion for a in rev))
        self.assertTrue(all(a.style == "reversion" for a in rev))


if __name__ == "__main__":
    unittest.main()


class RSI2SwitchTests(unittest.TestCase):
    def test_on_by_default_since_25_nov(self):
        team = build_team(40, ["AAPL"], 0)
        self.assertTrue(any(isinstance(a, RSI2) for a in team))
        self.assertFalse(any(isinstance(a, MeanReversion) and not isinstance(a, RSI2) for a in team))
