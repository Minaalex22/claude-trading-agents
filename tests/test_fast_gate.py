import unittest

from trading import fast_gate as fg
from trading import money_rules
from trading.decision_desk import build


class FastGateTest(unittest.TestCase):
    def test_unanimous_strong_heads_act(self):
        heads = [fg.Head(n, 1, 0.95) for n in ("trend", "flow", "street", "news")]
        d = fg.decide("BTC", heads, [("risk", True, "ok")])
        self.assertEqual(d["verdict"], "ACT")
        self.assertEqual(d["direction"], "buy")

    def test_two_voices_cannot_reach_the_gate(self):
        heads = [fg.Head("trend", 1, 1.0), fg.Head("flow", 1, 1.0), fg.Head("news")]
        d = fg.decide("BTC", heads, [("risk", True, "ok")])
        self.assertLess(d["confidence"], fg.ACT_AT)
        self.assertNotEqual(d["verdict"], "ACT")

    def test_failed_risk_check_holds(self):
        heads = [fg.Head(n, -1, 1.0) for n in ("trend", "flow", "street", "news")]
        d = fg.decide("ETH", heads, [("Sentinel size", False, "run 10% of normal size")])
        self.assertEqual(d["verdict"], "HOLD")
        self.assertTrue(any("Sentinel" in r for r in d["reasons"]))

    def test_split_heads_hold(self):
        heads = [fg.Head("trend", 1, 0.9), fg.Head("flow", -1, 0.9), fg.Head("street", 1, 0.5), fg.Head("news", -1, 0.5)]
        self.assertEqual(fg.decide("X", heads, [])["verdict"], "HOLD")

    def test_history_needs_30_trades_and_clear_range(self):
        self.assertEqual(fg.history_head({"trades": 10, "won": 9}).vote, 0)
        self.assertEqual(fg.history_head({"trades": 100, "won": 70}).vote, 1)
        self.assertEqual(fg.history_head({"trades": 100, "won": 50}).vote, 0)

    def test_self_weighting(self):
        c = fg.Calibration()
        for i in range(40):
            c.record("flow", True)
            c.record("news", i % 4 == 0)
        self.assertEqual(c.weight("flow"), 2.0)
        self.assertEqual(c.weight("news"), 0.25)
        self.assertEqual(c.weight("street"), 1.0)

    def test_kelly_no_edge_blocks(self):
        k = fg.half_kelly([-2.0] * 20 + [1.0] * 10, 10.0)
        self.assertEqual(k["size"], 0.0)
        heads = [fg.Head(n, 1, 1.0) for n in ("trend", "flow", "street", "news")]
        self.assertEqual(fg.decide("BTC", heads, [], sizing=k)["verdict"], "HOLD")

    def test_drift_breaker(self):
        self.assertTrue(fg.drift_breaker([-1.0] * 15 + [0.5] * 10)["tripped"])
        self.assertFalse(fg.drift_breaker([1.0] * 5)["tripped"])


class MoneyRulesTest(unittest.TestCase):
    def test_graduation_locked_without_owner(self):
        g = money_rules.graduation([5.0, -2.0] * 20)
        self.assertFalse(g["passed"])
        self.assertTrue(all(c["ok"] for c in g["checks"][:-1]))
        self.assertTrue(money_rules.graduation([5.0, -2.0] * 20, owner_yes=True)["passed"])

    def test_empty_record(self):
        self.assertFalse(money_rules.graduation([])["passed"])


class DecisionDeskTest(unittest.TestCase):
    def test_build_from_empty_desk(self):
        body = build({"departments": {}}, {"trades": [], "by_symbol": {}, "totals": {}}, {"history": []}, None)
        self.assertEqual(body["decisions"], [])
        self.assertEqual(body["graduation"]["real_money"], "locked")

    def test_build_with_prices(self):
        org = {"departments": {"trend": {"avg_vote": 0.4, "agents": 350, "active": 300},
                               "research": {"avg_vote": 0.5, "macro": 0.2}, "risk": {"avg_vote": 0.8}}}
        body = build(org, {"trades": [], "by_symbol": {}, "totals": {}},
                     {"history": [{"prices": {"BTC": 1.0, "ETH": 2.0}}]}, None)
        self.assertEqual({d["symbol"] for d in body["decisions"]}, {"BTC", "ETH"})
        self.assertIn(body["decisions"][0]["verdict"], ("ACT", "ESCALATE", "HOLD"))


if __name__ == "__main__":
    unittest.main()
