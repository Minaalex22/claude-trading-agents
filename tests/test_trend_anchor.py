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


class ForexRuleTests(unittest.TestCase):
    def tearDown(self):
        trend_anchor.FX_MODE = "off"

    def test_all_forex_benched_by_default(self):
        self.assertEqual(trend_anchor.FX_MODE, "off")
        self.assertEqual(trend_anchor.fx_gate("USDJPY", -1, 0.0, [1.0] * 30), 0)
        self.assertEqual(trend_anchor.fx_gate("GOLD", 1, 0.0, [1.0] * 30), 1)

    def test_majors_mode_keeps_usdjpy(self):
        trend_anchor.FX_MODE = "majors"
        self.assertEqual(trend_anchor.fx_gate("EURUSD", 1, 0.0, [1.0] * 30), 0)
        self.assertEqual(trend_anchor.fx_gate("USDJPY", -1, 0.0, [1.0] * 30), -1)
        self.assertEqual(trend_anchor.fx_gate("GOLD", 1, 0.0, [1.0] * 30), 1)  # not forex: untouched

    def test_all_forex_off(self):
        trend_anchor.FX_MODE = "off"
        self.assertEqual(trend_anchor.fx_gate("USDJPY", 1, 0.0, [1.0] * 30), 0)

    def test_adx_gate(self):
        trend_anchor.FX_MODE = "adx"
        trending = [1.0 + 0.001 * i for i in range(60)]
        choppy = [1.0 + 0.001 * (i % 2) for i in range(60)]
        self.assertGreater(trend_anchor.adx(trending), 25)
        self.assertLess(trend_anchor.adx(choppy), 25)
        self.assertEqual(trend_anchor.fx_gate("GBPUSD", 1, 0.0, trending), 1)
        self.assertEqual(trend_anchor.fx_gate("GBPUSD", 1, 0.0, choppy), 0)
        self.assertEqual(trend_anchor.fx_gate("GBPUSD", 1, 5.0, choppy), 1)  # already holding: keep it

    def test_keep(self):
        trend_anchor.FX_MODE = "keep"
        self.assertEqual(trend_anchor.fx_gate("USDJPY", -1, 0.0, [1.0] * 30), -1)


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


class IntradayAnchorTests(unittest.TestCase):
    def test_seeded_daily_history_makes_the_anchor_live_on_hourly_bars(self):
        t0 = 1_600_000_000
        bars = {"BTC": [(t0 + i * 3600, 20_000 + i) for i in range(5)]}
        desk = _replay(bars, TICKS="1")
        desk.seed_daily({"BTC": [10_000 + 10 * i for i in range(250)]})
        self.assertTrue(desk.anchor_status()["BTC"]["ready"])
        for i in range(5):
            desk.step({"BTC": 20_000.0 + i}, now=t0 + i * 3600)
        self.assertEqual(desk.anchor["BTC"], 1)
        self.assertIsNotNone(desk.clock)  # hourly bars keep their session rules

    def test_unseeded_hourly_desk_stays_idle(self):
        t0 = 1_600_000_000
        desk = _replay({"BTC": [(t0 + i * 3600, 20_000 + i) for i in range(30)]})
        self.assertFalse(desk.anchor_status()["BTC"]["ready"])
        self.assertEqual(desk.anchor.get("BTC", 0), 0)

    def test_daily_close_appended_on_new_day(self):
        t0 = 1_600_000_000 - 1_600_000_000 % DAY
        desk = _replay({"BTC": [(t0 + i * 3600, 20_000 + i) for i in range(3)]}, TICKS="1")
        desk.step({"BTC": 100.0}, now=t0 + 3600)
        desk.step({"BTC": 101.0}, now=t0 + 7200)
        desk.step({"BTC": 105.0}, now=t0 + DAY + 3600)
        self.assertEqual(list(desk.daily_hist["BTC"]), [101.0])


class WinRateIdeaKnobTests(unittest.TestCase):
    """mina's 25 Nov ideas stay as off-by-default test knobs (they did worse in the replay)."""

    def tearDown(self):
        trend_anchor.PULLBACK = "off"

    def test_off_by_default(self):
        self.assertEqual((trend_anchor.PULLBACK, trend_anchor.TRIM_R, trend_anchor.TRAIL_ATR), ("off", 0.0, 0.0))

    def test_pullback_rules(self):
        extended = [100 + i for i in range(60)]  # straight up: far above the 20-EMA, RSI(2) 100
        dipped = extended[:-2] + [extended[-3] - 3, extended[-3] - 6]
        trend_anchor.PULLBACK = "ema"
        self.assertFalse(trend_anchor.pulled_back(1, extended))
        trend_anchor.PULLBACK = "rsi2"
        self.assertFalse(trend_anchor.pulled_back(1, extended))
        self.assertTrue(trend_anchor.pulled_back(1, dipped))
        self.assertEqual(trend_anchor.rsi2(dipped), 0.0)
