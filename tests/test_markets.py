"""Markets section (trading/markets/): analysis maths, the 25-product board and the web routes."""
import http.client
import json
import os
import tempfile
import threading
import unittest
import urllib.parse
from http.server import ThreadingHTTPServer
from pathlib import Path

os.environ["MARKETS_FEED"] = "sim"  # never touch the network in tests

from trading import markets  # noqa: E402
from trading.dashboard import EventHub, make_handler  # noqa: E402
from trading.markets import catalog, changes, feeds, flow, indicators as ind, outlook, volume_profile  # noqa: E402
from trading.webauth import COOKIE, WebAuth  # noqa: E402


class Catalog(unittest.TestCase):
    def test_25_products_five_of_each_kind(self):
        self.assertEqual(len(catalog.PRODUCTS), 25)
        self.assertEqual(len(catalog.BY_SYMBOL), 25)
        for k in catalog.KINDS:
            self.assertEqual(sum(p.kind == k for p in catalog.PRODUCTS), 5, k)

    def test_unknown(self):
        with self.assertRaises(KeyError):
            catalog.get("NOPE")


class Feeds(unittest.TestCase):
    def test_all_six_timeframes(self):
        self.assertEqual(list(feeds.TIMEFRAMES), ["1m", "5m", "15m", "1h", "4h", "1d"])
        for tf in feeds.TIMEFRAMES:
            cs, src = feeds.candles("BTC", tf)
            self.assertEqual(src, "simulated")
            self.assertGreaterEqual(len(cs), 200)
            self.assertEqual([c["t"] for c in cs], sorted(c["t"] for c in cs))
            for c in cs:
                self.assertLessEqual(c["l"], min(c["o"], c["c"]) + 1e-9)
                self.assertGreaterEqual(c["h"], max(c["o"], c["c"]) - 1e-9)

    def test_timeframes_agree_on_price(self):
        self.assertEqual(len({round(feeds.candles("GOLD", tf)[0][-1]["c"], 6) for tf in feeds.TIMEFRAMES}), 1)

    def test_bad_timeframe(self):
        with self.assertRaises(ValueError):
            feeds.candles("BTC", "3m")

    def test_aggregate_4h(self):
        hourly = [{"t": i * 3600, "o": i, "h": i + 1, "l": i - 1, "c": i + .5, "v": 1} for i in range(8)]
        out = feeds._aggregate(hourly, 14400)
        self.assertEqual(len(out), 2)
        self.assertEqual((out[0]["o"], out[0]["c"], out[0]["h"], out[0]["v"]), (0, 3.5, 4, 4))


class Indicators(unittest.TestCase):
    def test_sma_ema(self):
        self.assertEqual(ind.sma([1, 2, 3, 4], 2), [None, 1.5, 2.5, 3.5])
        e = ind.ema([1, 2, 3, 4, 5], 3)
        self.assertEqual(e[:2], [None, None])
        self.assertAlmostEqual(e[4], 4)

    def test_rsi_extremes(self):
        self.assertEqual(ind.last(ind.rsi(list(range(1, 40)))), 100)
        self.assertLess(ind.last(ind.rsi(list(range(40, 1, -1)))), 1)

    def test_bollinger_and_macd(self):
        b = ind.bollinger([10 + (i % 5) for i in range(60)])
        self.assertLess(ind.last(b["lower"]), ind.last(b["mid"]))
        self.assertGreater(ind.last(b["upper"]), ind.last(b["mid"]))
        m = ind.macd([float(i) for i in range(100)])
        self.assertEqual(len(m["macd"]), 100)
        self.assertGreater(m["macd"][-1], 0)


class Profile(unittest.TestCase):
    def test_poc_inside_value_area(self):
        vp = volume_profile.profile(feeds.candles("ETH", "1h")[0])
        self.assertLessEqual(vp["val"], vp["poc"])
        self.assertLessEqual(vp["poc"], vp["vah"])
        self.assertIsNone(volume_profile.profile([])["poc"])


