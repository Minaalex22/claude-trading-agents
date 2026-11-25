"""Guarded ATAS orders through the ATAS MCP Bridge, against a fake bridge."""
import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from trading import atas_bridge as B


class FakeBridge(BaseHTTPRequestHandler):
    status = {"symbol": "NQH27", "portfolio": "Sim101", "activated": True}
    posts: list = []

    def do_GET(self):
        body = {"/api/status": self.status, "/api/quote": {"last": 22000.0}}.get(self.path, {"ok": True, "path": self.path})
        self._send(body)

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(n) or b"{}")
        FakeBridge.posts.append((self.path, body, self.headers.get("X-Bridge-Token")))
        self._send({"ok": True, "id": str(len(FakeBridge.posts))})

    def _send(self, body):
        raw = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *a):
        pass


class BridgeTests(unittest.TestCase):
    def setUp(self):
        FakeBridge.posts = []
        FakeBridge.status = {"symbol": "NQH27", "portfolio": "Sim101", "activated": True}
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), FakeBridge)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.tmp = tempfile.TemporaryDirectory()
        self.audit = Path(self.tmp.name, "audit.jsonl")
        self.sw = {"mode": "demo", "armed": False, "kill": False}

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.tmp.cleanup()

    def bridge(self, **env):
        env = {"ATAS_BRIDGE_URL": f"http://127.0.0.1:{self.server.server_port}", **env}
        return B.AtasBridge(env, mode=lambda: dict(self.sw), audit_path=self.audit)

    def events(self):
        return [json.loads(x)["event"] for x in self.audit.read_text().splitlines()]

    def test_demo_order_goes_through_with_a_protective_stop(self):
        out = self.bridge(ATAS_BRIDGE_TOKEN="tok").place_order("buy", "limit", 1, stop_loss=21950, price=21990)
        self.assertTrue(out["ok"])
        self.assertEqual([p[0] for p in FakeBridge.posts], ["/api/order", "/api/order"])
        entry, stop = FakeBridge.posts[0][1], FakeBridge.posts[1][1]
        self.assertEqual((entry["direction"], entry["type"], entry["price"]), ("buy", "limit", 21990))
        self.assertEqual((stop["direction"], stop["type"], stop["triggerPrice"]), ("sell", "stop", 21950))
        self.assertEqual(FakeBridge.posts[0][2], "tok")
        self.assertIn("sent", self.events())

    def test_market_stop_checked_against_live_price(self):
        b = self.bridge()
        with self.assertRaises(B.GuardError):
            b.place_order("sell", "market", 1, stop_loss=21990)  # last is 22000: a sell stop must be above
        b.place_order("sell", "market", 1, stop_loss=22020)
        self.assertEqual(FakeBridge.posts[1][1]["direction"], "buy")

    def test_guards_block_and_are_audited(self):
        b = self.bridge()
        cases = [dict(direction="buy", order_type="market", qty=2, stop_loss=1),       # over the size cap
                 dict(direction="buy", order_type="market", qty=1, stop_loss=None),    # no stop loss
                 dict(direction="hold", order_type="market", qty=1, stop_loss=1),
                 dict(direction="buy", order_type="limit", qty=1, stop_loss=1),        # limit without price
                 dict(direction="buy", order_type="market", qty=float("nan"), stop_loss=1)]
        for c in cases:
            with self.assertRaises(B.GuardError, msg=c):
                b.place_order(**c)
        self.assertEqual(FakeBridge.posts, [])
        self.assertEqual(self.events().count("blocked"), len(cases))

    def test_demo_switch_needs_a_demo_portfolio(self):
        FakeBridge.status = {"symbol": "NQH27", "portfolio": "APEX-12345", "activated": True}
        with self.assertRaisesRegex(B.GuardError, "DEMO"):
            self.bridge().place_order("buy", "limit", 1, stop_loss=21950, price=21990)
        self.assertEqual(FakeBridge.posts, [])

    def test_live_needs_all_three_keys(self):
        FakeBridge.status = {"symbol": "ESZ26", "portfolio": "APEX-12345", "activated": True}
        self.sw = {"mode": "live", "armed": True, "kill": False}
        with self.assertRaisesRegex(B.GuardError, "ATAS_ALLOW_LIVE_ORDERS"):
            self.bridge().place_order("buy", "limit", 1, stop_loss=21950, price=21990)
        self.sw["armed"] = False
        with self.assertRaises(B.GuardError):
            self.bridge(ATAS_ALLOW_LIVE_ORDERS="true").place_order("buy", "limit", 1, stop_loss=21950, price=21990)
        self.sw["armed"] = True
        self.assertTrue(self.bridge(ATAS_ALLOW_LIVE_ORDERS="true").place_order("buy", "limit", 1, stop_loss=21950,
                                                                               price=21990)["ok"])

    def test_kill_switch_symbol_and_unreadable_switch(self):
        self.sw["kill"] = True
        with self.assertRaisesRegex(B.GuardError, "kill"):
            self.bridge().place_order("buy", "limit", 1, stop_loss=21950, price=21990)
        self.sw["kill"] = False
        FakeBridge.status = {"symbol": "CLF27", "portfolio": "Sim101", "activated": True}
        with self.assertRaisesRegex(B.GuardError, "ATAS_ALLOWED_SYMBOLS"):
            self.bridge().place_order("buy", "limit", 1, stop_loss=21950, price=21990)

        def broken():
            raise OSError("app asleep")
        b = B.AtasBridge({"ATAS_BRIDGE_URL": f"http://127.0.0.1:{self.server.server_port}"}, mode=broken,
                         audit_path=self.audit)
        FakeBridge.status = {"symbol": "NQH27", "portfolio": "Sim101", "activated": True}
        with self.assertRaisesRegex(B.GuardError, "kill"):
            b.place_order("buy", "limit", 1, stop_loss=21950, price=21990)
        self.assertEqual(FakeBridge.posts, [])

    def test_daily_cap_and_risk_reducing_actions(self):
        b = self.bridge(ATAS_MAX_ORDERS_PER_DAY="1")
        b.place_order("buy", "limit", 1, stop_loss=21950, price=21990)
        with self.assertRaisesRegex(B.GuardError, "daily"):
            b.place_order("buy", "limit", 1, stop_loss=21950, price=21990)
        self.sw["kill"] = True  # cancel and flatten still work with the kill switch on
        b.cancel_order("7")
        b.close_position()
        self.assertEqual([p[0] for p in FakeBridge.posts][-2:], ["/api/order/cancel", "/api/position/close"])

    def test_only_this_computer(self):
        for bad in ("http://10.0.0.5:8787", "https://evil.example", "file:///etc/passwd"):
            with self.assertRaises(B.GuardError):
                B.bridge_url(bad)
        self.assertEqual(B.bridge_url(None), "http://127.0.0.1:8787")
        self.assertTrue(B.symbol_allowed("MNQZ26", {"MNQ"}))
        self.assertFalse(B.symbol_allowed("MNQZ26", {"NQ"}))
        self.assertTrue(B.symbol_allowed("ES", {"ES"}))
        self.assertFalse(B.symbol_allowed("ESTX50", {"ES"}))
        with self.assertRaises(B.GuardError):
            B.AtasBridge({"ATAS_MAX_QTY": "500"}).place_order("buy", "market", 11, stop_loss=1)  # hard cap 10


