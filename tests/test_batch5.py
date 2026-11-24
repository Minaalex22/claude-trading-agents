"""Batch 5: sister-pair SMT, +1.2R half scale-out, stall stop, macro bucket cap, parent lock, meta-label gate."""

import types
import unittest
from collections import deque

from trading import exits, meta_gate, risk, smt_filter
from trading.broker import PaperBroker


def trader(prices, position=1.0, style="reversion"):
    a = types.SimpleNamespace(position=position, prices=deque(prices), style=style, window=50,
                              must_practice=False, workspace={})
    a.workspace["entry"] = prices[-1]
    exits.open_trade(a)
    return a


class ScaleStallTests(unittest.TestCase):
    def setUp(self):
        for k in ("TREND_TRAIL", "SCALE_OUT", "STALL_STOP", "BAR_SECONDS"):
            self.addCleanup(setattr, exits, k, getattr(exits, k))
        exits.TREND_TRAIL, exits.SCALE_OUT, exits.STALL_STOP, exits.BAR_SECONDS = False, True, True, 3600
        from trading import sniper
        self.addCleanup(setattr, sniper, "SNIPER", sniper.SNIPER)
        sniper.SNIPER = False  # sniper mode's own exit has its own test

    def test_banks_half_at_1_2r_then_rest_cannot_lose(self):
        a = trader([100.0, 100.1] * 30)
        r = a.workspace["exit_plan"]["r"]
        a.prices.append(100.1 + 1.25 * r)
        self.assertEqual(exits.check(a), "scale")
        self.assertAlmostEqual(a.workspace["exit_plan"]["stop_r"], exits.SCALE_STOP_R)
        a.prices.append(100.1 + 0.1 * r)  # falls back: closed above entry
        self.assertEqual(exits.check(a), "breakeven")

    def test_practice_trades_never_scale(self):
        a = trader([100.0, 100.1] * 30)
        a.must_practice = True
        a.prices.append(100.1 + 1.25 * a.workspace["exit_plan"]["r"])
        self.assertNotEqual(exits.check(a), "scale")

    def test_stall_after_8_bars_or_12_on_15m(self):
        a = trader([100.0, 100.1] * 30)
        for _ in range(7):
            self.assertIsNone(exits.check(a))
        self.assertEqual(exits.check(a), "stall")
        exits.BAR_SECONDS = 900
        b = trader([100.0, 100.1] * 30)
        self.assertEqual([exits.check(b) for _ in range(12)][-1], "stall")


class BucketTests(unittest.TestCase):
    def test_third_crypto_position_is_refused(self):
        broker = PaperBroker(100_000)
        broker.fill("BTC", 0.01, 50_000)
        broker.fill("ETH", 1.0, 3_000)
        rm = risk.RiskManager(risk.RiskLimits(), broker)
        prices = {"BTC": 50_000, "ETH": 3_000, "SOL": 150, "AAPL": 200}
        qty, why = rm.approve("SOL", 5, prices)
        self.assertEqual(qty, 0.0)
        self.assertIn("bucket cap", why)
        self.assertGreater(rm.approve("AAPL", 5, prices)[0], 0)  # another bucket
        self.assertGreater(rm.approve("BTC", 0.01, prices)[0], 0)  # adding to an open one is fine


class SmtTests(unittest.TestCase):
    def test_reversion_wants_divergence_and_trend_wants_confirmation(self):
        flat = [100.0 + (i % 3) * 0.1 for i in range(30)]
        low = flat + [99.0]
        high = flat + [101.0]
        hist = {"EUR": low, "GBP": low}
        self.assertIn("both", smt_filter.smt("EUR", "reversion", 1.0, hist))
        self.assertIsNone(smt_filter.smt("EUR", "reversion", 1.0, {"EUR": low, "GBP": flat + [100.1]}))
        self.assertIn("fake-out", smt_filter.smt("EUR", "trend", 1.0, {"EUR": high, "GBP": flat + [100.1]}))
        self.assertIsNone(smt_filter.smt("EUR", "trend", 1.0, {"EUR": high, "GBP": high}))
        self.assertIsNone(smt_filter.smt("JPY", "trend", 1.0, {"JPY": high}))  # no sister

    def test_parent_lock_blocks_buying_a_dip_in_a_parent_downtrend(self):
        down = [200.0 - i * 0.5 for i in range(240)]
        self.assertIn("falling knife", smt_filter.parent("X", "reversion", 1.0, down, 3600))
        self.assertIsNone(smt_filter.parent("X", "reversion", -1.0, down, 3600))
        self.assertIsNone(smt_filter.parent("X", "reversion", 1.0, down, 86_400))  # no parent on daily bars


class MetaGateTests(unittest.TestCase):
    def test_learns_walk_forward_and_vetoes_the_losing_kind(self):
        g = meta_gate.MetaGate()
        self.assertEqual(g.size([0.0, 1.0]), 1.0)  # nothing learned yet: passes
        price = 100.0
        for i in range(120):
            good = i % 2 == 0
            g.note("X", 1.0, price, 1.0, [1.0 if good else 0.0, 1.0])
            g.update({"X": price + (1.3 if good else -1.1)})
        self.assertGreaterEqual(len(g.rows), meta_gate.MIN_TRAIN)
        self.assertEqual(g.size([0.0, 1.0]), 0.0)
        self.assertEqual(g.size([1.0, 1.0]), meta_gate.SIZE_UP)


if __name__ == "__main__":
    unittest.main()