class OrderFlow(unittest.TestCase):
    T = [{"t": 0, "price": 100, "size": 2, "side": "buy"},
         {"t": 10, "price": 100, "size": 1, "side": "sell"},
         {"t": 70, "price": 101, "size": 5, "side": "buy"}]

    def test_cumulative_delta_and_footprint(self):
        self.assertEqual([p["cd"] for p in flow.cumulative_delta(self.T)], [2, 1, 6])
        fp = flow.footprint(self.T, 60, tick=1)
        self.assertEqual(len(fp), 2)
        self.assertEqual(fp[0]["delta"], 1)
        self.assertEqual(fp[1]["rows"][0]["imbalance"], "buy")

    def test_dom_walls(self):
        d = flow.smart_dom({"bids": [[99, 1], [98, 1], [97, 50]], "asks": [[101, 1], [102, 1]]})
        self.assertTrue(d["bids"][2]["wall"])
        self.assertGreater(d["imbalance"], 0)
        self.assertEqual(d["spread"], 2)

    def test_coinbase_side_is_taker_side(self):
        # Coinbase reports the maker's side; a maker "sell" means the taker bought.
        import unittest.mock as mock
        rows = [{"time": "2026-11-21T00:00:00Z", "price": "100", "size": "1", "side": "sell"}]
        with mock.patch.dict(os.environ, {"MARKETS_FEED": ""}), \
             mock.patch.object(feeds, "_get_json", return_value=rows):
            feeds._cache.clear()
            tr, src = feeds.trades("SOL")
        self.assertEqual((src, tr[0]["side"]), ("coinbase", "buy"))


class Outlook(unittest.TestCase):
    def trend(self, up):
        cs, p = [], 100.0
        for i in range(120):
            o = p
            p *= 1.004 if up else 0.996
            cs.append({"t": i * 60, "o": o, "h": max(o, p) * 1.001, "l": min(o, p) * 0.999, "c": p, "v": 100})
        return cs

    def test_up_and_down(self):
        up, dn = outlook.analyse(self.trend(True)), outlook.analyse(self.trend(False))
        self.assertEqual((up["direction"], dn["direction"]), ("up", "down"))
        self.assertLess(up["stop_loss"], up["price"])
        self.assertGreater(up["take_profit"], up["price"])
        self.assertGreater(dn["stop_loss"], dn["price"])
        self.assertTrue(up["reasons"])

    def test_flat_is_not_worth_the_risk(self):
        cs = [{"t": i, "o": 100, "h": 100.1, "l": 99.9, "c": 100 + (.01 if i % 2 else -.01), "v": 1} for i in range(100)]
        r = outlook.analyse(cs)
        self.assertTrue(r["not_worth_the_risk"])
        self.assertIn("Not worth the risk", r["warning"])

    def test_four_horizons(self):
        o = outlook.outlook("NVDA")
        self.assertEqual(set(o), {"1m", "1h", "4h", "24h"})
        for v in o.values():
            if not v["not_worth_the_risk"]:
                self.assertGreaterEqual(v["reward_risk"], outlook.MIN_RR)
        self.assertTrue(outlook.why_moving("OIL"))


