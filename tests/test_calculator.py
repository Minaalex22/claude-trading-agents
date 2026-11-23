import unittest

from trading import calculator as calc
from trading.markets import catalog


class CalculatorTest(unittest.TestCase):
    def test_every_product_has_a_spec(self):
        self.assertEqual({p.symbol for p in catalog.PRODUCTS}, set(calc.SPECS))
        self.assertEqual(len(calc.specs()), len(catalog.PRODUCTS))

    def test_eurusd_position_size(self):
        # $10,000, 0.5% risk = $50, 20 pip stop, $10/pip/lot -> 0.25 lots
        d = calc.calculate("EURUSD", 10000, 1.1000, 1.0980)
        self.assertEqual(d["side"], "buy")
        self.assertAlmostEqual(d["size"]["lots"], 0.25)
        self.assertAlmostEqual(d["stop_pips"], 20)
        self.assertAlmostEqual(d["pip_value_lot_usd"], 10)
        self.assertAlmostEqual(d["loss_usd"], 50)
        self.assertAlmostEqual(d["rr"], 2.4)              # blank target -> 2.4x stop
        self.assertAlmostEqual(d["win_usd"], 120)
        self.assertAlmostEqual(d["margin_usd"], 27500 / 30, places=2)

    def test_jpy_pip_value_converts_to_usd(self):
        d = calc.calculate("USDJPY", 10000, 150.00, 150.30, target=149.40)
        self.assertEqual(d["side"], "sell")
        self.assertAlmostEqual(d["pip_value_lot_usd"], 1000 / 150, places=3)
        self.assertAlmostEqual(d["rr"], 2.0)

    def test_gold_and_index_and_share(self):
        g = calc.calculate("GOLD", 10000, 2650, 2640)       # $10 stop, 100 oz lot -> $1000/lot -> 0.05 lots
        self.assertAlmostEqual(g["size"]["lots"], 0.05)
        self.assertAlmostEqual(g["pip_value_lot_usd"], 10)
        a = calc.calculate("ASX200", 10000, 8300, 8290, fx={"AUDUSD": 0.65})
        self.assertAlmostEqual(a["pip_value_lot_usd"], 0.65)
        s = calc.calculate("AAPL", 10000, 228, 226)          # $50 / $2 = 25 shares, whole shares only
        self.assertEqual(s["size"]["units"], 25)
        c = calc.calculate("BTC", 10000, 95000, 94000)
        self.assertAlmostEqual(c["size"]["units"], 0.05)

    def test_money_rules_checks(self):
        d = calc.calculate("EURUSD", 10000, 1.1, 1.098)      # $50 risk > lower of 1% and $10
        checks = {c["rule"]: c["ok"] for c in d["checks"]}
        self.assertFalse(checks["risk per trade"])
        self.assertTrue(checks["target 2.4x the stop"])
        self.assertAlmostEqual(d["rule_cap_usd"], 10)
        self.assertAlmostEqual(d["rule_size_lots"], 0.05)
        ok = calc.calculate("EURUSD", 1000, 1.1, 1.098, risk_pct=0.5)   # $5 risk
        self.assertTrue({c["rule"]: c["ok"] for c in ok["checks"]}["risk per trade"])
        self.assertEqual(d["real_money"], "locked")

    def test_half_kelly(self):
        d = calc.calculate("EURUSD", 10000, 1.1, 1.098, win_rate=58)
        self.assertAlmostEqual(d["kelly"]["full_pct"], round((0.58 - 0.42 / 2.4) * 100, 2))
        self.assertAlmostEqual(d["kelly"]["half_pct"], round((0.58 - 0.42 / 2.4) * 50, 2))
        bad = calc.calculate("EURUSD", 10000, 1.1, 1.098, target=1.101, win_rate=30)
        self.assertFalse(bad["kelly"]["edge"])
        self.assertEqual(bad["kelly"]["half_pct"], 0)

    def test_too_small_and_bad_input(self):
        self.assertTrue(calc.calculate("EURUSD", 10, 1.1, 1.0)["size"]["too_small"])
        with self.assertRaises(ValueError):
            calc.calculate("EURUSD", 10000, 1.1, 1.1)
        with self.assertRaises(ValueError):
            calc.calculate("EURUSD", 10000, 1.1, 1.09, target=1.08)   # target on the wrong side
        with self.assertRaises(ValueError):
            calc.calculate("EURUSD", "abc", 1.1, 1.09)
        with self.assertRaises(KeyError):
            calc.calculate("NOPE", 10000, 1.1, 1.09)


if __name__ == "__main__":
    unittest.main()
