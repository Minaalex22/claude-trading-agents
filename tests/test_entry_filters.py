import unittest

from trading import entry_filters as ef
from trading import exits, loss_audit, training_report
from trading.agents import MeanReversion, Momentum


def feed(agent, prices):
    for p in prices:
        agent.observe(p)


class EntryFilterTests(unittest.TestCase):
    def setUp(self):
        self.addCleanup(setattr, ef, "ON", set(ef.ON))
        self.addCleanup(setattr, exits, "ENABLED", exits.ENABLED)
        ef.ON = set(ef.DEFAULT)
        ef.reset()
        loss_audit.reset()

    def test_features(self):
        t = Momentum("m", "AAPL", 10)
        feed(t, [100 + i for i in range(45)])  # a clean uptrend
        f = ef.features(t, 1.0)
        self.assertAlmostEqual(f["er"], 1.0)
        self.assertGreater(f["htf"], 0)
        self.assertLess(ef.features(t, -1.0)["htf"], 0)  # a short is against the higher timeframe

    def test_trend_trader_skips_a_choppy_market(self):
        t = Momentum("m", "AAPL", 10)
        feed(t, [100 + (1 if i % 2 else -1) for i in range(45)])  # zig-zag: efficiency near 0
        self.assertEqual(ef.check(t, 1.0, 1.0), 0.0)
        self.assertEqual(t.workspace["filtered"], {"regime": 1})

    def test_trend_trader_needs_the_higher_timeframe(self):
        t = Momentum("m", "AAPL", 10)
        feed(t, [100 + i for i in range(45)])
        self.assertEqual(ef.check(t, 1.0, 1.0), 1.0)  # with the trend: allowed
        self.assertEqual(ef.check(t, -1.0, 1.0), 0.0)  # against it: blocked
        self.assertEqual(t.workspace["filtered"], {"mtf": 1})

    def test_exits_are_never_blocked(self):
        t = Momentum("m", "AAPL", 10)
        feed(t, [100 + (1 if i % 2 else -1) for i in range(45)])
        t.position = 1.0
        self.assertEqual(ef.check(t, 0.0, 0.0), 0.0)  # closing
        self.assertEqual(ef.check(t, 0.5, 0.5), 0.5)  # trimming

    def test_reversion_is_not_filtered_by_default(self):
        r = MeanReversion("r", "AAPL", 10)
        feed(r, [100 + i for i in range(45)])
        self.assertEqual(ef.check(r, -1.0, 1.0), -1.0)

    def test_off(self):
        ef.configure("off")
        t = Momentum("m", "AAPL", 10)
        feed(t, [100 + (1 if i % 2 else -1) for i in range(45)])
        self.assertEqual(ef.check(t, 1.0, 1.0), 1.0)
        ef.configure("regime,meta,bogus")
        self.assertEqual(ef.ON, {"regime", "meta"})
        ef.configure(None)
        self.assertEqual(ef.ON, set(ef.DEFAULT))

    def test_meta_labeler_learns_and_reports_calibration(self):
        m = ef.MetaLabeler()
        for i in range(400):
            good = i % 2 == 0
            m.learn({"edge": 0.9 if good else 0.1, "er": 0.5}, won=good)
        self.assertTrue(m.ready())
        self.assertGreater(m.prob({"edge": 0.9, "er": 0.5}), m.prob({"edge": 0.1, "er": 0.5}))
        cal = m.calibration()
        self.assertEqual(cal["trades"], 400)
        self.assertLess(cal["brier"], 0.25)
        self.assertGreater(cal["brier_skill"], 0)


class LossAuditTests(unittest.TestCase):
    def setUp(self):
        loss_audit.reset()

    def test_first_wrong_step(self):
        base = {"er": 0.5, "htf": 1.0, "vol_ratio": 1.0, "edge": 0.5, "sure": 0.5, "agree": 0.2}
        self.assertEqual(loss_audit.bucket("trend", {}), "data")
        self.assertEqual(loss_audit.bucket("trend", {**base, "forced": True}), "forced")
        self.assertEqual(loss_audit.bucket("trend", {**base, "er": 0.1}), "wrong_regime")
        self.assertEqual(loss_audit.bucket("reversion", {**base, "er": 0.9}), "wrong_regime")
        self.assertEqual(loss_audit.bucket("trend", {**base, "htf": -0.5}), "against_trend")
        self.assertEqual(loss_audit.bucket("trend", {**base, "vol_ratio": 3.0}), "vol_spike")
        self.assertEqual(loss_audit.bucket("trend", {**base, "edge": 0.1, "sure": 0.1}), "weak_signal")
        self.assertEqual(loss_audit.bucket("trend", {**base, "agree": -0.5}), "against_view")
        self.assertEqual(loss_audit.bucket("trend", base), "noise")

    def test_closed_trades_feed_the_audit_and_the_training_report(self):
        t = Momentum("m", "AAPL", 10)
        feed(t, [100 + (1 if i % 2 else -1) for i in range(45)])
        t._trade(1.0)
        t.observe(90.0)
        t._trade(0.0)  # a loser
        self.assertEqual(loss_audit.TOTAL["losses"], 1)
        self.assertEqual(sum(t.workspace["loss_causes"].values()), 1)
        report = loss_audit.report()
        self.assertEqual(report["top"][0]["share_of_losses"], 1.0)
        org = {"departments": {"trend": {"name": "Trend", "trade_wins": 3, "trade_losses": 1,
                                         "loss_causes": t.workspace["loss_causes"], "filtered": {"regime": 4}}}}
        built = training_report.build(org)
        self.assertEqual(built["loss_audit"]["filtered"], {"regime": 4})
        self.assertEqual(built["loss_audit"]["top"][0]["share_of_trades"], 0.25)


if __name__ == "__main__":
    unittest.main()
