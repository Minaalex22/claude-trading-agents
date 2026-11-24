"""One account across every platform, the Demo/Live switch, and the ATAS receiver."""
import http.client
import json
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path

from trading import atas_feed as A
from trading import one_account as O
from trading.dashboard import EventHub, make_handler
from trading.webauth import COOKIE, WebAuth
from tests import test_web

TOKEN = "t" * 40
NOW = 1_795_000_000.0  # a weekday in Nov 2026


def cfd(start=10_000.0, equity=10_050.0, open_=(), closed=()):
    return {"start": start, "equity": equity, "unrealized": 50.0, "realized": 0.0, "open": list(open_), "closed": list(closed)}


class CombineTests(unittest.TestCase):
    def rows(self):
        mit = O.from_cfd("mitrade", "Mitrade", cfd(open_=[{"symbol": "XAUUSD", "side": "buy", "margin": 100, "leverage": 5,
                                                          "pnl": 50.0, "entry": 4000, "mark": 4010, "stop_loss": 3990}],
                                                   closed=[{"symbol": "EURUSD", "pnl": -20.0, "closed": NOW - 60}]))
        bt = O.from_cfd("btcc", "BTCC", cfd(start=10_000.0, equity=9_900.0,
                                             closed=[{"symbol": "BTCUSDT", "pnl": -100.0, "closed": NOW - 3600}]))
        mt5 = O.from_mt5({"account": {"balance": 5000, "equity": 5100, "currency": "USD", "demo": True},
                          "positions": [{"symbol": "EURUSD", "type": "POSITION_TYPE_SELL", "volume": 0.1, "profit": 100}]})
        return [O.from_desk({"totals": {"pnl": 12.5, "trades": 1}, "trades": [{"symbol": "AAPL", "pnl": 12.5, "ts": NOW - 30, "won": True}]}),
                mit, bt, mt5, O.from_mt5(None), O.from_atas({"state": "off"})]

    def test_one_balance_one_book(self):
        a = O.combine(self.rows(), now=NOW)
        self.assertEqual(a["totals"]["equity"], 10_050 + 9_900 + 5_100)
        self.assertEqual(a["totals"]["start"], 25_000)
        self.assertEqual(a["totals"]["open_positions"], 2)
        syms = {s["symbol"]: s for s in a["by_symbol"]}
        self.assertEqual(syms["GOLD"]["net"], 500.0)  # XAUUSD on Mitrade = GOLD, 100 margin x 5
        self.assertEqual(a["totals"]["closed_trades"], 3)
        self.assertEqual(sum(d["trades"] for d in a["desks"]), 3)  # every platform's trades credited to a desk
        self.assertEqual([d["key"] for d in a["desks"]], ["sydney", "asia", "europe", "london", "newyork"])

    def test_limits_apply_across_platforms(self):
        a = O.combine(self.rows(), now=NOW)
        with self.assertRaises(O.Refused):  # gold is open on Mitrade, so BTCC/MT5 can't add XAUUSD too
            O.check_order(a, "mt5", "XAUUSD", 100)
        with self.assertRaises(O.Refused):
            O.check_order(a, "btcc", "EURUSD", 100)
        O.check_order(a, "btcc", "ETHUSDT", 100)  # fine

    def test_daily_loss_stops_everything(self):
        rows = self.rows()
        rows.append(O.from_cfd("x", "x", cfd(start=1000, equity=0, closed=[{"symbol": "NDX", "pnl": -900.0, "closed": NOW - 10}])))
        a = O.combine(rows, now=NOW)
        self.assertTrue(a["risk"]["stopped"])
        with self.assertRaises(O.Refused):
            O.check_order(a, "btcc", "ETHUSDT", 10)
        self.assertTrue(O.combine(self.rows(), now=NOW, kill=True)["risk"]["stopped"])

    def test_live_order_rules(self):
        a = O.combine(self.rows(), mode="live", env={"LIVE_MONEY": "on"}, now=NOW)
        self.assertEqual(a["risk"]["max_open"], O.LIVE_MAX_OPEN)
        with self.assertRaises(O.Refused):  # two live positions already (gold + EURUSD)
            O.check_order(a, "etoro", "#1001", 50, "live", stop_loss=1, entry=2)
        a = O.combine(self.rows()[:3], mode="live", env={"LIVE_MONEY": "on"}, now=NOW)
        with self.assertRaises(O.Refused):
            O.check_order(a, "etoro", "#1001", 50, "live")  # no stop loss
        with self.assertRaises(O.Refused):
            O.check_order(a, "etoro", "#1001", 500, "live", stop_loss=99, entry=100)  # over $100 cap
        O.check_order(a, "etoro", "#1001", 80, "live", stop_loss=99, entry=100)

    def test_routes(self):
        demo = {r["key"]: r for r in O.routes({}, "demo")}
        self.assertEqual(demo["mitrade"]["mode"], "practice")
        live = {r["key"]: r for r in O.routes({"LIVE_MONEY": "on", "METAAPI_TOKEN": "x", "METAAPI_LIVE_ACCOUNT_ID": "y"}, "live")}
        self.assertEqual(live["mt5"]["mode"], "live")
        self.assertEqual(live["etoro"]["mode"], "practice")  # no keys
        self.assertEqual(live["mitrade"]["mode"], "practice")  # no API: never live
        self.assertEqual(live["btcc"]["mode"], "practice")


