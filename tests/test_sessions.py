"""The market clock: phases in each market's own time zone, sizing, trend trust and the session average."""

import unittest
from datetime import datetime, timedelta, timezone

from trading import hooks, sessions

NY = timezone(timedelta(hours=-5))  # New York in November (EST); no time-zone database needed on Windows
MELBOURNE = timezone(timedelta(hours=11))  # AEDT


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
        self.assertEqual(sessions.clock(ts(2026, 11, 18, 10, 0, MELBOURNE)).stock_phase, "closed")

    def test_crypto_phases(self):
        self.assertEqual(sessions.clock(ts(2026, 11, 18, 10, 15)).crypto_phase, "overlap")  # London and New York open
        self.assertEqual(sessions.clock(ts(2026, 11, 18, 9, 45)).crypto_phase, "ny_open")  # the banks' set-up
        self.assertEqual(sessions.clock(ts(2026, 11, 18, 3, 45)).crypto_phase, "london")
        self.assertEqual(sessions.clock(ts(2026, 11, 18, 20, 0)).crypto_phase, "asia")
        self.assertEqual(sessions.clock(ts(2026, 11, 21, 12, 0)).crypto_phase, "weekend")

    def test_daylight_saving(self):
        self.assertEqual(sessions._ny(datetime(2026, 7, 1, 13, 45, tzinfo=timezone.utc).timestamp()).hour, 9)  # EDT
        self.assertEqual(sessions._ny(datetime(2026, 12, 1, 14, 45, tzinfo=timezone.utc).timestamp()).hour, 9)  # EST
        self.assertEqual(sessions._london(datetime(2026, 7, 1, 7, 0, tzinfo=timezone.utc).timestamp()).hour, 8)  # BST

    def test_sizing_and_trust(self):
        c = sessions.clock(ts(2026, 11, 18, 9, 45))
        self.assertEqual((c.size("AAPL"), c.trend_trust("AAPL")), (0.5, 0.3))
        self.assertEqual((c.size("BTC"), c.trend_trust("BTC")), (0.5, 0.0))  # first 30 min after NY opens: wait
        later = sessions.clock(ts(2026, 11, 18, 10, 30))
        self.assertEqual((later.size("BTC"), later.trend_trust("BTC")), (1.0, 1.0))
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


class LosingStreakPauseTests(unittest.TestCase):
    def test_two_losses_pause_then_halve(self):
        j = hooks.DeskJournal()
        t0 = 1_800_000_000.0
        for i, (buy, sell) in enumerate([(100, 95), (100, 96)]):
            j.fill("AAPL", 0, 10, buy, i * 10, t0 + i * 60)
            j.fill("AAPL", 10, -10, sell, i * 10 + 1, t0 + i * 60 + 30)
        self.assertEqual(j.streak("AAPL"), 2)
        now = t0 + 60 + 30 + 120  # two minutes after the second loss
        self.assertTrue(j.cooling("AAPL", 25, now, 600))
        qty, notes = hooks.run_hooks(hooks.OrderCheck("AAPL", 10, 0, 100, streak=2, cooling=True))
        self.assertEqual(qty, 0.0)
        self.assertTrue(any("paused" in n for n in notes))
        self.assertFalse(j.cooling("AAPL", 25, now + 400, 600))  # past 5 minutes
        qty, _ = hooks.run_hooks(hooks.OrderCheck("AAPL", 10, 0, 100, streak=2, cooling=False))
        self.assertEqual(qty, 5.0)
        # getting out is never blocked
        self.assertEqual(hooks.run_hooks(hooks.OrderCheck("AAPL", -5, 10, 100, streak=2, cooling=True))[0], -5)

    def test_simulator_uses_ticks(self):
        j = hooks.DeskJournal()
        j.fill("AAPL", 0, 10, 100, 1)
        j.fill("AAPL", 10, -10, 90, 2)
        self.assertTrue(j.cooling("AAPL", 50, None, 600))
        self.assertFalse(j.cooling("AAPL", 700, None, 600))
