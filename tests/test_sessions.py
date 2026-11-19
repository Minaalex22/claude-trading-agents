"""The market clock: phases in each market's own time zone, sizing, trend trust and the session average."""

import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from trading import hooks, sessions

NY = ZoneInfo("America/New_York")


def ts(y, mo, d, h, mi, tz=NY):
    return datetime(y, mo, d, h, mi, tzinfo=tz).timestamp()


class ClockTests(unittest.TestCase):
    def test_new_york_phases_whatever_our_time_zone(self):
        cases = [((9, 45), "opening_hour"), ((10, 45), "morning"), ((12, 30), "midday"), ((14, 30), "afternoon"),
                 ((15, 30), "power_hour"), ((8, 0), "closed"), ((16, 30), "closed")]
        for (h, m), phase in cases:
            self.assertEqual(sessions.clock(ts(2026, 11, 18, h, m)).stock_phase, phase, (h, m))
        self.assertEqual(sessions.clock(ts(2026, 11, 21, 12, 0)).stock_phase, "closed")  # Saturday
        # 10:00 in Melbourne on a Wednesday is still Tuesday evening in New York: closed
        self.assertEqual(sessions.clock(ts(2026, 11, 18, 10, 0, ZoneInfo("Australia/Melbourne"))).stock_phase, "closed")

    def test_crypto_phases(self):
        self.assertEqual(sessions.clock(ts(2026, 11, 18, 10, 0)).crypto_phase, "overlap")  # London and New York open
        self.assertEqual(sessions.clock(ts(2026, 11, 18, 3, 0)).crypto_phase, "london")
        self.assertEqual(sessions.clock(ts(2026, 11, 18, 20, 0)).crypto_phase, "asia")
        self.assertEqual(sessions.clock(ts(2026, 11, 21, 12, 0)).crypto_phase, "weekend")

    def test_sizing_and_trust(self):
        c = sessions.clock(ts(2026, 11, 18, 9, 45))
        self.assertEqual((c.size("AAPL"), c.trend_trust("AAPL")), (0.5, 0.3))
        self.assertEqual((c.size("BTC"), c.trend_trust("BTC")), (1.0, 1.0))
        self.assertIsNone(sessions.clock(None))

    def test_session_average_resets_each_day(self):
        avg = sessions.SessionAverages()
        avg.update(sessions.clock(ts(2026, 11, 18, 10, 0)), {"AAPL": 100.0})
        avg.update(sessions.clock(ts(2026, 11, 18, 11, 0)), {"AAPL": 110.0})
        self.assertEqual(avg.get("AAPL"), 105.0)
        avg.update(sessions.clock(ts(2026, 11, 18, 20, 0)), {"AAPL": 999.0})  # after the close: ignored
        avg.update(sessions.clock(ts(2026, 11, 19, 10, 0)), {"AAPL": 120.0})  # new session
        self.assertEqual(avg.get("AAPL"), 120.0)


class HookTests(unittest.TestCase):
    def check(self, clock, price=100.0, avg=None, qty=10.0):
        return hooks.run_hooks(hooks.OrderCheck("AAPL", qty, 0.0, price, clock=clock, session_avg=avg))

    def test_opening_hour_halves_new_risk(self):
        qty, notes = self.check(sessions.clock(ts(2026, 11, 18, 9, 45)))
        self.assertEqual(qty, 5.0)
        self.assertTrue(any("market_clock" in n for n in notes))

    def test_chasing_is_halved(self):
        qty, notes = self.check(sessions.clock(ts(2026, 11, 18, 10, 45)), price=103.0, avg=100.0)
        self.assertEqual(qty, 5.0)
        self.assertTrue(any("chasing" in n for n in notes))

    def test_no_clock_no_change(self):
        self.assertEqual(self.check(None), (10.0, []))


if __name__ == "__main__":
    unittest.main()
