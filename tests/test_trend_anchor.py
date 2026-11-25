"""Trend anchor (trading/trend_anchor.py) and the daily-bar fixes that let the desk trade real daily prices."""

import io
import math
import unittest

from trading import backtest, engine, trend_anchor

DAY = 86_400


class AnchorRuleTests(unittest.TestCase):
    def test_direction_needs_daily_bars_and_full_history(self):
        up = [100 + i for i in range(trend_anchor.LOOKBACK + 1)]
        self.assertEqual(trend_anchor.direction(up, DAY), 1)
        self.assertEqual(trend_anchor.direction(list(reversed(up)), DAY), -1)
        self.assertEqual(trend_anchor.direction(up[:-1], DAY), 0)  # not enough history yet
        self.assertEqual(trend_anchor.direction(up, 3600), 0)  # hourly bars: idle
        self.assertEqual(trend_anchor.direction(up, None), 0)  # simulator: idle

    def test_veto_and_base(self):
        self.assertEqual(trend_anchor.apply(-0.9, 1), trend_anchor.BASE)  # against the trend: dropped, base held
        self.assertEqual(trend_anchor.apply(0.0, -1), -trend_anchor.BASE)  # quiet agents: base held
        self.assertEqual(trend_anchor.apply(0.9, 1), 0.9)  # agents agreeing size it up
        self.assertEqual(trend_anchor.apply(0.3, 0), 0.3)  # no anchor: unchanged

    def test_bucket_scales_only_the_extra(self):
        b = trend_anchor.BASE
        self.assertAlmostEqual(trend_anchor.target(b, 1, 10_000, 0.25), b * 10_000)
        self.assertAlmostEqual(trend_anchor.target(1.0, -1, 10_000, 0.25), -(b + (1 - b) * 0.25) * 10_000)

    def test_hold_band(self):
        self.assertTrue(trend_anchor.hold(4_000, 4_500))
        self.assertFalse(trend_anchor.hold(2_000, 4_500))
        self.assertFalse(trend_anchor.hold(-4_000, 4_500))  # wrong side: trade
        self.assertFalse(trend_anchor.hold(0, 4_500))


def _replay(bars, **env):
    market = backtest.HistoricalMarket(bars, max_gap=5 * DAY)
    base = {"MARKET": "history", "FEE_BPS": "3", "TICKS": str(len(market)), "TICK_SECONDS": "0",
            "SYMBOLS": ",".join(bars), "MEMORY": "off", "COACH": "off", "READING_ROOM_CLAUDE": "off",
            "AGENT_COUNT": "60", "SEED": "7", "ORBIT_HEAT": "off"}
    base.update(env)
    return engine.run(base, stream=io.StringIO(), market=market)


class DailyDeskTests(unittest.TestCase):
    def setUp(self):
        t0 = 1_600_000_000
        # A volatile daily uptrend (3% daily wiggle, crypto-like), 320 bars: the anchor wakes up after 200.
        self.bars = {"BTC": [(t0 + i * DAY, 10_000 * math.exp(0.004 * i + 0.03 * math.sin(i * 1.7))) for i in range(320)]}

    def test_desk_holds_a_daily_uptrend_long(self):
        desk = _replay(self.bars)
        self.assertEqual(desk.anchor.get("BTC"), 1)
        self.assertGreater(desk.broker.position("BTC"), 0)
        self.assertIsNone(desk.clock)  # no session-of-day rules on daily bars

    def test_anchor_off_restores_old_behaviour(self):
        desk = _replay(self.bars, TREND_ANCHOR="off")
        self.assertEqual(desk.anchor.get("BTC"), 0)
        trend_anchor.ENABLED = True


if __name__ == "__main__":
    unittest.main()
