import json
import unittest
from datetime import datetime, timedelta, timezone

from trading import session_desks as sd
from tests import test_web


def utc(s):
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)


class SessionDeskTests(unittest.TestCase):
    def test_follow_the_sun_on_a_weekday(self):
        # Monday 23 Nov 2026 (Melbourne on daylight time, +11)
        self.assertEqual(sd.lead(utc("2026-11-23T03:00:00")), "asia")      # 14:00 Melbourne
        self.assertEqual(sd.lead(utc("2026-11-23T10:00:00")), "london")    # 21:00 Melbourne
        self.assertEqual(sd.lead(utc("2026-11-23T15:00:00")), "newyork")   # 02:00 Melbourne, London still open
        self.assertEqual(sd.lead(utc("2026-11-23T22:30:00")), "sydney")    # 09:30 Melbourne Tuesday

    def test_desks_cover_every_hour_of_the_week(self):
        for start in ("2026-11-24T00:00:00", "2026-07-14T00:00:00"):  # southern summer and winter
            self.assertEqual(sd.coverage(utc(start)), 24.0, start)

    def test_hours_in_melbourne_time(self):
        d = sd.desk("london", utc("2026-11-23T03:00:00"))
        self.assertEqual(d["hours"]["melbourne"], "19:00 to 04:00 Melbourne")
        d = sd.desk("sydney", utc("2026-07-14T03:00:00"))
        self.assertEqual(d["hours"]["melbourne"], "07:00 to 16:00 Melbourne")

    def test_weekend_is_crypto_only_at_half_size(self):
        b = sd.board(utc("2026-11-21T12:00:00"))  # Saturday
        self.assertTrue(b["weekend"])
        leader = next(d for d in b["desks"] if d["leading"])
        self.assertEqual(leader["size"], 0.5)
        self.assertTrue(all(m["symbol"] in sd.CRYPTO for m in leader["markets"]))
        self.assertTrue(all(not d["open"] for d in b["desks"]))

    def test_handoffs_go_round_the_world(self):
        hs = sd.handoffs(utc("2026-11-23T03:00:00"), 4)
        self.assertEqual([(h["from"], h["to"]) for h in hs],
                         [("asia", "europe"), ("europe", "london"), ("london", "newyork"), ("newyork", "sydney")])

    def test_europe_desk_opens_an_hour_before_london(self):
        d = sd.desk("europe", utc("2026-11-23T03:00:00"))
        self.assertEqual(d["city"], "Frankfurt")
        self.assertEqual(d["hours"]["melbourne"], "18:00 to 03:00 Melbourne")
        self.assertEqual(sd.lead(utc("2026-11-23T07:30:00")), "europe")   # 08:30 Frankfurt, London not open yet

    def test_every_desk_has_all_four_departments(self):
        d = sd.desk("asia", utc("2026-11-23T03:00:00"))
        self.assertEqual(set(d["crew"]), {"trend", "reversion", "research", "risk"})
        self.assertEqual(d["crew_total"] * 4, sum(sd._crew(None).values()) * 4)

    def test_trades_go_to_the_leading_desk(self):
        t = utc("2026-11-23T10:00:00").timestamp()
        b = sd.board(utc("2026-11-23T11:00:00"), trades=[{"ts": t, "won": True, "pnl": 3.5, "symbol": "EURUSD"}])
        books = {d["key"]: d["book"]["trades"] for d in b["desks"]}
        self.assertEqual(books, {"sydney": 0, "asia": 0, "europe": 0, "london": 1, "newyork": 0})

    def test_real_money_stays_locked(self):
        env = {"METAAPI_TOKEN": "x", "METAAPI_ACCOUNT_ID": "y", "MT5_MODE": "demo"}
        self.assertEqual(sd.route(env), {"orders_to": "paper", "mt5_demo": "ready", "real_money": "locked"})
        self.assertEqual(sd.route({"METAAPI_TOKEN": "x", "METAAPI_ACCOUNT_ID": "y", "MT5_MODE": "live"})["mt5_demo"], "not set up")
        self.assertEqual(sd.route({"METAAPI_TOKEN": "x"})["mt5_demo"], "ready")
        self.assertEqual(sd.route({})["mt5_demo"], "not set up")


class DeskPageTests(unittest.TestCase):
    setUp = test_web.ServerTests.setUp
    tearDown = test_web.ServerTests.tearDown
    request = test_web.ServerTests.request
    login = test_web.ServerTests.login
    cookie_from = test_web.ServerTests.cookie_from

    def test_desk_pages(self):
        resp, _ = self.request("GET", "/desks/london")
        self.assertEqual((resp.status, resp.getheader("Location")), (303, "/login"))
        cookie = self.cookie_from(self.login()[0])
        for path in ("/desks", "/desks/sydney", "/desks/asia", "/desks/europe", "/desks/london", "/desks/newyork"):
            resp, body = self.request("GET", path, cookie=cookie)
            self.assertEqual(resp.status, 200, path)
        resp, body = self.request("GET", "/api/desks", cookie=cookie)
        self.assertEqual([d["key"] for d in json.loads(body)["desks"]], ["sydney", "asia", "europe", "london", "newyork"])
        resp, body = self.request("GET", "/api/desks/newyork", cookie=cookie)
        self.assertEqual(json.loads(body)["desk"]["name"], "New York")
        resp, _ = self.request("GET", "/desks/mars", cookie=cookie)
        self.assertEqual(resp.status, 404)


if __name__ == "__main__":
    unittest.main()
