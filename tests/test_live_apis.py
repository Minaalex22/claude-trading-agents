import unittest

from trading import live_apis as L
from trading import one_account as O


class KeysTests(unittest.TestCase):
    def test_names_only_and_ready_flags(self):
        env = {"METAAPI_TOKEN": "secret-token-value", "LIVE_MONEY": "on", "ETORO_API_KEY": "k"}
        rows = {r["key"]: r for r in L.keys_status(env)}
        self.assertNotIn("secret-token-value", repr(rows))  # never a value
        self.assertTrue(rows["mt5"]["demo_ready"] and rows["mt5"]["live_ready"])
        self.assertFalse(rows["etoro"]["demo_ready"])
        self.assertIn("ETORO_USER_KEY", rows["etoro"]["missing"])
        self.assertFalse(rows["polymarket"]["live_possible"])
        self.assertFalse(rows["mitrade"]["live_ready"])
        for k in ("mt5", "etoro", "ibkr", "betfair", "moomoo", "polymarket", "btcc", "mitrade", "atas", "desks", "all"):
            self.assertIn(k, rows)

    def test_betfair_session_token_counts(self):
        rows = {r["key"]: r for r in L.keys_status({"BETFAIR_APP_KEY": "a", "BETFAIR_SESSION_TOKEN": "t"})}
        self.assertTrue(rows["betfair"]["demo_ready"])
        self.assertNotIn("BETFAIR_PASSWORD", rows["betfair"]["missing"])


class Mt5Tests(unittest.TestCase):
    def setUp(self):
        L._live_found.clear()

    def test_live_account_found_from_token_never_demo(self):
        accounts = [{"_id": "demo1", "server": "GoMarkets-Demo", "state": "DEPLOYED", "login": "1"},
                    {"_id": "real1", "server": "Ava-Real 1-MT5", "state": "DEPLOYED", "login": "2", "region": "london"}]

        def http(method, url, headers, body=None):
            if url == L.PROVISIONING:
                return accounts
            return {"type": "ACCOUNT_TRADE_MODE_REAL"}
        self.assertEqual(L.mt5_live_account({"METAAPI_TOKEN": "t"}, http), ("real1", "london"))
        env = O.mt5_env({"METAAPI_TOKEN": "t2"}, True, http)
        self.assertEqual(env["METAAPI_ACCOUNT_ID"], "real1")

    def test_no_real_account_refuses(self):
        def http(method, url, headers, body=None):
            return [{"_id": "d", "server": "X-Demo", "state": "DEPLOYED"}] if url == L.PROVISIONING else {}
        with self.assertRaises(L.Refused):
            L.mt5_live_account({"METAAPI_TOKEN": "t3"}, http)
        with self.assertRaises(O.Refused):
            O.mt5_env({"METAAPI_TOKEN": "t3"}, True, http)

    def test_provision_any_broker_from_env(self):
        sent = []

        def http(method, url, headers, body=None):
            sent.append((method, body))
            return [] if method == "GET" else {"id": "new1"}
        env = {"METAAPI_TOKEN": "t", "MT5_LIVE_LOGIN": "123456", "MT5_LIVE_PASSWORD": "pw", "MT5_LIVE_SERVER": "Ava-Real 1-MT5"}
        self.assertEqual(L.mt5_provision(env, "live", http), "new1")
        self.assertEqual(sent[-1][1]["server"], "Ava-Real 1-MT5")
        self.assertIsNone(L.mt5_provision({"METAAPI_TOKEN": "t"}, "live", http))

    def test_live_close_refuses_demo_account(self):
        class Api:
            base, _token = "https://x", "t"

            def account(self):
                return {"type": "ACCOUNT_TRADE_MODE_DEMO"}

            def demo_close(self, pid):
                return {"ok": True, "mode": "demo"}
        self.assertEqual(L.mt5_close(Api(), False, "1")["mode"], "demo")
        with self.assertRaises(L.Refused):
            L.mt5_close(Api(), True, "1")


class EtoroTests(unittest.TestCase):
    def test_symbol_to_instrument_and_close_paths(self):
        seen = []

        def http(method, url, headers, body=None):
            seen.append(url)
            return {"items": [{"instrumentId": 1001, "internalSymbolFull": "AAPL"}]}
        env = {"ETORO_API_KEY": "a", "ETORO_USER_KEY": "b"}
        self.assertEqual(L.etoro_instrument_id(env, "aapl", http), 1001)
        self.assertEqual(L.etoro_instrument_id(env, "#55", http), 55)
        L.etoro_close(env, False, 9, 1001, http)
        L.etoro_close(env, True, 9, 1001, http)
        self.assertIn("/execution/demo/market-close-orders/positions/9", seen[-2])
        self.assertNotIn("/demo/", seen[-1])


class IbkrTests(unittest.TestCase):
    def make(self, account):
        sent = []

        def http(method, url, headers, body=None):
            sent.append((method, url, body))
            if url.endswith("/iserver/accounts"):
                return {"accounts": [account]}
            if "/secdef/search" in url:
                return [{"symbol": "AAPL", "conid": 265598}]
            if url.endswith("/orders"):
                return [{"id": "q1", "message": ["Are you sure?"]}]
            if "/iserver/reply/" in url:
                return [{"order_id": "777", "order_status": "Submitted"}]
            return {}
        return L.Ibkr({"IBKR_GATEWAY_URL": "https://gw.example"}, http), sent

    def test_paper_in_demo_and_real_in_live(self):
        api, sent = self.make("DU1234")
        r = api.order(False, "AAPL", "buy", 1)
        self.assertTrue(r["ok"])
        with self.assertRaises(L.Refused):
            api.order(True, "AAPL", "buy", 1, stop_loss=180)  # live on a paper login
        api, sent = self.make("U9999")
        with self.assertRaises(L.Refused):
            api.order(False, "AAPL", "buy", 1)  # demo on a real login
        with self.assertRaises(L.Refused):
            api.order(True, "AAPL", "buy", 1)  # live needs a stop
        r = api.order(True, "AAPL", "buy", 1, stop_loss=180)
        orders = next(b for m, u, b in sent if u.endswith("/orders"))["orders"]
        self.assertEqual([o["orderType"] for o in orders], ["MKT", "STP"])
        self.assertEqual(orders[1]["parentId"], orders[0]["cOID"])
        self.assertEqual((r["mode"], r["ok"]), ("live", True))

    def test_needs_https(self):
        with self.assertRaises(L.Refused):
            L.Ibkr({"IBKR_GATEWAY_URL": "http://gw"})
        with self.assertRaises(L.Refused):
            L.Ibkr({})

    def test_row(self):
        row = L.from_ibkr({"account": {"id_tail": "1234", "paper": True, "equity": 1000.0},
                           "positions": [{"symbol": "AAPL", "qty": 2, "value": 380, "pnl": 10}]})
        self.assertEqual((row["state"], row["equity"], row["unrealized"]), ("connected", 1000.0, 10.0))


class RoutesTests(unittest.TestCase):
    def test_ibkr_and_moomoo_routes(self):
        live = {r["key"]: r for r in O.routes({"LIVE_MONEY": "on", "METAAPI_TOKEN": "x", "IBKR_GATEWAY_URL": "https://g"}, "live")}
        self.assertEqual(live["mt5"]["mode"], "live")  # real account found from the token
        self.assertEqual(live["ibkr"]["mode"], "live")
        self.assertEqual(live["moomoo"]["mode"], "practice")
        self.assertEqual(live["polymarket"]["mode"], "practice")


if __name__ == "__main__":
    unittest.main()
