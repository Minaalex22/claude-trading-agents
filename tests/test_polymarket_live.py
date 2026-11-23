"""Polymarket platform: parsing real Gamma markets, the agents' lean, and the paper book."""
import json
import tempfile
import unittest

from trading import polymarket_live as P

RAW = {"id": "6919847", "question": "Will Bitcoin be above $100k on Dec 31?", "outcomes": '["Yes", "No"]',
       "outcomePrices": '["0.62", "0.38"]', "bestBid": 0.61, "bestAsk": 0.63, "volume24hr": 1234567.8,
       "liquidityNum": 335942.0, "endDate": "2099-01-01T00:00:00Z", "oneDayPriceChange": 0.07, "acceptingOrders": True,
       "feesEnabled": True, "feeSchedule": {"rate": 0.01}, "events": [{"title": "Bitcoin above ___?", "slug": "btc-above"}]}


def feed_with(markets, now=lambda: 1_000.0):
    calls = []

    def get(url):
        calls.append(url)
        if "/markets/" in url:
            mid = url.rsplit("/", 1)[1]
            return next(m for m in markets if m["id"] == mid)
        return markets
    f = P.MarketFeed(get=get, now=now)
    f.calls = calls
    return f


class ParseAndLean(unittest.TestCase):
    def test_parses_a_gamma_market(self):
        m = P.parse_market(RAW)
        self.assertEqual((m["id"], m["yes"], m["bid"], m["ask"]), ("6919847", 0.62, 0.61, 0.63))
        self.assertEqual(m["category"], "crypto")
        self.assertEqual(m["url"], "https://polymarket.com/event/btc-above")
        self.assertEqual(m["fee_rate"], 0.01)
        self.assertIsNone(P.parse_market({**RAW, "outcomes": '["A","B","C"]'}))  # two-outcome markets only

    def test_resolution(self):
        m = P.parse_market({**RAW, "closed": True, "outcomePrices": '["1", "0"]'})
        self.assertEqual(P.resolved_outcome(m), 0)
        self.assertIsNone(P.resolved_outcome(P.parse_market(RAW)))

    def test_lean_skips_thin_and_wide_markets_and_explains(self):
        thin = P.parse_market({**RAW, "liquidityNum": 1000})
        self.assertIsNone(P.lean(thin, 0)["side"])
        self.assertIn("thin", P.lean(thin, 0)["skip"])
        long_shot = P.parse_market({**RAW, "outcomePrices": '["0.10","0.90"]', "bestBid": 0.099, "bestAsk": 0.101,
                                    "oneDayPriceChange": 0, "endDate": "2000-01-02T00:00:00Z"})
        lv = P.lean(long_shot, 946_684_800)
        self.assertEqual(lv["side"], "NO")
        self.assertLess(lv["fair"], 0.10)


class Feed(unittest.TestCase):
    def test_falls_back_to_sample_when_polymarket_is_down(self):
        def down(url):
            raise OSError("blocked")
        v = P.MarketFeed(get=down).view()
        self.assertEqual(v["mode"], "sample")
        self.assertIn("not reachable", v["error"])
        self.assertTrue(v["markets"])

    def test_caches_and_filters(self):
        f = feed_with([RAW, {**RAW, "id": "2", "question": "Will the Fed cut rates?", "events": []}])
        v = f.view(category="crypto")
        self.assertEqual([m["id"] for m in v["markets"]], ["6919847"])
        self.assertEqual(f.view(query="fed")["markets"][0]["id"], "2")
        self.assertEqual(len(f.calls), 1)  # second view used the cache
        self.assertTrue(f.calls[0].startswith("https://gamma-api.polymarket.com/markets?"))


class Book(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.clock = [1_000.0]
        self.raw = dict(RAW)
        self.feed = feed_with([self.raw], now=lambda: self.clock[0])
        self.book = P.PaperBook(self.feed, self.dir, now=lambda: self.clock[0])

    def test_bet_uses_real_ask_and_fee_and_caps_size(self):
        pos = self.book.bet("6919847", "yes", 100)
        self.assertEqual(pos["price"], 0.63)
        fee = 0.01 * 0.63 * 0.37
        self.assertAlmostEqual(pos["shares"], 100 / (0.63 + fee), places=3)
        no = self.book.bet("6919847", "NO", 50)
        self.assertEqual(no["price"], 0.39)  # 1 - best bid
        with self.assertRaises(P.Refused):
            self.book.bet("6919847", "YES", 600)  # over 5% of $10k
        with self.assertRaises(P.Refused):
            self.book.bet("6919847", "YES", 10, live=True)
        with self.assertRaises(P.Refused):
            self.book.bet("6919847", "MAYBE", 10)
        with self.assertRaises(P.Refused):
            self.book.bet("6919847", "YES", float("nan"))
        s = self.book.snapshot()
        self.assertEqual(len(s["open"]), 2)
        self.assertAlmostEqual(s["cash"], 9850.0)

    def test_sell_at_bid_and_keeps_state_on_disk(self):
        pos = self.book.bet("6919847", "YES", 100)
        done = self.book.sell(pos["id"])
        self.assertEqual(done["exit"], 0.61)
        self.assertLess(done["pnl"], 0)  # crossing the spread costs money
        again = P.PaperBook(self.feed, self.dir)
        self.assertEqual(len(again.snapshot()["closed"]), 1)
        with self.assertRaises(P.Refused):
            self.book.sell(pos["id"])

    def test_resolution_pays_winners_and_scores_agents(self):
        self.feed.markets()
        self.book.record_leans(self.feed.cache["markets"])
        yes = self.book.bet("6919847", "YES", 100)
        no = self.book.bet("6919847", "NO", 100)
        self.raw.update(closed=True, outcomePrices='["1", "0"]')
        self.clock[0] += 400
        paid = {p["id"]: p for p in self.book.settle()}
        self.assertAlmostEqual(paid[yes["id"]]["proceeds"], yes["shares"], places=1)
        self.assertEqual(paid[no["id"]]["proceeds"], 0)
        s = self.book.snapshot()
        self.assertEqual((s["bets"], s["wins"], s["win_rate"]), (2, 1, 50.0))
        if s["agents"]["tracked"]:
            self.assertEqual(s["agents"]["scored"], 1)
            self.assertIsNotNone(s["agents"]["brier"])

    def test_reset_needs_nothing_open_to_restart(self):
        self.book.bet("6919847", "YES", 100)
        s = self.book.reset()
        self.assertEqual((s["cash"], len(s["open"])), (10_000.0, 0))


if __name__ == "__main__":
    unittest.main()
