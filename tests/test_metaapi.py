"""MetaTrader 5 via the MetaApi cloud: reads always, orders only on a demo account, secrets never shown."""

import unittest
from datetime import datetime, timezone

from trading import metaapi as M
from trading import platforms as P

ENV = {"METAAPI_TOKEN": "sekrit-token", "METAAPI_ACCOUNT_ID": "acc-1234", "MT5_MODE": "demo"}


def fake(account_type="ACCOUNT_TRADE_MODE_DEMO", deals=None, positions=None):
    calls = []

    def http(method, url, headers, body=None):
        calls.append((method, url, headers, body))
        if url.endswith("/account-information"):
            return {"type": account_type, "balance": 870.0, "equity": 860.0, "currency": "AUD",
                    "broker": "GO Markets", "server": "GoMarkets-Demo", "login": 99, "name": "x"}
        if url.endswith("/positions"):
            return positions or []
        if "/history-deals/" in url:
            return deals or []
        if url.endswith("/current-price"):
            return {"bid": 1.1, "ask": 1.3}
        if url.endswith("/trade"):
            return {"numericCode": 10009, "stringCode": "TRADE_RETCODE_DONE", "message": "ok", "positionId": "7"}
        raise AssertionError(url)
    return http, calls


def deal(symbol, profit, closing=True, side="DEAL_TYPE_SELL", commission=0.0, swap=0.0, t="2026-11-20T01:00:00Z"):
    return {"symbol": symbol, "profit": profit, "commission": commission, "swap": swap, "type": side, "time": t,
            "entryType": "DEAL_ENTRY_OUT" if closing else "DEAL_ENTRY_IN", "volume": 0.1}


class ReadTests(unittest.TestCase):
    def test_needs_both_keys(self):
        self.assertFalse(M.configured({}))
        with self.assertRaises(PermissionError):
            M.MetaApi({"METAAPI_ACCOUNT_ID": "x"})

    def test_reads_use_token_header_and_region(self):
        http, calls = fake()
        api = M.MetaApi({**ENV, "METAAPI_REGION": "london"}, http)
        snap = api.snapshot()
        self.assertTrue(snap["account"]["demo"])
        self.assertNotIn("login", snap["account"])
        method, url, headers, _ = calls[0]
        self.assertEqual(method, "GET")
        self.assertTrue(url.startswith("https://mt-client-api-v1.london.agiliumtrade.ai/users/current/accounts/acc-1234"))
        self.assertEqual(headers["auth-token"], "sekrit-token")
        self.assertNotIn("sekrit", repr(api))

    def test_bad_region_refused(self):
        with self.assertRaises(ValueError):
            M.MetaApi({**ENV, "METAAPI_REGION": "evil.com/x"})


class OrderTests(unittest.TestCase):
    def test_demo_order_sent(self):
        http, calls = fake()
        fill = M.MetaApi(ENV, http).demo_order("EURUSD", "buy", 0.1)
        self.assertTrue(fill["ok"])
        post = [c for c in calls if c[0] == "POST"][0]
        self.assertEqual(post[3]["actionType"], "ORDER_TYPE_BUY")
        self.assertEqual(post[3]["volume"], 0.1)

    def test_real_account_refused(self):
        http, calls = fake("ACCOUNT_TRADE_MODE_REAL")
        with self.assertRaises(M.NotDemo):
            M.MetaApi(ENV, http).demo_order("EURUSD", "buy", 0.1)
        with self.assertRaises(M.NotDemo):
            M.MetaApi(ENV, http).demo_close("7")
        self.assertFalse([c for c in calls if c[0] == "POST"])

    def test_bad_order_refused(self):
        http, _ = fake()
        for side, vol in (("hold", 1), ("buy", 0), ("buy", 1000)):
            with self.assertRaises(ValueError):
                M.MetaApi(ENV, http).demo_order("EURUSD", side, vol)

    def test_connector_uses_cloud_and_keeps_live_locked(self):
        http, calls = fake("ACCOUNT_TRADE_MODE_REAL")
        c = P.connect("mt5", ENV)
        c.api = lambda: M.MetaApi(ENV, http)
        self.assertTrue(c.configured)
        self.assertEqual(c.mode, "demo")
        with self.assertRaises(P.LiveTradingLocked):
            c.place_order("EURUSD", "buy", 0.1)
        with self.assertRaises(P.LiveTradingLocked):
            c.place_order("EURUSD", "buy", 0.1, live=True)
        self.assertFalse([x for x in calls if x[0] == "POST"])

    def test_status_shows_names_only(self):
        row = next(r for r in P.status(ENV) if r["key"] == "mt5")
        self.assertTrue(row["configured"])
        self.assertEqual(row["mode"], "demo")
        self.assertNotIn("sekrit", repr(P.status(ENV)))
        self.assertIn("METAAPI_TOKEN", P.SECRET_NAMES)
        self.assertNotIn("METAAPI_ACCOUNT_ID", P.SECRET_NAMES)


