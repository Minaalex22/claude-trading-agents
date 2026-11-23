import unittest

from trading import signal_gate as sg
from trading.agents import Momentum


def feed(agent, prices):
    for p in prices:
        agent.observe(p)


class SignalGateTests(unittest.TestCase):
    def setUp(self):
        self.addCleanup(setattr, sg, "ENABLED", sg.ENABLED)
        sg.ENABLED = True
        sg.reset()
        self.t = Momentum("m", "AAPL", 10)
        feed(self.t, [100 + i for i in range(45)])  # clean uptrend: higher timeframe and regime back a long
        self.t.position = 0.0
        sg.reset()

    def test_off_lets_everything_through(self):
        sg.ENABLED = False
        self.t.workspace["sureness"] = 0.0
        self.assertEqual(sg.check(self.t, 1.0, 0.0), 1.0)

    def test_triage_rejects_very_unsure(self):
        self.t.workspace["sureness"] = 0.05
        self.assertEqual(sg.check(self.t, 1.0, 0.05), 0.0)
        self.assertEqual(sg.STATE["rejected"], 1)

    def test_sure_trades_pass_the_gate(self):
        self.t.workspace["sureness"] = 0.9
        self.assertEqual(sg.check(self.t, -1.0, 0.9), -1.0)  # sure enough: no deep read
        self.assertEqual(sg.STATE["fast"], 1)

    def test_unsure_trades_get_the_deep_read(self):
        self.t.workspace["sureness"] = 0.5
        self.assertEqual(sg.check(self.t, 1.0, 0.5), 1.0)  # trend and higher timeframe agree: 2 of 3
        self.assertEqual(sg.check(self.t, -1.0, 0.5), 0.0)  # a short against both fails
        self.assertEqual((sg.STATE["deep_pass"], sg.STATE["deep_fail"]), (1, 1))

    def test_exits_never_gated(self):
        self.t.position = 1.0
        self.t.workspace["sureness"] = 0.0
        self.assertEqual(sg.check(self.t, 0.0, 0.0), 0.0)
        self.assertEqual(sg.check(self.t, 0.5, 0.0), 0.5)


if __name__ == "__main__":
    unittest.main()
