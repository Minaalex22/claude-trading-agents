"""Mitrade bridge: guest price feed parsing and safety, practice desk at bid/ask, and the page behind the login."""

import http.client
import json
import tempfile
import threading
import unittest
import urllib.parse
from http.server import ThreadingHTTPServer
from pathlib import Path

from trading import mitrade as M
from trading import platforms as P
from trading.dashboard import STATIC, EventHub, make_handler
from trading.webauth import COOKIE, WebAuth

PASSWORD = "correct-horse-battery-staple"
ROWS = [
    {"symbol": "XAUUSD", "buyPrice": 3649.16, "sellPrice": 3648.66, "dailyHigh": 3678.1, "dailyLow": 3619.41,
     "open": 3637.33, "lastClose": 3634.17, "dailyChangeInValue": 14.49, "dailyChangeInPercentage": 0.003988,
     "type": "COMMODITIES", "updateDatetime": "2026-11-24T02:43:11"},
    {"symbol": "BTCUSD", "buyPrice": 60397.67, "sellPrice": 60276.99, "type": "CRYPTO"},
    {"symbol": "EURUSD", "buyPrice": None, "sellPrice": 1.09},
]


class FakeHttp:
    def __init__(self, rows=ROWS):
        self.rows, self.calls = rows, []

    def __call__(self, url):
        self.calls.append(url)
        if "/instruments/quotations/all" in url:
            return {"success": True, "value": self.rows}
        if "/instruments/trend" in url:
            return {"success": True, "value": [{"symbol": "XAUUSD", "data": [1, 2, 3]}]}
        raise AssertionError(url)


class FeedTests(unittest.TestCase):
    def test_parses_bid_ask_and_skips_broken_rows(self):
        feed = M.Feed(FakeHttp(), now=lambda: 1000.0)
        q = feed.quotes()
        self.assertEqual(set(q), {"XAUUSD", "BTCUSD"})
        self.assertEqual((q["XAUUSD"]["bid"], q["XAUUSD"]["ask"]), (3648.66, 3649.16))
        self.assertAlmostEqual(q["XAUUSD"]["spread"], 0.5, places=3)
        self.assertEqual(q["XAUUSD"]["change_pct"], 0.4)
        self.assertTrue(feed.status()["connected"])
        self.assertEqual(feed.trend()["XAUUSD"], [1.0, 2.0, 3.0])

    def test_caches_so_mitrade_is_not_hammered(self):
        t = [1000.0]
        fake = FakeHttp()
        feed = M.Feed(fake, now=lambda: t[0])
        feed.quotes(); feed.quotes(); feed.quotes()
        self.assertEqual(len(fake.calls), 1)
        t[0] += M.QUOTE_TTL + 1
        feed.quotes()
        self.assertEqual(len(fake.calls), 2)

    def test_one_request_for_all_symbols_to_the_guest_host(self):
        fake = FakeHttp()
        M.Feed(fake, now=lambda: 1.0).quotes()
        self.assertTrue(fake.calls[0].startswith("https://www.mizoneservice.com/api/v3/instruments/quotations/all?"))
        self.assertIn("XAUUSD,", fake.calls[0])

    def test_only_price_paths(self):
        fake = FakeHttp()
        for path in ("/trade/order", "/account/login", "/user/info"):
            with self.assertRaises(M.Refused):
                M._get(path, {}, fake)
        self.assertEqual(fake.calls, [])
        self.assertNotIn("password", json.dumps(M.HEADERS).lower())
        self.assertNotIn("authorization", json.dumps(M.HEADERS).lower())

    def test_down_feed_is_reported_not_raised(self):
        def boom(url):
            raise OSError("blocked")
        feed = M.Feed(boom, now=lambda: 1.0)
        self.assertEqual(feed.quotes(), {})
        st = feed.status()
        self.assertFalse(st["connected"])
        self.assertIn("OSError", st["error"])
        bad = M.Feed(lambda url: {"success": False, "message": "header param error"}, now=lambda: 1.0)
        bad.quotes()
        self.assertIn("header param error", bad.status()["error"])


class DeskTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.rows = [dict(r) for r in ROWS[:2]]
        self.t = [1000.0]
        self.feed = M.Feed(lambda url: {"success": True, "value": self.rows}, now=lambda: self.t[0])
        self.desk = M.PracticeDesk(self.tmp.name, self.feed)

    def tearDown(self):
        self.tmp.cleanup()

    def move(self, sym, bid, ask):
        r = next(r for r in self.rows if r["symbol"] == sym)
        r.update(sellPrice=bid, buyPrice=ask)
        self.t[0] += M.QUOTE_TTL + 1

    def test_rules(self):
        with self.assertRaises(M.Refused):
            self.desk.open("XAUUSD", "buy", 100, 2, 3600, live=True)
        with self.assertRaises(ValueError):
            self.desk.open("BTCUSD", "buy", 100, 3, 59000)  # crypto capped at 2x (ASIC)
        with self.assertRaises(ValueError):
            self.desk.open("XAUUSD", "buy", 100, 6, 3600)  # our 5x rule
        with self.assertRaises(ValueError):
            self.desk.open("XAUUSD", "buy", 100, 2, 3700)  # stop on the wrong side
        with self.assertRaises(ValueError):
            self.desk.open("DOGE", "buy", 100, 1, 1)
        with self.assertRaises(ValueError):
            self.desk.open("XAUUSD", "buy", 50_000, 2, 3600)

    def test_buy_fills_at_ask_and_closes_at_bid(self):
        pos = self.desk.open("XAUUSD", "buy", 1000, 5, 3600)
        self.assertEqual(pos["entry"], 3649.16)
        snap = self.desk.snapshot()
        self.assertLess(snap["open"][0]["pnl"], 0)  # the spread costs straight away
        self.move("XAUUSD", 3685.15, 3685.65)
        done = self.desk.close(pos["id"])
        self.assertEqual(done["exit"], 3685.15)
        self.assertAlmostEqual(done["pnl"], 1000 * 5 * (3685.15 - 3649.16) / 3649.16, places=2)
        self.assertEqual(self.desk.snapshot()["win_rate"], 100.0)

    def test_stop_loss_closes_sell(self):
        self.desk.open("XAUUSD", "sell", 100, 2, 3660)
        self.move("XAUUSD", 3661.0, 3661.5)
        snap = self.desk.snapshot()
        self.assertEqual(snap["open"], [])
        self.assertEqual(snap["closed"][0]["reason"], "stop loss")
        self.assertLess(snap["closed"][0]["pnl"], 0)

    def test_survives_restart(self):
        self.desk.open("XAUUSD", "buy", 100, 1, 3600)
        self.assertEqual(len(M.PracticeDesk(self.tmp.name, self.feed).state["open"]), 1)


class PlatformTests(unittest.TestCase):
    def test_listed_paper_only_and_locked(self):
        row = next(r for r in P.status({"MITRADE_MODE": "live"}) if r["key"] == "mitrade")
        self.assertEqual(row["mode"], "paper")
        self.assertTrue(row["live_locked"])
        with self.assertRaises(P.LiveTradingLocked):
            P.connect("mitrade").place_order("XAUUSD", "buy", 1, live=True)


class PageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        Path(self.tmp.name, "events-0.jsonl").write_text('{"ts":1,"shard":0,"type":"start","agents":250}\n')
        hub = EventHub(Path(self.tmp.name))
        hub.poll_files()
        self.saved = M.FEED
        M.FEED = M.Feed(FakeHttp(), now=lambda: 1.0)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(hub, WebAuth(PASSWORD, "secret")))
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self):
        M.FEED = self.saved
        self.server.shutdown()
        self.server.server_close()
        self.tmp.cleanup()

    def request(self, method, path, body=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        conn.request(method, path, body=body, headers={"Host": "ats.example.com", **(headers or {})})
        resp = conn.getresponse()
        data = resp.read()
        conn.close()
        return resp, data

    def test_page_needs_login_and_live_orders_are_refused(self):
        resp, _ = self.request("GET", "/mitrade")
        self.assertEqual((resp.status, resp.getheader("Location")), (303, "/login"))
        resp, _ = self.request("GET", "/api/mitrade")
        self.assertEqual(resp.status, 401)
        resp, _ = self.request("POST", "/login", urllib.parse.urlencode({"password": PASSWORD}),
                               {"Content-Type": "application/x-www-form-urlencoded", "X-Forwarded-For": "7.7.7.8"})
        cookie = resp.getheader("Set-Cookie").split(";")[0].split("=", 1)[1]
        h = {"Cookie": f"{COOKIE}={cookie}"}
        resp, body = self.request("GET", "/mitrade", headers=h)
        self.assertEqual(resp.status, 200)
        self.assertIn(b"/mitrade.js", body)
        resp, body = self.request("GET", "/api/mitrade", headers=h)
        self.assertEqual(resp.status, 200)
        self.assertIn("XAUUSD", json.loads(body)["quotes"])
        order = json.dumps({"symbol": "XAUUSD", "side": "buy", "margin": 10, "leverage": 2, "stop_loss": 1,
                            "live": True})
        resp, body = self.request("POST", "/api/mitrade/open", order, {**h, "Content-Type": "application/json"})
        self.assertEqual(resp.status, 403, body)

    def test_assets_registered(self):
        self.assertIn("/mitrade.js", STATIC)


if __name__ == "__main__":
    unittest.main()