class SwitchTests(unittest.TestCase):
    def test_switch_needs_arming_and_confirm(self):
        with tempfile.TemporaryDirectory() as d:
            s = O.ModeSwitch(d)
            self.assertEqual(s.mode, "demo")
            with self.assertRaises(O.Refused):
                s.set("live", "GO LIVE", {})  # not armed on Render
            with self.assertRaises(O.Refused):
                s.set("live", "yes", {"LIVE_MONEY": "on"})  # wrong word
            self.assertEqual(s.set("live", "go live", {"LIVE_MONEY": "on"})["mode"], "live")
            self.assertEqual(O.ModeSwitch(d).mode, "live")  # saved
            self.assertEqual(s.set("demo", "", {})["mode"], "demo")  # back to demo needs nothing


class LiveRoutingTests(unittest.TestCase):
    def test_mt5_live_needs_stop_and_small_size(self):
        class Api:
            base, _token = "https://x", "tok"
            sent = []

            def http(self, method, url, headers, body=None):
                self.sent.append(body)
                return {"stringCode": "TRADE_RETCODE_DONE", "positionId": "1"}

            def demo_order(self, *a):
                return {"mode": "demo"}

        api = Api()
        self.assertEqual(O.mt5_order(api, False, "EURUSD", "buy", 0.01)["mode"], "demo")
        with self.assertRaises(O.Refused):
            O.mt5_order(api, True, "EURUSD", "buy", 0.01)
        with self.assertRaises(O.Refused):
            O.mt5_order(api, True, "EURUSD", "buy", 5, stop_loss=1.0)
        r = O.mt5_order(api, True, "EURUSD", "buy", 0.01, stop_loss=1.0)
        self.assertEqual((r["mode"], r["ok"]), ("live", True))
        self.assertEqual(api.sent[-1]["stopLoss"], 1.0)
        self.assertEqual(O.mt5_env({"METAAPI_LIVE_ACCOUNT_ID": "real"}, True)["METAAPI_ACCOUNT_ID"], "real")

    def test_etoro_demo_and_real_paths(self):
        seen = []
        http = lambda m, url, h, body=None: seen.append(url) or {"ok": True}  # noqa: E731
        env = {"ETORO_API_KEY": "a", "ETORO_USER_KEY": "b"}
        O.etoro_order(env, False, 1001, "buy", 50, 1, 9.0, None, http)
        O.etoro_order(env, True, 1001, "buy", 50, 1, 9.0, None, http)
        self.assertIn("/execution/demo/", seen[0])
        self.assertNotIn("/demo/", seen[1])


