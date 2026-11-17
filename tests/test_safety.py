"""Safety and real-data tests: daily loss stop, kill switch, stale prices, price spikes, real news feed."""

import io
import tempfile
import unittest
from pathlib import Path

from trading import engine
from trading.broker import PaperBroker
from trading.live_market import LiveMarket, fetch_quote
from trading.news_feed import LiveNewsFeed, match_symbol, parse_rss
from trading.reading_room import read_headline
from trading.risk import RiskLimits, RiskManager

DAY = 86_400.0


class Clock:
    def __init__(self, t=1_800_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


class RiskStopTests(unittest.TestCase):
    def setUp(self):
        self.broker = PaperBroker(cash=100_000.0, fee_bps=0.0)
        self.clock = Clock()

    def test_daily_loss_stops_new_risk_until_next_day(self):
        risk = RiskManager(RiskLimits(max_daily_loss=0.02), self.broker, clock=self.clock)
        self.broker.fill("AAPL", 100, 100.0)
        risk.update({"AAPL": 100.0})
        risk.update({"AAPL": 75.0})  # down $2,500 = 2.5% of the day's start
        self.assertEqual(risk.stop_reason, "daily loss limit hit")
        self.assertEqual(risk.approve("AAPL", 10, {"AAPL": 75.0}), (0.0, "daily loss limit hit"))
        self.assertEqual(risk.approve("AAPL", -10, {"AAPL": 75.0}), (-10, None))  # exits still allowed
        self.clock.t += DAY
        risk.update({"AAPL": 75.0})
        self.assertIsNone(risk.stop_reason)

    def test_kill_file_flattens_and_blocks(self):
        with tempfile.TemporaryDirectory() as d:
            kill = Path(d) / "KILL"
            risk = RiskManager(RiskLimits(), self.broker, kill_path=kill, clock=self.clock)
            risk.update({"AAPL": 100.0})
            self.assertFalse(risk.killed)
            kill.write_text("stop")
            risk.update({"AAPL": 100.0})
            self.assertTrue(risk.killed)
            self.assertEqual(risk.approve("AAPL", 5, {"AAPL": 100.0})[0], 0.0)
            kill.unlink()
            risk.update({"AAPL": 100.0})
            self.assertFalse(risk.killed)

    def test_stale_symbol_exits_only(self):
        risk = RiskManager(RiskLimits(), self.broker, clock=self.clock)
        self.broker.fill("AAPL", 10, 100.0)
        risk.frozen = {"AAPL"}
        self.assertEqual(risk.approve("AAPL", 5, {"AAPL": 100.0}), (0.0, "price feed stale"))
        self.assertEqual(risk.approve("AAPL", -5, {"AAPL": 100.0}), (-5, None))
        self.assertEqual(risk.approve("MSFT", 5, {"AAPL": 100.0, "MSFT": 100.0}), (5, None))

    def test_engine_kill_switch_closes_every_position(self):
        with tempfile.TemporaryDirectory() as d:
            env = {"TICKS": "150", "TICK_SECONDS": "0", "AGENT_COUNT": "200", "EVENTS_DIR": d, "MEMORY": "off"}
            desk = engine.run(env, stream=io.StringIO())
            self.assertTrue(any(desk.broker.positions.values()))
            (Path(d) / "KILL").write_text("stop")
            desk = engine.run(dict(env, TICKS="5"), stream=io.StringIO())
            self.assertTrue(desk.risk.killed)
            self.assertFalse(any(abs(q) > 1e-9 for q in desk.broker.positions.values()))


class LivePriceGuardTests(unittest.TestCase):
    def test_spike_needs_a_second_reading(self):
        quotes = iter([100.0, 150.0, 101.0, 150.0, 151.0])
        m = LiveMarket(["BTC"], fetch=lambda s: next(quotes), start_thread=False)
        seen = [m.tick()["BTC"]]
        for _ in range(4):
            m.poll_once()
            seen.append(m.tick()["BTC"])
        # 150 is held back, 101 is normal, a lone 150 is held again, then 151 confirms the jump.
        self.assertEqual(seen, [100.0, 100.0, 101.0, 101.0, 151.0])
        self.assertEqual(m.spikes, 2)

    def test_old_price_is_stale(self):
        clock = Clock()
        m = LiveMarket(["AAPL", "BTC"], fetch=lambda s: (100.0, clock.t - (3600 if s == "AAPL" else 0)),
                       start_thread=False, clock=clock)
        self.assertEqual(m.stale(), {"AAPL"})  # market closed: its last trade is an hour old

    def test_backup_sources(self):
        def get(url):
            if "coinbase" in url or "query1" in url:
                raise OSError("down")
            if "kraken" in url:
                return {"result": {"XXBTZUSD": {"c": ["64000.1", "1"]}}}
            return {"chart": {"result": [{"meta": {"regularMarketPrice": 190.0, "regularMarketTime": 5}}]}}
        self.assertEqual(fetch_quote("BTC", get)[0], 64000.1)
        self.assertEqual(fetch_quote("AAPL", get), (190.0, 5.0))


RSS = b"""<?xml version="1.0"?><rss><channel>
<item><title>Nvidia beats estimates as AI demand surges - Reuters</title><pubDate>Mon, 16 Nov 2026 10:00:00 GMT</pubDate></item>
<item><title>Nvidia beats estimates as AI demand surges - CNBC</title><pubDate>Mon, 16 Nov 2026 10:05:00 GMT</pubDate></item>
<item><title>Tesla shares plunge after recall</title><pubDate>Mon, 16 Nov 2026 09:00:00 GMT</pubDate></item>
<item><title>Old story about Apple</title><pubDate>Mon, 09 Nov 2026 09:00:00 GMT</pubDate></item>
<item><title>Markets wait for the Fed</title></item>
</channel></rss>"""


class LiveNewsTests(unittest.TestCase):
    def test_parse_and_match(self):
        self.assertEqual(len(parse_rss(RSS)), 5)
        self.assertEqual(match_symbol("Tesla shares plunge after recall", ["TSLA", "AAPL"]), "TSLA")
        self.assertIsNone(match_symbol("Apple and Microsoft rally", ["AAPL", "MSFT"]))  # two symbols: skip
        self.assertIsNone(match_symbol("Markets wait for the Fed", ["AAPL"]))

    def test_feed_dedupes_drops_old_and_hands_out_once(self):
        clock = Clock(1_794_830_400.0)  # 16 Nov 2026 12:00 UTC
        feed = LiveNewsFeed(["NVDA", "TSLA", "AAPL"], get=lambda url: RSS, start_thread=False, clock=clock)
        feed.poll_once()
        texts = [h["text"] for h in feed.tick({}, 1)]
        self.assertIn("NVDA: Nvidia beats estimates as AI demand surges - Reuters", texts)
        self.assertEqual(sum("beats estimates" in t for t in texts), 1)  # same story, one headline
        self.assertFalse(any("Old story" in t for t in texts))
        self.assertEqual(feed.tick({}, 2), [])
        self.assertEqual(feed.status()["feeds_failed"], 0)

    def test_dead_feed_does_not_stop_the_rest(self):
        def get(url):
            if "yahoo" in url:
                raise OSError("down")
            return RSS
        feed = LiveNewsFeed(["TSLA"], get=get, start_thread=False, clock=Clock(1_794_830_400.0))
        feed.poll_once()
        self.assertGreater(feed.feeds_failed, 0)
        self.assertTrue(feed.tick({}, 1))

    def test_reader_understands_real_headlines(self):
        self.assertGreater(read_headline("NVDA: Nvidia beats estimates as AI demand surges"), 0)
        self.assertLess(read_headline("TSLA: Tesla shares plunge after recall"), 0)
        self.assertIsNone(read_headline("AAPL: Apple unveils new iPhone"))
        self.assertLess(read_headline("NVDA: Inflation above forecast"), 0)  # the wire's wording still wins


if __name__ == "__main__":
    unittest.main()


class BacktestTests(unittest.TestCase):
    def test_aligns_markets_and_reports_both_halves(self):
        import math
        from trading import backtest
        hour = 3600.0
        stocks = [(i * hour + 1800, 100 + 5 * math.sin(i / 15)) for i in range(400)]
        crypto = [(i * hour, 200 + 9 * math.sin(i / 11)) for i in range(401)]
        m = backtest.HistoricalMarket({"AAPL": stocks, "BTC": crypto})
        self.assertEqual(len(m), 400)  # every stock bar finds the crypto close from half an hour before
        r = backtest.run({"AAPL": stocks, "BTC": crypto}, {"AGENT_COUNT": "200"})
        self.assertEqual(r["bars"], 400)
        for part in ("whole", "learning_half", "test_half", "buy_and_hold_test_half"):
            self.assertIn("sharpe", r[part])

    def test_metrics(self):
        from trading.backtest import metrics
        m = metrics([0, 86_400, 2 * 86_400], [100.0, 90.0, 99.0])
        self.assertEqual(m["max_drawdown_pct"], 10.0)
        self.assertEqual(m["return_pct"], -1.0)


class TwoStepLoginTests(unittest.TestCase):
    SECRET = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"  # RFC 6238 test key "12345678901234567890"

    def test_rfc6238_vectors(self):
        from trading.webauth import totp
        self.assertEqual(totp(self.SECRET, 59), "287082")
        self.assertEqual(totp(self.SECRET, 1111111109), "081804")

    def test_login_needs_password_and_fresh_code(self):
        from trading.webauth import WebAuth, totp
        clock = Clock(1111111109)
        auth = WebAuth("a-long-password!", "s", clock=clock, totp_secret=self.SECRET)
        self.assertTrue(auth.two_step)
        self.assertFalse(auth.check("a-long-password!", ""))
        self.assertFalse(auth.check("wrong-password!!", "081804"))
        self.assertTrue(auth.check("a-long-password!", "081 804"))
        self.assertFalse(auth.check("a-long-password!", "081804"))  # a code works once
        clock.t += 30
        self.assertTrue(auth.check("a-long-password!", totp(self.SECRET, clock.t)))

    def test_bad_secret_refused(self):
        from trading.webauth import WebAuth
        with self.assertRaises(SystemExit):
            WebAuth("a-long-password!", "s", totp_secret="not base32!")