if __name__ == "__main__":
    unittest.main()


class LockedBridgeTests(unittest.TestCase):
    def test_locked_copy_checks_token_and_refuses_web_pages(self):
        src = (Path(B.__file__).parent / "atas_install" / "McpBridgeStrategy.cs").read_text()
        self.assertIn('req.Headers["X-Bridge-Token"]', src)
        self.assertIn("CryptographicOperations.FixedTimeEquals", src)
        self.assertIn('req.Headers["Origin"]', src)
        self.assertIn("token.Length < 32", src)  # no token saved: everything refused
        self.assertNotIn("Access-Control-Allow", src)  # never opens CORS
        self.assertIn("MIT License", src)  # the original licence travels with the copy
        # the guard runs before any route, including the event stream
        self.assertLess(src.index("var refused = Refuse(req);"), src.index('if (path == "/api/stream"'))

    def test_installers_carry_the_bridge_and_a_token(self):
        from trading import atas_feed as A
        sh = A.installer("sh", "https://ats.example.com", "t" * 40)
        ps = A.installer("ps1", "https://ats.example.com", "t" * 40)
        for text in (sh, ps):
            self.assertIn("class McpBridgeStrategy : ChartStrategy", text)
            self.assertIn("bridge_token=", text)
            self.assertIn("AtsMatrix.McpBridge.dll", text)
        self.assertIn("openssl rand -hex 24", sh)
        self.assertIn("RandomNumberGenerator", ps)

    def test_client_uses_the_saved_bridge_token(self):
        import os
        from unittest import mock
        with tempfile.TemporaryDirectory() as home:
            Path(home, ".jarvisfeed").mkdir()
            Path(home, ".jarvisfeed", "config").write_text("url=https://a.example\ntoken=" + "f" * 40 +
                                                           "\nbridge_token=" + "b" * 48 + "\n")
            with mock.patch.dict(os.environ, {"HOME": home, "USERPROFILE": home}, clear=False):
                os.environ.pop("ATAS_BRIDGE_TOKEN", None)
                bridge = B.from_config()
        self.assertEqual(bridge.token, "b" * 48)
