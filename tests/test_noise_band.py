"""Noise band (trading/noise_band.py): sessions, bands, entries, stops, flat by the close, and the desk hook."""

import io
import math
import unittest
from datetime import datetime, timedelta, timezone

from trading import backtest, engine, noise_band
from trading.sessions import _ny


def ny(y, m, d, hh, mm):
    """Unix time of a New York wall-clock time (no tz database: Windows CI has none)."""
    for off in (-5, -4):
        ts = datetime(y, m, d, hh, mm, tzinfo=timezone(timedelta(hours=off))).timestamp()
        if (_ny(ts).hour, _ny(ts).minute) == (hh, mm):
            return ts
    raise ValueError("not a New York time")


def sessions(days, path, start=datetime(2025, 3, 3)):
    """30-minute bars from 09:00 to 16:30 New York on `days` weekdays. path(day, k) -> price, k = bar index."""
    bars, d, n = [], start, 0
    while n < days:
        if d.weekday() < 5:
            for k in range(16):  # bar ends 09:00, 09:30, ..., 16:30
                bars.append((ny(d.year, d.month, d.day, 9, 0) + 1800 * k, path(n, k)))
            n += 1
        d += timedelta(days=1)
    return bars


def quiet(n, k):
    return 5000 * (1 + 0.001 * math.sin(n * 1.3 + k))  # ~0.1% wiggles: a narrow band


class SessionTests(unittest.TestCase):
    def test_us_session_clock(self):
        self.assertIsNone(noise_band.session_clock("SPX", ny(2025, 3, 4, 9, 30)))  # the open itself: pre-open price
        self.assertEqual(noise_band.session_clock("SPX", ny(2025, 3, 4, 10, 0)), ("2025-03-04", 30, 390))
        self.assertEqual(noise_band.session_clock("SPX", ny(2025, 3, 4, 16, 0))[1:], (390, 390))
        self.assertIsNone(noise_band.session_clock("SPX", ny(2025, 3, 4, 16, 30)))
        self.assertIsNone(noise_band.session_clock("SPX", ny(2025, 3, 8, 12, 0)))  # Saturday

    def test_gold_and_crypto_sessions(self):
        self.assertEqual(noise_band.session_clock("GOLD", ny(2025, 3, 4, 3, 30))[1], 30)
        midnight = datetime(2025, 3, 5, tzinfo=timezone.utc).timestamp()
        self.assertEqual(noise_band.session_clock("BTC", midnight), ("2025-03-04", 1440, 1440))

    def test_default_markets(self):
        self.assertFalse(noise_band.allowed("SPX"))  # helped alone, hurt next to gold: opt in
        self.assertTrue(noise_band.allowed("GOLD"))
        self.assertFalse(noise_band.allowed("BTC"))  # crypto lost on 2022-26 bars: off by default
        self.assertFalse(noise_band.allowed("EURUSD"))
        old = noise_band.ENABLED
        noise_band.ENABLED = False
        try:
            self.assertFalse(noise_band.allowed("SPX"))
        finally:
            noise_band.ENABLED, noise_band.MARKETS = old, noise_band.DEFAULT_MARKETS


class BandTests(unittest.TestCase):
    def feed(self, bars, symbol="SPX"):
        band = noise_band.NoiseBand(symbol)
        return band, [band.update(t, p) for t, p in bars]

    def test_warm_up_returns_none(self):
        band, out = self.feed(sessions(noise_band.MIN_SESSIONS - 1, quiet))
        self.assertTrue(all(v is None or v == 0 for v in out))
        self.assertIsNone(band.sigma(30))

    def test_breakout_up_then_stop_then_flat_by_close(self):
        def path(n, k):
            if n < 12:
                return quiet(n, k)
            # day 12: rallies 2% by 11:00, then collapses below the open at 13:00
            return 5000 * {2: 1.0, 3: 1.01, 4: 1.02, 5: 1.025, 6: 1.02, 7: 0.99, 8: 0.99}.get(k, 0.99)

        band, out = self.feed(sessions(13, path))
        last = out[-16:]
        self.assertIn(1, last[3:6])  # long after breaking the upper band
        self.assertEqual(last[8], 0)  # stopped when price fell back inside
        self.assertEqual(last[14], 0)  # 16:00 close: flat
        self.assertEqual(band.position, 0)

    def test_breakdown_goes_short(self):
        def path(n, k):
            return quiet(n, k) if n < 12 else 5000 * (1 - 0.004 * max(0, k - 1))

        _, out = self.feed(sessions(13, path))
        self.assertIn(-1, out[-16:])

    def test_vol_target_sizing(self):
        band = noise_band.NoiseBand("SPX")
        band.closes.extend([100 * (1 + 0.01 * (-1) ** i) for i in range(15)])  # ~2% daily swings
        self.assertLess(band.leverage(), 2.0)
        calm = noise_band.NoiseBand("SPX")
        calm.closes.extend([100 + 0.001 * i for i in range(15)])
        self.assertEqual(calm.leverage(), noise_band.LEV_CAP)
        self.assertEqual(calm.size(), 1.0)

    def test_replay_closes_every_trade_by_the_session_end(self):
        def path(n, k):
            return quiet(n, k) if n % 3 else 5000 * (1 + 0.004 * max(0, k - 1))

        r = noise_band.replay(sessions(40, path), "SPX", cost_bps=1.0)
        self.assertGreater(r["n"], 0)
        for t in r["trades"]:
            local = _ny(t["t"])
            self.assertLessEqual(local.hour * 60 + local.minute, 16 * 60)
        self.assertGreater(r["win_rate"], 0)


class DeskTests(unittest.TestCase):
    def run_desk(self, bars, **env):
        market = backtest.HistoricalMarket({"SPX": bars}, max_gap=4 * 86_400)
        base = {"MARKET": "history", "FEE_BPS": "1", "TICKS": str(len(market)), "TICK_SECONDS": "0",
                "SYMBOLS": "SPX", "MEMORY": "off", "COACH": "off", "READING_ROOM_CLAUDE": "off",
                "AGENT_COUNT": "40", "SEED": "7", "ORBIT_HEAT": "off"}
        base.update(env)
        try:
            return engine.run(base, stream=io.StringIO(), market=market)
        finally:
            noise_band.ENABLED, noise_band.MARKETS = True, noise_band.DEFAULT_MARKETS
            noise_band.BAND_MULT = noise_band.DEFAULT_MULT

    def test_intraday_desk_feeds_the_band_and_follows_a_breakout(self):
        def path(n, k):
            return quiet(n, k) if n < 14 else 5000 * (1 + 0.006 * max(0, k - 1))

        desk = self.run_desk(sessions(15, path), NOISE_BAND_MARKETS="indices")
        self.assertIn("SPX", desk.noise.bands)
        self.assertGreaterEqual(desk.noise.bands["SPX"].sessions_seen, 14)
        self.assertGreater(desk.noise_led, 0)  # the breakout led the desk

    def test_off_switch(self):
        desk = self.run_desk(sessions(15, quiet), NOISE_BAND="off", NOISE_BAND_MARKETS="all")
        self.assertEqual(desk.noise.bands, {})
        self.assertEqual(desk.noise_led, 0)

    def test_daily_bars_never_use_it(self):
        t0 = 1_600_000_000
        desk = self.run_desk([(t0 + i * 86_400, 5000 + i) for i in range(30)], NOISE_BAND_MARKETS="all")
        self.assertEqual(desk.noise.bands, {})


if __name__ == "__main__":
    unittest.main()