class DiscoverTests(unittest.TestCase):
    def setUp(self):
        M._found.clear()

    def test_finds_demo_account_and_deploys_it(self):
        calls = []

        def http(method, url, headers, body=None):
            calls.append((method, url))
            if url == M.PROVISIONING:
                return [{"_id": "live-1", "server": "GoMarkets-Live", "state": "DEPLOYED", "region": "london"},
                        {"_id": "demo-9", "server": "GoMarkets-Demo", "state": "UNDEPLOYED", "region": "london"}]
            return {}
        api = M.MetaApi({"METAAPI_TOKEN": "tok"}, http)
        self.assertEqual(api.account_id, "demo-9")
        self.assertIn("london", api.base)
        self.assertIn(("POST", M.PROVISIONING + "/demo-9/deploy"), calls)
        M.MetaApi({"METAAPI_TOKEN": "tok"}, http)  # cached: no second lookup
        self.assertEqual(sum(1 for c in calls if c[1] == M.PROVISIONING), 1)

    def test_no_accounts_says_what_to_do(self):
        with self.assertRaises(PermissionError):
            M.MetaApi({"METAAPI_TOKEN": "tok2"}, lambda *a, **k: [])

    def test_rejected_token_is_explained(self):
        import urllib.error
        err = urllib.error.HTTPError(M.PROVISIONING, 401, "Unauthorized", {}, None)
        self.assertIn("token rejected", M.explain(err))
        self.assertIn("could not find", M.explain(urllib.error.HTTPError("u", 404, "x", {}, None)))

    def test_token_alone_means_demo_mode(self):
        self.assertTrue(M.configured({"METAAPI_TOKEN": "x"}))
        row = next(r for r in P.status({"METAAPI_TOKEN": "x"}) if r["key"] == "mt5")
        self.assertEqual(row["mode"], "demo")
        row = next(r for r in P.status({"METAAPI_TOKEN": "x", "MT5_MODE": "live"}) if r["key"] == "mt5")
        self.assertEqual(row["mode"], "paper")
        self.assertEqual(next(r for r in P.status({}) if r["key"] == "mt5")["mode"], "paper")


class ReportTests(unittest.TestCase):
    def test_loss_report(self):
        deals = [deal("XAUUSD", -120, t="2026-11-18T01:00:00Z"), deal("XAUUSD", -80, t="2026-11-18T02:00:00Z"),
                 deal("EURUSD", 30, commission=-2, t="2026-11-18T03:00:00Z"),
                 deal("XAUUSD", 0, closing=False, side="DEAL_TYPE_BUY"),
                 {"type": "DEAL_TYPE_BALANCE", "profit": 1000, "time": "2026-11-01T00:00:00Z"}]
        r = M.loss_report(deals, {"balance": 828.0}, 30)
        self.assertEqual(r["trades"], 3)
        self.assertEqual(r["net"], -172.0)
        self.assertAlmostEqual(r["win_rate"], 0.333, places=3)
        self.assertEqual(list(r["by_symbol"])[0], "XAUUSD")
        self.assertEqual(r["deposits_withdrawals"], 1000.0)
        self.assertEqual(r["max_drawdown"], -200.0)
        self.assertEqual(r["worst_trades"][0]["net"], -120.0)
        self.assertEqual(r["net_pct_of_start"], -17.2)
        self.assertTrue(any("XAUUSD lost the most" in n for n in r["notes"]))

    def test_empty(self):
        self.assertEqual(M.loss_report([])["notes"], ["No closed trades in this period."])

    def test_report_pages_history(self):
        http, calls = fake(deals=[deal("EURUSD", 5)])
        r = M.MetaApi(ENV, http).report(7, now=datetime(2026, 11, 22, tzinfo=timezone.utc))
        self.assertEqual(r["trades"], 1)
        url = next(c[1] for c in calls if "/history-deals/" in c[1])
        self.assertIn("2026-11-15T00%3A00%3A00.000Z", url)


class DemoSetupTests(unittest.TestCase):
    """mina's MT5 demo guide (25 Nov 2026): METAAPI_MT5_DEMO_ACCOUNT_ID / METAAPI_MODE names and the verify script."""

    def test_demo_account_alias(self):
        http, calls = fake()
        api = M.MetaApi({"METAAPI_TOKEN": "t", "METAAPI_MT5_DEMO_ACCOUNT_ID": "demo-uuid"}, http)
        api.account()
        self.assertIn("/accounts/demo-uuid/", calls[-1][1])

    def test_live_never_uses_the_demo_alias(self):
        from trading import one_account
        e = one_account.mt5_env({"METAAPI_TOKEN": "t", "METAAPI_MT5_DEMO_ACCOUNT_ID": "demo-uuid",
                                 "METAAPI_LIVE_ACCOUNT_ID": "live-uuid"}, live=True)
        self.assertEqual(e["METAAPI_ACCOUNT_ID"], "live-uuid")
        self.assertNotIn("METAAPI_MT5_DEMO_ACCOUNT_ID", e)

    def _verify(self):
        import sys
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
        import verify_metaapi_demo
        return verify_metaapi_demo.verify

    def test_verify_script_reports_demo_equity_and_drift(self):
        http, _ = fake()
        r = self._verify()({"METAAPI_TOKEN": "t", "METAAPI_MT5_DEMO_ACCOUNT_ID": "d"}, "EURUSD", 1.2, http)
        self.assertTrue(r["ok"])
        self.assertTrue(r["demo"])
        self.assertEqual(r["account"]["equity"], 860.0)
        self.assertEqual(r["drift"]["gap_pct"], 0.0)
        self.assertTrue(r["drift"]["within"])
        self.assertNotIn("t", [str(v) for v in r["account"].values()])

    def test_verify_script_flags_a_real_account(self):
        http, _ = fake(account_type="ACCOUNT_TRADE_MODE_REAL")
        r = self._verify()({"METAAPI_TOKEN": "t", "METAAPI_ACCOUNT_ID": "x"}, "EURUSD", None, http)
        self.assertFalse(r["ok"])
        self.assertIn("NOT a demo", r["error"])


if __name__ == "__main__":
    unittest.main()
