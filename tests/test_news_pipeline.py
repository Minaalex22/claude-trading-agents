import unittest

from trading.news_pipeline import DEEP_COST, CHEAP_COST, NewsPipeline, cheap_read, verify


class NewsPipelineTest(unittest.TestCase):
    def test_sure_headline_is_kept_and_billed_cheap(self):
        p = NewsPipeline(seed=1)
        rec = p.run({"symbol": "AAPL", "kind": "economy", "text": "AAPL: Earnings beats forecast, record buyback"}, {"news": .5, "chart": .4})
        self.assertEqual(rec["route"], "cheap")
        self.assertGreaterEqual(rec["triage"]["confidence"], p.gate)
        self.assertEqual(rec["call"], "BUY")

    def test_unknown_wording_escalates_and_flags_a_human(self):
        p = NewsPipeline(seed=1)
        rec = p.run({"symbol": "MSFT", "kind": "politics", "text": "MSFT: Something odd happened"}, None)
        self.assertEqual(rec["route"], "deep")
        self.assertTrue(rec["human"])
        self.assertIn("unknown", [t["tag"] for t in rec["deep"]["tags"]])

    def test_high_impact_news_is_always_overruled_to_the_deep_read(self):
        p = NewsPipeline(seed=1)
        rec = p.run({"symbol": "BTC", "kind": "economy", "text": "BTC: Central bank rate decision above forecast"}, {"news": .2, "chart": .3})
        self.assertEqual((rec["route"], rec["overrule"]), ("deep", "central bank"))

    def test_cache_skips_repeats_and_bill_adds_up(self):
        p = NewsPipeline(seed=1)
        h = [{"symbol": "AAPL", "text": "AAPL: Earnings beats forecast"}, {"symbol": "TSLA", "text": "TSLA: Something odd"}]
        p.feed(h); p.feed(h)
        s = p.snapshot()
        self.assertEqual(s["counts"]["read"], 2)
        self.assertAlmostEqual(s["cost"]["deep_only"], 2 * DEEP_COST, places=4)
        self.assertAlmostEqual(s["cost"]["cheap_only"], 2 * CHEAP_COST, places=4)
        self.assertLess(s["cost"]["hybrid"], s["cost"]["deep_only"])
        self.assertGreater(s["savings"], 0)

    def test_verifier_forces_escalation_without_evidence_or_market(self):
        h = {"symbol": "", "text": "beats forecast"}
        self.assertIn("no market named", verify(h, cheap_read(h)))

    def test_gate_stays_inside_its_limits(self):
        p = NewsPipeline(seed=2)
        for i in range(400):
            p.run({"symbol": "AAPL", "text": f"AAPL: upgrade and growth {i}"}, {"news": -.9, "chart": -.9})
        self.assertTrue(0.7 <= p.gate <= 0.9)


if __name__ == "__main__":
    unittest.main()
