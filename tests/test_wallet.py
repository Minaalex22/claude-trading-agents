import json
import tempfile
import unittest
from pathlib import Path

from tests.test_markets import Routes
from trading.wallet import START_CASH, Wallet, WalletError, looks_secret

BTC = "bc1qar0srrr7xfkvy5l643lydnw9re59gtzzwf5mdq"
ETH = "0xde0B295669a9FD93d5F28D9Ec85E40f4cb697BAe"


def fake_http(url, body=None):
    if "blockstream" in url:
        return {"chain_stats": {"funded_txo_sum": 250_000_000, "spent_txo_sum": 50_000_000}}
    return {"result": hex(3 * 10**18)}


class WalletTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.w = Wallet(self.tmp.name, env={}, http=fake_http)

    def tearDown(self):
        self.tmp.cleanup()

    def test_practice_money_in_and_out_and_saved(self):
        self.w.move("deposit", 500, "top up")
        self.w.move("withdraw", "200")
        with self.assertRaises(WalletError):
            self.w.move("withdraw", START_CASH * 10)
        for bad in (0, -5, "abc", 10**9):
            with self.assertRaises(WalletError):
                self.w.move("deposit", bad)
        again = Wallet(self.tmp.name, env={}, http=fake_http)  # survives a restart
        s = again.snapshot(desk={"totals": {"pnl": 120.5, "trades": 3}}, polymarket={"total": 9.5})
        self.assertEqual(s["practice_cash"], START_CASH + 300)
        self.assertEqual(s["total"], START_CASH + 300 + 130)
        self.assertEqual(s["profit"], 130)  # money moved in or out is not profit
        self.assertEqual([e["kind"] for e in s["ledger"]], ["withdraw", "deposit", "start"])
        self.assertEqual(s["live_trading"], "locked")

    def test_refuses_private_keys_and_seed_phrases(self):
        secrets = ["a" * 64, "0x" + "1f" * 32, "5HueCGU8rMjxEXxiPuD5BDku4MkFqeZyd4dZ1jvhTVqvbTLvyTJ",
                   " ".join(["abandon"] * 11 + ["about"])]
        for s in secrets:
            self.assertTrue(looks_secret(s))
            with self.assertRaises(WalletError) as e:
                self.w.watch(s)
            self.assertIn("private key", str(e.exception))
        self.assertFalse(Path(self.tmp.name, "wallet.json").exists() and
                         any(s in Path(self.tmp.name, "wallet.json").read_text() for s in secrets))
        with self.assertRaises(WalletError):
            self.w.watch("not an address")

    def test_watch_list_balances(self):
        self.w.watch(BTC, "cold")
        self.w.watch(ETH)
        with self.assertRaises(WalletError):
            self.w.watch(BTC)
        s = self.w.snapshot(prices={"BTC-USD": 100.0, "ETH-USD": 10.0})
        by = {c["coin"]: c for c in s["crypto"]}
        self.assertEqual(by["BTC"]["balance"], 2.0)
        self.assertEqual(by["BTC"]["usd"], 200.0)
        self.assertEqual(by["ETH"]["balance"], 3.0)
        self.assertEqual(s["crypto_usd"], 230.0)
        self.assertTrue(self.w.unwatch(ETH))
        self.assertEqual(len(self.w.snapshot()["crypto"]), 1)

    def test_explorer_down_is_shown_not_crashed(self):
        def down(url, body=None):
            raise OSError("no network")
        w = Wallet(self.tmp.name, env={}, http=down)
        w.watch(BTC)
        c = w.snapshot()["crypto"][0]
        self.assertIsNone(c["balance"])
        self.assertIn("couldn't reach", c["error"])

    def test_platform_rows_show_key_names_never_values(self):
        rows = [{"key": "etoro", "name": "eToro", "configured": True, "needs_keys": True, "missing": [], "mode": "paper"},
                {"key": "moomoo", "name": "moomoo", "configured": True, "needs_keys": False, "missing": [], "mode": "demo"}]
        s = self.w.snapshot(platforms=rows)
        keys = {a["key"]: a for a in s["accounts"]}
        self.assertEqual(keys["etoro"]["state"], "keys_set")
        self.assertEqual(keys["moomoo"]["state"], "paper")
        self.assertIn(keys["mt5"]["state"], ("not_installed", "not_connected"))


class WalletPage(Routes):
    def post(self, path, body):
        import http.client
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=30)
        conn.request("POST", path, body=json.dumps(body), headers={
            "Host": "ats.example.com", "Origin": "https://ats.example.com", "Content-Type": "application/json",
            "Cookie": self._cookie_header()})
        r = conn.getresponse()
        data = r.read()
        conn.close()
        return r.status, json.loads(data)

    def _cookie_header(self):
        from trading.webauth import COOKIE
        return f"{COOKIE}={self.cookie}"

    def test_wallet_page_and_api(self):
        self.assertEqual(self.request("GET", "/wallet")[0].status, 303)
        self.assertEqual(self.request("GET", "/api/wallet")[0].status, 401)
        for path in ("/wallet", "/wallet.js", "/wallet.css", "/api/wallet"):
            self.assertEqual(self.request("GET", path, cookie=self.cookie)[0].status, 200, path)
        html = self.request("GET", "/wallet", cookie=self.cookie)[1].decode()
        self.assertIn("PRACTICE MONEY", html)
        self.assertNotIn("style=", html)
        self.assertIn('href="/wallet"', self.request("GET", "/news", cookie=self.cookie)[1].decode())
        self.assertEqual(self.post("/api/wallet/move", {"kind": "deposit", "amount": 250})[0], 200)
        status, body = self.post("/api/wallet/watch", {"address": "a" * 64})
        self.assertEqual(status, 400)
        self.assertIn("private key", body["error"])
        d = json.loads(self.request("GET", "/api/wallet", cookie=self.cookie)[1])
        self.assertEqual(d["practice_cash"], START_CASH + 250)
        self.assertEqual(d["mode"], "practice")


if __name__ == "__main__":
    unittest.main()
