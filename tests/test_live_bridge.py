"""Real-money chain: desk proposal -> owner approval with a code -> bridge places -> result back."""

import http.client
import json
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path

from trading import live_orders
from trading.bridge import Bridge, Refused, check
from trading.dashboard import EventHub, make_handler
from trading.webauth import COOKIE, WebAuth, totp

SECRET = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"
TOKEN = "t" * 40
PASSWORD = "correct-horse-battery"
ENV = {"LIVE_MAX_ORDER_USD": "500", "LIVE_MAX_DAY_USD": "800", "LIVE_SYMBOLS": "AAPL,MSFT", "BRIDGE_TOKEN": TOKEN}


class QueueTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.limits = live_orders.Limits(ENV)
        self.t = [1_800_000_000.0]
        self.clock = lambda: self.t[0]

    def tearDown(self):
        self.tmp.cleanup()

    def test_proposals_are_capped_and_filtered(self):
        o = live_orders.propose(self.dir, "AAPL", "buy", 120.0, "why", self.limits, self.clock)
        self.assertEqual((o["qty"], o["status"]), (4, "proposed"))  # 4 x $120 fits $500
        self.assertIsNone(live_orders.propose(self.dir, "AAPL", "buy", 120.0, "", self.limits, self.clock))  # open
        self.assertIsNone(live_orders.propose(self.dir, "TSLA", "buy", 100.0, "", self.limits, self.clock))  # not listed
        self.assertIsNone(live_orders.propose(self.dir, "MSFT", "sell", 100.0, "", self.limits, self.clock))  # none held
        self.assertIsNone(live_orders.propose(self.dir, "MSFT", "buy", 900.0, "", self.limits, self.clock))  # 0 shares

    def test_daily_limit_and_expiry(self):
        a = live_orders.propose(self.dir, "AAPL", "buy", 120.0, "", self.limits, self.clock)
        live_orders.decide(self.dir, a["id"], True, self.limits, self.clock)
        b = live_orders.propose(self.dir, "MSFT", "buy", 100.0, "", self.limits, self.clock)
        self.assertIsNone(b)  # $480 + $500 would pass the $800 day
        self.t[0] += live_orders.PROPOSAL_TTL + 1
        self.assertIsNone(live_orders.next_for_bridge(self.dir, self.clock))  # approved too long ago: stale price
        self.assertEqual(live_orders.load(self.dir)["orders"][0]["status"], "expired")

    def test_off_without_all_limits(self):
        limits = live_orders.Limits({"LIVE_MAX_ORDER_USD": "500", "LIVE_SYMBOLS": "AAPL"})
        self.assertFalse(limits.on)
        self.assertIsNone(live_orders.propose(self.dir, "AAPL", "buy", 100.0, "", limits, self.clock))


class FakeBroker:
    real = True

    def __init__(self):
        self.placed = []

    def place(self, symbol, side, qty, price):
        self.placed.append((symbol, side, qty, price))
        return {"order_id": "123", "limit": price}

    def positions(self):
        return {"AAPL": sum(q for s, side, q, _ in self.placed if s == "AAPL")}


class BridgeCheckTests(unittest.TestCase):
    def test_own_limits(self):
        limits = live_orders.Limits(ENV)
        check({"symbol": "AAPL", "side": "buy", "qty": 4, "price": 120.0}, limits, 0)
        with self.assertRaises(Refused):
            check({"symbol": "AAPL", "side": "buy", "qty": 40, "price": 120.0}, limits, 0)  # $4,800
        with self.assertRaises(Refused):
            check({"symbol": "TSLA", "side": "buy", "qty": 1, "price": 120.0}, limits, 0)
        with self.assertRaises(Refused):
            check({"symbol": "AAPL", "side": "buy", "qty": 4, "price": 120.0}, limits, 700)  # over the day


class EndToEndTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        hub = EventHub(self.dir)
        self.auth = WebAuth(PASSWORD, "secret", totp_secret=SECRET)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(hub, self.auth, env=ENV))
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.cookie = self.auth.issue()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.tmp.cleanup()

    def call(self, method, path, body=None, cookie=None, token=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        h = {"Content-Type": "application/json"}
        if cookie:
            h["Cookie"] = f"{COOKIE}={cookie}"
        if token:
            h["Authorization"] = f"Bearer {token}"
        conn.request(method, path, body=json.dumps(body) if body is not None else None, headers=h)
        resp = conn.getresponse()
        data = json.loads(resp.read() or b"{}")
        conn.close()
        return resp.status, data

    def http_for_bridge(self, method, path, body=None):
        return self.call(method, path, body, token=TOKEN)[1]

    def test_full_chain(self):
        order = live_orders.propose(self.dir, "AAPL", "buy", 120.0, "desk", live_orders.Limits(ENV))
        status, live = self.call("GET", "/api/live", cookie=self.cookie)
        self.assertEqual((status, live["orders"][0]["id"], live["two_step"], live["bridge"]),
                         (200, order["id"], True, True))
        # The bridge sees nothing until the owner approves, and a wrong code doesn't approve.
        self.assertIsNone(self.call("GET", "/api/bridge/next", token=TOKEN)[1]["order"])
        self.assertEqual(self.call("POST", "/api/live/decide", {"id": order["id"], "approve": True, "code": "000000"},
                                   cookie=self.cookie)[0], 403)
        self.assertEqual(self.call("POST", "/api/live/decide", {"id": order["id"], "approve": True,
                                                                 "code": totp(SECRET, time.time())},
                                   cookie=self.cookie)[0], 200)
        # Without the token, or with a wrong one, the bridge gets nothing.
        self.assertEqual(self.call("GET", "/api/bridge/next")[0], 401)
        self.assertEqual(self.call("GET", "/api/bridge/next", token="x" * 40)[0], 401)
        broker = FakeBroker()
        bridge = Bridge("https://example", TOKEN, broker, live_orders.Limits(ENV), http=self.http_for_bridge,
                        kill_path=self.dir / "KILL", log=lambda *_: None)
        self.assertIn("placed real order", bridge.once())
        self.assertEqual(broker.placed, [("AAPL", "buy", 4, 120.0)])
        self.assertEqual(bridge.once(), "idle")  # placed once only
        live = self.call("GET", "/api/live", cookie=self.cookie)[1]
        self.assertEqual((live["orders"][0]["status"], live["positions"]), ("filled", {"AAPL": 4}))

    def test_kill_file_stops_the_bridge(self):
        (self.dir / "KILL").write_text("stop")
        bridge = Bridge("https://example", TOKEN, FakeBroker(), live_orders.Limits(ENV), http=self.http_for_bridge,
                        kill_path=self.dir / "KILL", log=lambda *_: None)
        self.assertEqual(bridge.once(), "kill switch on")

    def test_needs_sign_in(self):
        self.assertEqual(self.call("POST", "/api/live/decide", {"id": "x", "approve": True})[0], 401)
        self.assertEqual(self.call("GET", "/api/live")[0], 401)


if __name__ == "__main__":
    unittest.main()


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        (self.dir / "events-0.jsonl").write_text(
            '{"ts":1,"shard":0,"type":"start","agents":200,"market":"live"}\n'
            '{"ts":2,"shard":0,"type":"tick","tick":5,"equity":101000,"pnl":1000,"proven":2,"stopped":null,'
            '"stale":["AAPL"],"risk":{"day_pnl":400,"drawdown":0.01},"jev":{"approved":7,"rejected":1}}\n')
        hub = EventHub(self.dir)
        hub.poll_files()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(
            hub, WebAuth(PASSWORD, "secret"), env={**ENV, "REPORT_TOKEN": "r" * 40}))
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.tmp.cleanup()

    call = EndToEndTests.call

    def test_report_with_token(self):
        status, rep = self.call("GET", "/api/report", token="r" * 40)
        self.assertEqual(status, 200)
        self.assertEqual((rep["market"], rep["session_pnl"], rep["today_pnl"], rep["orders"], rep["proven_agents"]),
                         ("live", 1000, 400, 7, 2))
        self.assertEqual(rep["stale_prices"], ["AAPL"])
        self.assertIn("real_money", rep)

    def test_report_refused_without_token(self):
        self.assertEqual(self.call("GET", "/api/report")[0], 401)
        self.assertEqual(self.call("GET", "/api/report", token="x" * 40)[0], 401)
        self.assertEqual(self.call("GET", "/api/report", token=TOKEN)[0], 401)  # the bridge token doesn't open it


class AuditTests(unittest.TestCase):
    def test_every_step_is_chained_and_tampering_is_caught(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            limits = live_orders.Limits(ENV)
            o = live_orders.propose(d, "AAPL", "buy", 120.0, "desk", limits)
            live_orders.decide(d, o["id"], True, limits, by="owner test")
            sent = live_orders.next_for_bridge(d)
            live_orders.record_result(d, sent["id"], True, "placed", 4, 120.0, {"AAPL": 4})
            lines = [json.loads(x) for x in (d / "audit.jsonl").read_text().splitlines()]
            self.assertEqual([x["step"] for x in lines], ["proposed", "approved", "sent", "filled"])
            self.assertEqual(lines[1]["by"], "owner test")
            self.assertEqual(live_orders.verify_audit(d), {"ok": True, "entries": 4})
            raw = (d / "audit.jsonl").read_text().splitlines()
            raw[1] = raw[1].replace('"approved"', '"rejected"')  # someone edits history
            (d / "audit.jsonl").write_text("\n".join(raw) + "\n")
            self.assertEqual(live_orders.verify_audit(d), {"ok": False, "entries": 1, "broken_at_line": 2})