class Board(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["EVENTS_DIR"] = self.tmp.name

    def tearDown(self):
        os.environ.pop("EVENTS_DIR", None)
        self.tmp.cleanup()

    def test_changes_and_movers(self):
        rows = changes.board(["BTC", "ETH", "GOLD", "AAPL", "EURUSD", "SPX"])
        for r in rows:
            for k in ("change_5m", "change_1d", "change_40d"):
                self.assertIsNotNone(r[k], (r["symbol"], k))
        mv = changes.movers(rows, n=3)
        self.assertEqual(len(mv["risers"]), 3)
        self.assertGreaterEqual(mv["risers"][0]["change_1d"], mv["fallers"][0]["change_1d"])

    def test_favourites_persist(self):
        self.assertIn("BTC", markets.favourites())
        self.assertNotIn("BTC", markets.toggle_favourite("btc"))
        self.assertNotIn("BTC", markets.favourites())
        self.assertIn("BTC", markets.toggle_favourite("BTC"))
        with self.assertRaises(KeyError):
            markets.toggle_favourite("NOPE")

    def test_product_payload(self):
        d = markets.product("BTC", "15m")
        self.assertIsNotNone(d["orderflow"])
        self.assertIsNone(markets.product("AAPL", "1d")["orderflow"])


PASSWORD = "correct horse battery staple"


class Routes(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["EVENTS_DIR"] = self.tmp.name
        Path(self.tmp.name, "events-0.jsonl").write_text('{"ts":1,"shard":0,"type":"start","agents":250}\n')
        hub = EventHub(Path(self.tmp.name))
        hub.poll_files()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(hub, WebAuth(PASSWORD, "secret")))
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        resp, _ = self.request("POST", "/login", urllib.parse.urlencode({"password": PASSWORD}))
        self.cookie = resp.getheader("Set-Cookie").split(";")[0].split("=", 1)[1]

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        os.environ.pop("EVENTS_DIR", None)
        self.tmp.cleanup()

    def request(self, method, path, body=None, cookie=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=30)
        h = {"Host": "ats.example.com", "Origin": "https://ats.example.com"}
        if cookie:
            h["Cookie"] = f"{COOKIE}={cookie}"
        if body is not None:
            h["Content-Type"] = "application/x-www-form-urlencoded"
        conn.request(method, path, body=body, headers=h)
        resp = conn.getresponse()
        data = resp.read()
        conn.close()
        return resp, data

    def test_needs_sign_in(self):
        for path in ("/markets", "/markets/BTC"):
            resp, _ = self.request("GET", path)
            self.assertEqual((resp.status, resp.getheader("Location")), (303, "/login"), path)
        for path in ("/api/markets", "/api/markets/BTC"):
            self.assertEqual(self.request("GET", path)[0].status, 401, path)
        self.assertEqual(self.request("POST", "/api/markets/BTC/favourite", b"")[0].status, 401)

    def test_pages(self):
        for path in ("/markets", "/markets/BTC", "/markets/eurusd", "/markets.js", "/market.js", "/markets.css"):
            self.assertEqual(self.request("GET", path, cookie=self.cookie)[0].status, 200, path)
        self.assertEqual(self.request("GET", "/markets/NOPE", cookie=self.cookie)[0].status, 404)

    def test_pages_have_no_inline_script_or_style(self):
        # The dashboard's CSP allows scripts and styles only from files on this site.
        for path in ("/markets", "/markets/BTC"):
            _, body = self.request("GET", path, cookie=self.cookie)
            html = body.decode()
            self.assertNotIn("<script>", html)
            self.assertNotIn("style=", html)
            self.assertNotIn("onclick", html)

    def test_api(self):
        resp, body = self.request("GET", "/api/markets", cookie=self.cookie)
        d = json.loads(body)
        self.assertEqual(len(d["products"]), 25)
        self.assertIn("risers", d["movers_1d"])
        d = json.loads(self.request("GET", "/api/markets/BTC?tf=4h", cookie=self.cookie)[1])
        self.assertEqual(d["timeframe"], "4h")
        for k in ("ma20", "ma50", "bollinger", "rsi", "macd"):
            self.assertIn(k, d["indicators"])
        self.assertEqual(set(d["outlook"]), {"1m", "1h", "4h", "24h"})
        self.assertEqual(self.request("GET", "/api/markets/BTC?tf=7m", cookie=self.cookie)[0].status, 404)
        self.assertEqual(self.request("GET", "/api/markets/NOPE", cookie=self.cookie)[0].status, 404)

    def test_favourite_toggle(self):
        resp, body = self.request("POST", "/api/markets/TSLA/favourite", b"", cookie=self.cookie)
        self.assertEqual(resp.status, 200)
        self.assertIn("TSLA", json.loads(body)["favourites"])
        self.assertEqual(self.request("POST", "/api/markets/NOPE/favourite", b"", cookie=self.cookie)[0].status, 404)


if __name__ == "__main__":
    unittest.main()