class AtasFeedTests(unittest.TestCase):
    def test_ingest_and_view(self):
        t = [100.0]
        f = A.AtasFeed(clock=lambda: t[0])
        self.assertEqual(f.status({})["state"], "off")
        f.ingest({"symbol": "es", "bar": {"time": "1", "close": 5000, "volume": 1000, "delta": 200, "poc": 4999.75},
                  "big_trades": [{"price": 5000, "size": 80, "side": "buy"}]})
        f.ingest({"symbol": "ES", "bar": {"time": "1", "close": 5001, "volume": 1100, "delta": 250}})  # same bar again
        st = f.status({"ATAS_TOKEN": TOKEN})
        self.assertEqual(st["state"], "live")
        self.assertEqual(len(st["books"][0]["bars"]), 1)
        self.assertGreater(f.view("ES")["lean"], 0)
        t[0] += A.FRESH_SECONDS + 1
        self.assertEqual(f.status({"ATAS_TOKEN": TOKEN})["state"], "stale")
        for bad in ({"bar": {"close": 1}}, {"symbol": "ES", "bar": {}}, {"symbol": "ES", "bar": {"close": "nan"}}):
            with self.assertRaises(A.FeedError):
                f.ingest(bad)

    def test_atas_account_joins_the_one_account(self):
        f = A.AtasFeed()
        f.ingest({"symbol": "NQ", "bar": {"close": 1}, "account": {"balance": 2000, "open_pnl": 25,
                                                                 "positions": [{"symbol": "NQ", "size": 1, "pnl": 25}]}})
        row = O.from_atas(f.status({"ATAS_TOKEN": TOKEN}))
        self.assertEqual(row["equity"], 2025)
        self.assertEqual(row["positions"][0]["side"], "buy")


class AccountWebTests(unittest.TestCase):
    request = test_web.ServerTests.request
    login = test_web.ServerTests.login
    cookie_from = test_web.ServerTests.cookie_from

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        Path(self.tmp.name, "events-0.jsonl").write_text('{"ts":1,"shard":0,"type":"start","agents":250}\n')
        hub = EventHub(Path(self.tmp.name))
        hub.poll_files()
        env = {"ATAS_TOKEN": TOKEN, "LIVE_MONEY": "on"}
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(hub, WebAuth(test_web.PASSWORD, "secret"), env=env))
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.tmp.cleanup()

    def post(self, path, body, cookie=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        h = {"Host": "ats.example.com", "Content-Type": "application/json", **(headers or {})}
        if cookie:
            h["Cookie"] = f"{COOKIE}={cookie}"
        conn.request("POST", path, body=json.dumps(body), headers=h)
        resp = conn.getresponse()
        data = resp.read()
        conn.close()
        return resp, data

    def test_page_switch_and_atas(self):
        resp, _ = self.request("GET", "/account")
        self.assertEqual(resp.status, 303)
        resp, _ = self.request("GET", "/api/account")
        self.assertEqual(resp.status, 401)
        cookie = self.cookie_from(self.login()[0])
        resp, _ = self.request("GET", "/account", cookie=cookie)
        self.assertEqual(resp.status, 200)
        resp, body = self.request("GET", "/api/account", cookie=cookie)
        a = json.loads(body)
        self.assertEqual(a["mode"], "demo")
        self.assertTrue(a["armed"])
        self.assertIn("mitrade", [x["key"] for x in a["accounts"]])
        # the switch
        resp, body = self.post("/api/account/mode", {"mode": "live", "confirm": "nope"}, cookie)
        self.assertEqual(resp.status, 403)
        resp, body = self.post("/api/account/mode", {"mode": "live", "confirm": "GO LIVE"}, cookie)
        self.assertEqual(json.loads(body)["mode"], "live")
        resp, body = self.post("/api/account/mode", {"mode": "demo"}, cookie)
        self.assertEqual(json.loads(body)["mode"], "demo")
        resp, _ = self.post("/api/account/mode", {"mode": "live", "confirm": "GO LIVE"})
        self.assertEqual(resp.status, 401)  # never without signing in
        # ATAS: token or nothing
        bar = {"symbol": "ES", "bar": {"time": "1", "close": 5000, "volume": 10, "delta": 4}}
        resp, _ = self.post("/api/atas/feed", bar, headers={"Authorization": "Bearer wrong"})
        self.assertEqual(resp.status, 401)
        resp, body = self.post("/api/atas/feed", bar, headers={"Authorization": f"Bearer {TOKEN}"})
        self.assertEqual(resp.status, 200, body)
        resp, body = self.request("GET", "/api/atas", cookie=cookie)
        self.assertEqual(json.loads(body)["state"], "live")
        # orders on platforms that aren't connected are refused clearly
        resp, body = self.post("/api/account/order", {"platform": "etoro", "side": "buy", "instrument_id": 1, "amount": 10}, cookie)
        self.assertEqual(resp.status, 403)
        self.assertIn("ETORO", json.loads(body)["error"])


if __name__ == "__main__":
    unittest.main()
