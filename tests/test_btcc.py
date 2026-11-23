"""BTCC bridge: read-only BTCC calls, practice desk rules, news parsing, and the page behind the login."""

import hashlib
import http.client
import json
import tempfile
import threading
import unittest
import urllib.parse
from http.server import ThreadingHTTPServer
from pathlib import Path

from trading import btcc as B
from trading import platforms as P
from trading.dashboard import STATIC, EventHub, make_handler
from trading.webauth import COOKIE, WebAuth

ENV = {"BTCC_API_KEY": "key-1", "BTCC_SECRET_KEY": "sekrit", "BTCC_USERNAME": "me", "BTCC_PASSWORD": "pw-123",
       "BTCC_API_BASE_URL": "https://api.example-btcc.test"}
PASSWORD = "correct-horse-battery-staple"


class SignTests(unittest.TestCase):
    def test_signature_is_md5_of_sorted_params_with_secret(self):
        want = hashlib.md5(b"a=1&b=x%20y&secret_key=s").hexdigest()  # noqa: S324
        self.assertEqual(B.sign({"b": "x y", "a": 1}, "s"), want)


class ClientTests(unittest.TestCase):
    def fake(self):
        calls = []

        def http(method, url, body):
            calls.append((method, url, body))
            if "/v1/user/login" in url:
                return {"code": 0, "token": "tok", "account": {"id": 42}}
            if "/v1/account/positionlist" in url:
                return {"code": 0, "positions": [{"symbol": "BTCUSDT", "direction": 1, "volume": "0.01",
                                                  "open_price": "90000", "multiple": 3, "profit": "12.5"}]}
            raise AssertionError(url)
        return http, calls

    def test_needs_keys_and_https(self):
        self.assertFalse(B.configured({}))
        with self.assertRaises(PermissionError):
            B.BtccClient({})
        with self.assertRaises(ValueError):
            B.BtccClient({**ENV, "BTCC_API_BASE_URL": "http://plain.test"})

    def test_reads_positions_with_login_only(self):
        http, calls = self.fake()
        rows = B.BtccClient(ENV, http).positions()
        self.assertEqual(rows[0]["symbol"], "BTCUSDT")
        self.assertEqual(rows[0]["side"], "long")
        self.assertEqual(rows[0]["pnl"], 12.5)
        self.assertEqual([c[0] for c in calls], ["POST", "GET"])
        self.assertIn("sign=", calls[1][1])

    def test_anything_but_reading_is_refused_before_the_network(self):
        http, calls = self.fake()
        client = B.BtccClient(ENV, http)
        for method, path in (("POST", "/v1/account/openposition"), ("POST", "/v1/account/withdraw")):
            with self.assertRaises(B.Refused):
                client._call(method, path, {})
        self.assertEqual(calls, [])

    def test_never_shows_secrets(self):
        http, _ = self.fake()
        self.assertNotIn("sekrit", repr(B.BtccClient(ENV, http)))
        def boom(method, url, body):
            raise OSError("down")
        out = B.live_account(ENV, boom)
        self.assertFalse(out["connected"])
        self.assertNotIn("sekrit", json.dumps(out))
        self.assertNotIn("pw-123", json.dumps(out))

    def test_paused_api_is_explained_without_keys(self):
        out = B.live_account({})
        self.assertEqual(out["api"], "paused")
        self.assertIn("BTCC_API_KEY", out["needs"])
        self.assertIn("13 Feb 2026", out["status"])


class PracticeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.px = {"BTC": 100_000.0, "ETH": 3_000.0, "SOL": 150.0}
        self.desk = B.PracticeDesk(self.tmp.name, price=lambda s: self.px[s])
        B.MARK_TTL_SAVED = B.MARK_TTL
        B.MARK_TTL = 0  # read the price fresh each time in tests

    def tearDown(self):
        B.MARK_TTL = B.MARK_TTL_SAVED
        self.tmp.cleanup()

    def test_rules(self):
        with self.assertRaises(B.Refused):
            self.desk.open("BTC", "long", 100, 3, 95_000, live=True)
        with self.assertRaises(ValueError):
            self.desk.open("BTC", "long", 100, 10, 95_000)  # leverage above 5x
        with self.assertRaises(ValueError):
            self.desk.open("BTC", "long", 100, 3, 101_000)  # stop on the wrong side
        with self.assertRaises(ValueError):
            self.desk.open("DOGE", "long", 100, 3, 1)
        with self.assertRaises(ValueError):
            self.desk.open("BTC", "long", 50_000, 3, 95_000)  # more than the practice cash

    def test_long_profit_and_close(self):
        pos = self.desk.open("BTCUSDT", "long", 100, 5, 95_000, 110_000)
        self.px["BTC"] = 102_000.0  # +2% x5 = +10 USDT
        snap = self.desk.snapshot()
        self.assertAlmostEqual(snap["open"][0]["pnl"], 10.0, places=2)
        done = self.desk.close(pos["id"])
        self.assertAlmostEqual(done["pnl"], 9.4, places=2)  # +10 less two 0.3 fees
        self.assertEqual(self.desk.snapshot()["win_rate"], 100.0)

    def test_stop_loss_closes_short(self):
        self.desk.open("ETH", "short", 100, 2, 3_100)
        self.px["ETH"] = 3_150.0
        snap = self.desk.snapshot()
        self.assertEqual(snap["open"], [])
        self.assertEqual(snap["closed"][0]["reason"], "stop loss")
        self.assertLess(snap["closed"][0]["pnl"], 0)

    def test_survives_restart(self):
        self.desk.open("SOL", "long", 100, 1, 140)
        again = B.PracticeDesk(self.tmp.name, price=lambda s: self.px[s])
        self.assertEqual(len(again.state["open"]), 1)


class NewsTests(unittest.TestCase):
    def test_parse_and_cache(self):
        xml = (b'<rss><channel><item><title>BTCC lists NEW token</title><link>https://news.example/a</link>'
               b'<pubDate>Mon, 23 Nov 2026 01:00:00 GMT</pubDate><source url="https://btcc.com">BTCC</source></item>'
               b'<item><title>bad link</title><link>javascript:alert(1)</link></item></channel></rss>')
        items = B.parse_rss(xml)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["source"], "BTCC")
        B._news.update(at=0.0, items=[])
        got = B.news(get=lambda url: xml, now=lambda: 1000.0)
        self.assertEqual(got[0]["title"], "BTCC lists NEW token")
        self.assertEqual(B.news(get=lambda url: b"", now=lambda: 1100.0), got)  # cached
        self.assertEqual(B.parse_rss(b"not xml"), [])


class PlatformTests(unittest.TestCase):
    def test_listed_paper_only_and_locked(self):
        row = next(r for r in P.status({"BTCC_MODE": "live"}) if r["key"] == "btcc")
        self.assertEqual(row["mode"], "paper")
        self.assertTrue(row["live_locked"])
        self.assertIn("BTCC_SECRET_KEY", P.SECRET_NAMES)
        with self.assertRaises(P.LiveTradingLocked):
            P.connect("btcc").place_order("BTC", "buy", 1, live=True)


class PageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        Path(self.tmp.name, "events-0.jsonl").write_text('{"ts":1,"shard":0,"type":"start","agents":250}\n')
        hub = EventHub(Path(self.tmp.name))
        hub.poll_files()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(hub, WebAuth(PASSWORD, "secret")))
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self):
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
        resp, _ = self.request("GET", "/btcc")
        self.assertEqual((resp.status, resp.getheader("Location")), (303, "/login"))
        resp, _ = self.request("GET", "/api/btcc")
        self.assertEqual(resp.status, 401)
        resp, _ = self.request("POST", "/login", urllib.parse.urlencode({"password": PASSWORD}),
                               {"Content-Type": "application/x-www-form-urlencoded", "X-Forwarded-For": "7.7.7.7"})
        cookie = resp.getheader("Set-Cookie").split(";")[0].split("=", 1)[1]
        h = {"Cookie": f"{COOKIE}={cookie}"}
        resp, body = self.request("GET", "/btcc", headers=h)
        self.assertEqual(resp.status, 200)
        self.assertIn(b"/btcc.js", body)
        order = json.dumps({"symbol": "BTC", "side": "long", "margin": 10, "leverage": 2, "stop_loss": 1,
                            "live": True})
        resp, body = self.request("POST", "/api/btcc/open", order, {**h, "Content-Type": "application/json"})
        self.assertEqual(resp.status, 403, body)

    def test_assets_registered(self):
        self.assertIn("/btcc.js", STATIC)
        self.assertIn("/btcc.css", STATIC)


if __name__ == "__main__":
    unittest.main()
