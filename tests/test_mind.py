"""Every trader's two-step mind: think (its own belief), then decide (its own action)."""

import io
import json
import tempfile
import unittest
from pathlib import Path

from trading import engine, mind
from trading.agents import Momentum
from trading.reading_room import View


def agent(prices, agent_id="trend-0001-momentum"):
    a = Momentum(agent_id, "AAPL", len(prices) - 1)
    a.prices.extend(prices)
    return a


class MindTests(unittest.TestCase):
    def test_no_two_minds_alike(self):
        a, b = mind.Mind("trend-0001"), mind.Mind("trend-0002")
        self.assertNotEqual((a.attention, a.weights, a.patience), (b.attention, b.weights, b.patience))
        self.assertEqual(mind.Mind("trend-0001").weights, a.weights)  # but each is stable for its id

    def test_think_then_decide_in_words(self):
        a = agent([100 + i for i in range(20)])
        a.view = View(direction=0.6, chart=0.5)
        lean = a.mind.think(a, 0.9)
        self.assertGreater(lean, 0)
        self.assertIn("chance it goes up", a.mind.thought)
        a.mind.patience = 0.99
        self.assertEqual(a.mind.decide(lean), 0.0)
        self.assertIn("wait", a.mind.decision)
        a.mind.patience = 0.05
        self.assertGreater(a.mind.decide(lean), 0)
        self.assertIn("buy", a.mind.decision)

    def test_thinking_learns_what_predicts(self):
        m = mind.Mind("x")
        m.attention = ["strategy", "room"]
        m.weights = {"strategy": 0.0, "room": 0.0}
        m.learning_rate = 0.05
        a = agent([100.0] * 10)
        price = 100.0
        for i in range(400):
            room = 1.0 if i % 2 else -1.0
            a.view = View(direction=room, chart=0.0)
            a.prices.append(price)
            m.think(a, 0.0)
            price += room  # the room's view always comes true
        self.assertGreater(m.weights["room"], 0.5)
        self.assertGreater(m.accuracy, 0.9)

    def test_deciding_learns_from_its_own_trades(self):
        m = mind.Mind("y")
        p0, a0 = m.patience, m.appetite
        m.trade_closed(False, m.patience)
        self.assertGreater(m.patience, p0)
        self.assertLess(m.appetite, a0)
        p1 = m.patience
        m.trade_closed(True, 0.9)
        self.assertLess(m.patience, p1)

    def test_minds_are_remembered_and_reported(self):
        with tempfile.TemporaryDirectory() as d:
            env = {"AGENT_COUNT": "80", "TICKS": "300", "TICK_SECONDS": "0", "EVENTS_DIR": d,
                   "PRACTICE_HOUR_TICKS": "100"}
            out = io.StringIO()
            first = engine.run(env, stream=out)
            t = next(a for a in first.agents if a.mind)
            second = engine.run({**env, "TICKS": "1"}, stream=io.StringIO())
            t2 = next(a for a in second.agents if a.agent_id == t.agent_id)
            self.assertGreaterEqual(t2.mind.guesses, t.mind.guesses)
            orgs = [json.loads(x) for x in out.getvalue().splitlines() if '"minds"' in x]
            minds = orgs[-1]["practice"]["minds"]
            self.assertIn("trend", minds)
            self.assertIn("thinks", minds["trend"]["example"])

    def test_minds_off(self):
        d = engine.run({"AGENT_COUNT": "80", "TICKS": "5", "TICK_SECONDS": "0", "MINDS": "off"},
                       stream=io.StringIO())
        self.assertTrue(all(a.mind is None for a in d.agents))
        mind.ENABLED = True


class JarvisMindTests(unittest.TestCase):
    def test_jarvis_explains_think_and_decide(self):
        from trading.jarvis import BuiltinBrain
        snap = {"practice": {"minds": {"trend": {"guessed_right": 0.52, "trusts": {"the chart": 1.1},
                                                 "example": {"agent": "trend-0007", "thinks": "61% chance it goes up",
                                                             "decides": "buy 40% of my account"}}}}}
        answer = BuiltinBrain().answer("how do they think and decide?", snap)
        self.assertIn("thinks first, then decides", answer)
        self.assertIn("buy 40%", answer)


if __name__ == "__main__":
    unittest.main()
