"""Betfair bridge: off without keys, paper by default, real money only with switch + confirm + money rules."""
import json
import tempfile
import unittest

from trading import betfair as B

KEYS = {"BETFAIR_APP_KEY": "app", "BETFAIR_USERNAME": "u", "BETFAIR_PASSWORD": "p"}
CAT = [{"marketId": "1.234", "marketName": "Match Odds", "event": {"name": "Lakers v Celtics"}, "eventType": {"name": "Basketball"},
        "runners": [{"selectionId": 11, "runnerName": "Lakers"}, {"selectionId": 22, "runnerName": "Celtics"}]}]
BOOK = [{"marketId": "1.234", "status": "OPEN", "totalMatched": 50000, "runners": [
    {"selectionId": 11, "ex": {"availableToBack": [{"price": 2.2}], "availableToLay": [{"price": 2.24}]}},
    {"selectionId": 22, "ex": {"availableToBack": [{"price": 1.8}], "availableToLay": [{"price": 1.82}]}}]}]


class FakeBetfair:
    def __init__(self):
        self.calls = []
        self.balance = 2000.0
        self.open_orders = []

    def __call__(self, url, body, headers):
        self.calls.append((url, headers))
        if "login" in url:
            return {"status": "SUCCESS", "token": "tok"}
        req = json.loads(body)
        m = req["method"].split("/")[-1]
        res = {"listMarketCatalogue": CAT, "listMarketBook": BOOK,
               "getAccountFunds": {"availableToBetBalance": self.balance, "exposure": 0},
               "listCurrentOrders": {"currentOrders": self.open_orders},
               "placeOrders": {"status": "SUCCESS", "instructionReports": [{"betId": "999", "sizeMatched": 5.0, "orderStatus": "EXECUTION_COMPLETE"}]},
               "cancelOrders": {"status": "SUCCESS"}}[m]
        return {"jsonrpc": "2.0", "result": res, "id": 1}


def bridge(env):
    fake = FakeBetfair()
    client = B.Betfair(env, post=fake) if B.configured(env) else None
    return B.Bridge(env, tempfile.mkdtemp(), client=client), fake


