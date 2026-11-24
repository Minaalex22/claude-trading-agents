"""Event guard (earnings + big news windows) and Sentinel's trade checker with its audit log."""
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from trading import event_guard as eg
from trading import hooks, trade_checker

NOW = datetime(2026, 11, 24, 14, 0, tzinfo=timezone.utc)


def ev(scope, mins, impact="HIGH", kind="macro", name=None):
    return eg.ScheduledEvent(scope, name or f"{scope} event", NOW + timedelta(minutes=mins), impact, kind)


class Guard(unittest.TestCase):
    def test_earnings_window_is_24h_before_to_1h_after(self):
        e = [ev("NVDA", 23 * 60, kind="earnings", name="NVDA earnings")]
        self.assertFalse(eg.check("NVDA", e, NOW)[0])
        self.assertTrue(eg.check("AAPL", e, NOW)[0])
        self.assertTrue(eg.check("NVDA", [ev("NVDA", 25 * 60, kind="earnings")], NOW)[0])
        self.assertFalse(eg.check("NVDA", [ev("NVDA", -50, kind="earnings")], NOW)[0])
        self.assertTrue(eg.check("NVDA", [ev("NVDA", -70, kind="earnings")], NOW)[0])

    def test_high_news_block_and_half_size(self):
        self.assertEqual(eg.check("AAPL", [ev("USD", 30)], NOW)[:2], (False, 0.0))
        self.assertEqual(eg.check("AAPL", [ev("USD", -20)], NOW)[:2], (False, 0.0))
        self.assertEqual(eg.check("AAPL", [ev("USD", 90)], NOW)[:2], (True, 0.5))
        self.assertEqual(eg.check("AAPL", [ev("USD", 200)], NOW)[:2], (True, 1.0))
        self.assertEqual(eg.check("AAPL", [ev("USD", 30, impact="MEDIUM")], NOW)[:2], (True, 1.0))
        self.assertEqual(eg.check("BTC", [ev("USD", 30)], NOW)[:2], (True, 1.0))         # crypto: off
        self.assertEqual(eg.check("AUDUSD", [ev("AUD", 10)], NOW)[:2], (False, 0.0))
        self.assertEqual(eg.check("EURJPY", [ev("USD", 10)], NOW)[:2], (True, 1.0))

    def test_strictest_event_wins(self):
        self.assertFalse(eg.check("AAPL", [ev("USD", 90), ev("USD", 20)], NOW)[0])

    def test_feeds_parse(self):
        day = datetime(2026, 11, 24, tzinfo=timezone.utc)
        rows = {"data": {"rows": [{"symbol": "NVDA", "time": "time-after-hours"}, {"symbol": "ZZZ", "time": "time-pre-market"}]}}
        out = eg.parse_nasdaq(rows, day, {"NVDA"})
        self.assertEqual((len(out), out[0].kind, out[0].at.hour), (1, "earnings", 21))       # 16:30 New York = 21:30 UTC
        macro = eg.parse_macro([{"title": "CPI m/m", "country": "USD", "date": "2026-11-24T08:30:00-05:00", "impact": "High"},
                                {"title": "Bank Holiday", "country": "JPY", "date": "2026-11-24T00:00:00-05:00", "impact": "Holiday"}])
        self.assertEqual((len(macro), macro[0].scope, macro[0].at.hour), (1, "USD", 13))

    def test_calendar_survives_a_dead_feed(self):
        def get(url):
            if "nasdaq" in url:
                raise OSError("down")
            return [{"title": "FOMC", "country": "USD", "date": "2026-11-24T14:00:00-05:00", "impact": "High"}]
        cal = eg.Calendar(["AAPL"], get=get, clock=lambda: NOW.timestamp())
        cal.refresh()
        self.assertIn("unreachable", cal.status["earnings"])
        self.assertEqual(len(cal.snapshot()), 1)
        self.assertEqual(cal.upcoming(NOW)[0]["scope"], "USD")

    def test_hook_blocks_new_risk_but_never_the_exit(self):
        events = [ev("USD", 20)]
        add = hooks.OrderCheck("AAPL", 10, 0, 100, events=events, now=NOW.timestamp())
        self.assertEqual(hooks.run_hooks(add)[0], 0.0)
        out = hooks.OrderCheck("AAPL", -10, 10, 100, events=events, now=NOW.timestamp())
        self.assertEqual(hooks.run_hooks(out)[0], -10)
        half = hooks.OrderCheck("AAPL", 10, 0, 100, events=[ev("USD", 100)], now=NOW.timestamp())
        self.assertEqual(hooks.run_hooks(half)[0], 5.0)


class Checker(unittest.TestCase):
    def test_pass_fail_and_audit_log(self):
        with tempfile.TemporaryDirectory() as d:
            c = trade_checker.TradeChecker(d, shard=1)
            ok = c.review(symbol="aapl", qty=5, price=100, desk_score=0.6, threshold=0.5, now=NOW.timestamp())
            bad = c.review(symbol="NVDA", qty=-3, price=200, desk_score=0.6, threshold=0.5, event_ok=False,
                           event_reason="earnings block: NVDA earnings in 300m", room_direction=0.8, room_confident=True)
            low = c.review(symbol="TSLA", qty=1, price=300, desk_score=0.3, threshold=0.5, costs_ok=False)
            self.assertTrue(ok.passed)
            self.assertFalse(bad.passed)
            self.assertIn("news and chart point the other way", bad.verdict)
            self.assertIn("below the threshold 0.50", low.verdict)
            lines = Path(d, "desk_audit.jsonl").read_text().splitlines()
            self.assertEqual([json.loads(x)["symbol"] for x in lines], ["AAPL", "NVDA", "TSLA"])
            log = trade_checker.read_log(d)
            self.assertEqual((log["passed"], log["failed"], log["rows"][0]["symbol"]), (1, 2, "TSLA"))
            self.assertIn("earnings block", log["reasons"])

    def test_desk_writes_the_audit_log_in_a_run(self):
        from trading import engine
        import io
        with tempfile.TemporaryDirectory() as d:
            desk = engine.run({"AGENT_COUNT": "60", "TICKS": "300", "TICK_SECONDS": "0", "EVENTS_DIR": d,
                               "MEMORY": "off", "COACH": "off", "READING_ROOM_CLAUDE": "off"}, stream=io.StringIO())
            self.assertIsNotNone(desk.checker)
            self.assertIsNone(desk.calendar)              # simulator: no real dates, no calendar
            log = trade_checker.read_log(d)
            self.assertEqual(log["passed"] + log["failed"], sum(desk.checker.counts.values()))


if __name__ == "__main__":
    unittest.main()
