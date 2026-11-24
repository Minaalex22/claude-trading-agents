"""Batch 8: real-price fills (the Heikin Ashi trap), the HA flip agent, and Option Big Flows."""

import unittest

from trading import agents, desk as desk_mod, execution_integrity as ei, hooks, option_flows as of
from trading.backtest import HistoricalMarket


class HeikinAshi(unittest.TestCase):
    def test_ha_formula_and_real_prices_kept_apart(self):
        bars = ei.heikin_ashi([{"open": 10, "high": 12, "low": 9, "close": 11},
                               {"open": 11, "high": 15, "low": 11, "close": 14}])
        self.assertAlmostEqual(bars[0].ha_close, 10.5)
        self.assertAlmostEqual(bars[0].ha_open, 10.5)
        self.assertAlmostEqual(bars[1].ha_open, 10.5)       # (prev ha_open + prev ha_close) / 2
        self.assertAlmostEqual(bars[1].ha_close, 12.75)
        self.assertEqual(bars[1].real_open, 11)             # the real open is untouched
        self.assertLess(bars[1].ha_open, bars[1].real_low)  # the HA open never traded: the trap

    def test_fill_pays_spread_and_slippage(self):
        self.assertGreater(ei.fill_price(1, 100.0), 100.0)
        self.assertLess(ei.fill_price(-1, 100.0), 100.0)

    def test_audit_flags_same_bar_and_synthetic(self):
        a = ei.FillAudit()
        a.check("BTC", 5, 6, 100.0, 100.0, 100.0, 1)
        self.assertTrue(a.report()["clean"])
        a.check("BTC", 5, 5, 100.0, 100.0, 100.0, 1)
        a.check("BTC", 5, 6, 90.0, 100.0, 101.0, 1)
        r = a.report()
        self.assertEqual((r["same_bar_fills"], r["synthetic_price_fills"]), (1, 1))

    def test_ha_flip_agent(self):
        a = agents.HeikinAshiFlip("t", "BTC", 8)
        a.trend_trust = lambda: 1.0
        a.prices.extend([100, 99, 98, 97, 96, 99, 102])
        self.assertGreater(a.decide(), 0)
        a.prices.extend([101, 98, 95])
        self.assertLess(a.decide(), 0)


class NextBarFills(unittest.TestCase):
    def _run(self, on: bool):
        from trading import engine
        import io
        bars = {"AAA": [(i * 3600.0, 100 + 10 * ((i // 40) % 2) + (i % 7) * 0.4) for i in range(400)]}
        m = HistoricalMarket(bars)
        env = {"AGENT_COUNT": "40", "TICKS": str(len(m)), "TICK_SECONDS": "0", "SEED": "1", "MEMORY": "off",
               "COACH": "off", "SYMBOLS": "AAA", "MARKET": "history", "EXEC_NEXT_BAR": "on" if on else "off",
               "READING_ROOM_CLAUDE": "off", "SIGNAL_THRESHOLD": "0.2"}
        return engine.run(env, stream=io.StringIO(), market=m)

    def test_next_bar_fills_are_clean_and_same_bar_fills_are_flagged(self):
        on, off = self._run(True), self._run(False)
        r_on, r_off = on.integrity.report(), off.integrity.report()
        self.assertGreater(r_on["fills"], 0)
        self.assertEqual(r_on["same_bar_fills"], 0)
        self.assertEqual(r_on["synthetic_price_fills"], 0)
        self.assertGreater(r_off["same_bar_fills"], 0)


class OptionFlows(unittest.TestCase):
    def setUp(self):
        of.FEED.clear()
        self.addCleanup(of.FEED.clear)

    def p(self, kind, action, prem=4e6, at=1000.0, key=True, under="SPX", dte=0):
        return of.FlowPrint(under, kind, action, prem, dte=dte, at_key_level=key, at=at)

    def test_put_polarity_is_inverted(self):
        self.assertEqual(of.decode(self.p("PUT", "BUY_TO_OPEN")).bias, "BEARISH")
        self.assertEqual(of.decode(self.p("PUT", "SELL_TO_OPEN")).bias, "BULLISH")
        self.assertEqual(of.decode(self.p("CALL", "BUY_TO_OPEN")).bias, "BULLISH")
        self.assertEqual(of.decode(self.p("CALL", "SELL_TO_OPEN")).bias, "BEARISH")

    def test_noise_ignored(self):
        self.assertEqual(of.decode(self.p("CALL", "BUY_TO_CLOSE")).bias, "IGNORE")
        self.assertEqual(of.decode(self.p("CALL", "BUY_TO_OPEN", prem=1.4e6)).bias, "IGNORE")
        self.assertEqual(of.decode(self.p("CALL", "BUY_TO_OPEN", dte=3)).bias, "IGNORE")

    def test_decay_spillover_veto_and_boost(self):
        of.FEED.ingest(self.p("PUT", "BUY_TO_OPEN", prem=6e6, at=0.0))
        b, key = of.FEED.bias("SPY", now=60.0)
        self.assertLess(b, -0.45)
        self.assertTrue(key)
        self.assertEqual(of.FEED.bias("BTC", now=60.0), (0.0, False))
        self.assertLess(abs(of.FEED.bias("SPY", now=35 * 60.0)[0]), abs(b))  # decays
        self.assertIsNotNone(of.FEED.against("QQQ", 1.0, now=60.0))         # a long fights the whale
        self.assertIsNone(of.FEED.against("QQQ", -1.0, now=60.0))
        self.assertAlmostEqual(of.FEED.boost("SPY", -0.8, now=60.0), -1.0)   # agreeing short gets the boost
        check = hooks.OrderCheck("SPY", 5.0, 0.0, 500.0, now=60.0)
        qty, notes = hooks.run_hooks(check)
        self.assertEqual(qty, 0.0)
        self.assertTrue(any("against_whale" in n for n in notes))

    def test_parse_pasted_lines(self):
        p = of.parse_line("CALL - BUY TO OPEN, Volume 150, Premium $3.41M, DTE 0, Filled, Green Diamond at KEY LEVEL")
        self.assertEqual((p.option_type, p.action, p.volume, p.dte), ("CALL", "BUY_TO_OPEN", 150, 0))
        self.assertAlmostEqual(p.premium, 3.41e6)
        self.assertTrue(p.at_key_level)
        q = of.parse_line("QQQ PUT SELL TO OPEN premium 4.2M dte 0 strike 520")
        self.assertEqual((q.underlying, q.option_type, q.action, q.strike), ("QQQ", "PUT", "SELL_TO_OPEN", 520.0))
        self.assertEqual(of.decode(q).bias, "BULLISH")
        self.assertIsNone(of.parse_line("nothing here"))


if __name__ == "__main__":
    unittest.main()