class Bridge(unittest.TestCase):
    def test_off_without_keys(self):
        b, _ = bridge({})
        s = b.status()
        self.assertFalse(s["configured"])
        self.assertEqual(s["missing"], list(B.ENV_KEYS))
        with self.assertRaises(B.Refused):
            b.place("1.234", 11, "BACK", 5)
        with self.assertRaises(B.NotConnected):
            B.Betfair({})

    def test_logs_in_and_reads_real_prices_without_leaking_keys(self):
        b, fake = bridge(KEYS)
        mk = b.search("Lakers")[0]
        self.assertEqual(mk["runners"][0]["back"], 2.2)
        self.assertEqual(fake.calls[0][0], B.LOGIN_URL)
        self.assertEqual(fake.calls[1][1]["X-Authentication"], "tok")
        self.assertNotIn("p", repr(b.api()).replace("hidden", "").replace("Betfair", ""))

    def test_paper_back_and_cash_out(self):
        b, fake = bridge(KEYS)
        bet = b.place("1.234", 11, "BACK", 10)
        self.assertEqual((bet["mode"], bet["odds"]), ("paper", 2.2))
        done = b.settle_paper(bet["id"])
        self.assertAlmostEqual(done["pnl"], 10 * (2.2 / 2.24 - 1), places=2)
        self.assertFalse(any("placeOrders" in json.dumps(c) for c in fake.calls))

    def test_real_money_gates(self):
        b, _ = bridge(KEYS)
        with self.assertRaisesRegex(B.Refused, "off"):
            b.place("1.234", 11, "BACK", 5, 2.2, live=True, confirm="REAL MONEY")
        env = {**KEYS, "BETFAIR_REAL_MONEY": "on", "BETFAIR_RISK_PCT": "1"}
        b, fake = bridge(env)
        with self.assertRaisesRegex(B.Refused, "live-unlock"):  # switch on, but no Orbit arm promoted yet
            b.place("1.234", 11, "BACK", 5, 2.2, live=True, confirm="REAL MONEY")
        b.unlocked = lambda: True
        with self.assertRaisesRegex(B.Refused, "confirm"):
            b.place("1.234", 11, "BACK", 5, 2.2, live=True, confirm="yes")
        with self.assertRaisesRegex(B.Refused, "A\\$10"):
            b.place("1.234", 11, "BACK", 15, 2.2, live=True, confirm="REAL MONEY")
        with self.assertRaisesRegex(B.Refused, "Odds"):
            b.place("1.234", 11, "BACK", 5, 50, live=True, confirm="REAL MONEY")
        fake.balance = 100.0  # 1% = A$1
        with self.assertRaisesRegex(B.Refused, "balance"):
            b.place("1.234", 11, "BACK", 5, 2.2, live=True, confirm="REAL MONEY")
        fake.balance = 2000.0
        with self.assertRaisesRegex(B.Refused, "exactly"):  # lower case no longer counts (mina, 25 Nov)
            b.place("1.234", 11, "BACK", 5, 2.215, live=True, confirm="real money")
        rec = b.place("1.234", 11, "BACK", 5, 2.215, live=True, confirm="REAL MONEY")
        self.assertEqual((rec["bet_id"], rec["odds"]), ("999", 2.22))  # snapped to a valid Betfair tick
        fake.open_orders = [{"status": "EXECUTABLE"}, {"status": "EXECUTABLE"}]
        with self.assertRaisesRegex(B.Refused, "open real bets"):
            b.place("1.234", 11, "BACK", 5, 2.2, live=True, confirm="REAL MONEY")

    def test_daily_cap(self):
        env = {**KEYS, "BETFAIR_REAL_MONEY": "on", "BETFAIR_DAILY_STAKE": "12", "BETFAIR_RISK_PCT": "10"}
        b, _ = bridge(env)
        b.unlocked = lambda: True
        b.place("1.234", 11, "BACK", 10, 2.2, live=True, confirm="REAL MONEY")
        with self.assertRaisesRegex(B.Refused, "daily"):
            b.place("1.234", 11, "BACK", 5, 2.2, live=True, confirm="REAL MONEY")

    def test_au_prediction_market_guard(self):
        v = B.validate_prediction_market_order
        on = {"BETFAIR_REAL_MONEY": "on"}
        self.assertFalse(v("POLYMARKET", "LIVE", "REAL MONEY", True, 10, env=on)[0])  # never live in Australia
        self.assertTrue(v("polymarket", "paper", "", False, 10)[0])
        self.assertTrue(v("BETFAIR", "PAPER", "", False, 10, env={})[0])
        self.assertIn("SWITCH", v("BETFAIR", "LIVE", "REAL MONEY", True, 10, env={})[1])
        self.assertIn("UNLOCK", v("BETFAIR", "LIVE", "REAL MONEY", False, 10, env=on)[1])
        self.assertIn("CONFIRMATION", v("BETFAIR", "LIVE", "yes", True, 10, env=on)[1])
        self.assertIn("STAKE_CAP", v("BETFAIR", "LIVE", "REAL MONEY", True, 60, env=on)[1])
        self.assertEqual(v("BETFAIR", "LIVE", "REAL MONEY", True, 50, env=on), (True, "PASS_BETFAIR_AU_LIVE"))
        self.assertFalse(v("KALSHI", "PAPER", "", True, 1)[0])
        from trading import platforms
        self.assertFalse(platforms.validate_prediction_market_order("POLYMARKET", "LIVE", "REAL MONEY", True, 1)[0])

    def test_matches_polymarket_picks(self):
        b, _ = bridge(KEYS)
        pick = {"id": "7", "question": "Lakers vs. Celtics", "outcomes": ["Lakers", "Celtics"], "yes": 0.42, "lean": {"side": "YES"}}
        m = b.matches([pick])[0]
        self.assertEqual((m["runner"], m["betfair_pct"], m["gap_pts"]), ("Lakers", 45.5, 3.5))
        self.assertEqual(B.search_terms("Will Nikki Haley win the 2028 Republican presidential nomination?"), "Nikki Haley Republican")

    def test_tick_price(self):
        self.assertEqual(B.tick_price(2.213), 2.22)
        self.assertEqual(B.tick_price(1.005), 1.01)
        self.assertEqual(B.tick_price(7.33), 7.4)


if __name__ == "__main__":
    unittest.main()
