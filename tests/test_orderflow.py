import json
import random
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory

from trading.atlas_sources import AtlasSources
from trading.dashboard import EventHub, make_handler
from trading.orderflow import BUY, SELL, OrderFlowBook, OrderFlowDesk, Trade, auto_tick, volume_profile
from trading.street_view import CITIES, HOUSES, StreetView, find_markets, lexicon_read


def trend(bias, seed=1, tick=0.5):
    rnd = random.Random(seed)
    b = OrderFlowBook("X", tick=tick, bar_seconds=60)
    price = 100.0
    for i in range(1200):
        side = BUY if rnd.random() < bias else SELL
        price += tick * (1 if side == BUY else -1) * (rnd.random() < 0.3)
        b.add(Trade(i, price, rnd.uniform(0.5, 2), side))
    return b


class OrderFlowTest(unittest.TestCase):
    def test_footprint_and_delta(self):
        b = OrderFlowBook("X", tick=1.0)
        b.add(Trade(0, 100, 5, BUY)); b.add(Trade(1, 100, 2, SELL)); b.add(Trade(2, 101, 3, BUY))
        bar = b.bars[-1]
        self.assertEqual((bar.levels[100.0].ask, bar.levels[100.0].bid), (5, 2))
        self.assertEqual((bar.delta, bar.volume, bar.poc, b.cum_delta), (6, 10, 100.0, 6))

    def test_new_bar_cvd_and_late_trades(self):
        b = OrderFlowBook("X", tick=1.0)
        b.add(Trade(0, 100, 5, BUY)); b.add(Trade(61, 100, 2, SELL))
        self.assertEqual((len(b.bars), list(b.cvd), b.cum_delta), (2, [5], 3))
        b.add(Trade(10, 100, 9, BUY))
        self.assertEqual(b.cum_delta, 3)

    def test_tick_rule(self):
        b = OrderFlowBook("X", tick=1.0)
        for ts, p in ((0, 100), (1, 101), (2, 100)):
            b.add(Trade(ts, p, 1))
        lv = b.bars[-1].levels
        self.assertEqual((lv[101.0].ask, lv[100.0].bid), (1, 1))

    def test_volume_profile(self):
        prof = volume_profile({1: 1, 2: 2, 3: 10, 4: 3, 5: 1})
        self.assertEqual((prof["poc"], prof["volume"]), (3, 17))
        self.assertTrue(prof["val"] <= 3 <= prof["vah"])
        self.assertIsNone(volume_profile({})["poc"])

    def test_stacked_imbalance(self):
        b = OrderFlowBook("X", tick=1.0)
        for p in (100, 101, 102, 103):
            b.add(Trade(0, p, 10, BUY)); b.add(Trade(0, p, 1, SELL))
        buys, sells = b.bars[-1].stacked(1.0)
        self.assertEqual((buys, sells), ([[100.0, 103.0]], []))

    def test_big_trades(self):
        b = OrderFlowBook("X", tick=1.0)
        for i in range(50):
            b.add(Trade(i * 0.1, 100, 1 + (i % 3) * 0.1, BUY))
        b.add(Trade(6, 100, 50, SELL))
        self.assertTrue(any(t.size == 50 for t in b.bars[-1].big_trades))

    def test_equal_sizes_are_never_big(self):
        b = OrderFlowBook("X", tick=1.0)
        for i in range(60):
            b.add(Trade(i, 100 + i % 3, 1.0))
        self.assertFalse(any(bar.big_trades for bar in b.bars))

    def test_view_reads_buyers_and_sellers(self):
        up, down = trend(0.7).view(), trend(0.3).view()
        self.assertGreater(up["direction"], 0.2)
        self.assertLess(down["direction"], -0.2)
        self.assertTrue(up["reasons"])

    def test_auto_tick_and_desk(self):
        self.assertEqual((auto_tick(60000), auto_tick(25)), (20, 0.01))
        d = OrderFlowDesk()
        self.assertFalse(d.view("BTC-USD")["ready"])
        d.add("BTC-USD", Trade(0, 60000, 1, BUY))
        self.assertEqual(d.book("BTC-USD").tick, 20)
        self.assertIn("BTC-USD", d.snapshot())


class StreetViewTest(unittest.TestCase):
    def test_twenty_houses(self):
        self.assertEqual(len({h["id"] for h in HOUSES}), 20)
        for c in CITIES:
            self.assertEqual(sum(h["city"] == c for h in HOUSES), 5)

    def test_reading(self):
        self.assertEqual(set(find_markets("overweight gold, upside in bitcoin")), {"GOLD", "BTC"})
        self.assertGreater(lexicon_read("upgrade to overweight; strong upside")[0], 0)
        self.assertLess(lexicon_read("downgrade, recession risk")[0], 0)
        self.assertEqual(lexicon_read("nothing here"), (0.0, 0.0))

    def test_view_and_latest_call(self):
        sv = StreetView(reader=None)
        sv.add_note("gs", "Bullish on gold, raise target, upside", ts=0)
        sv.add_note("dbs", "Gold rally to continue, buy", ts=0)
        sv.add_note("cicc", "Gold looks weak, downside risk", ts=0)
        v = sv.view("GOLD", now=0)
        self.assertEqual(v["houses"], 3)
        self.assertGreater(v["direction"], 0)
        self.assertEqual(set(v["by_city"]), {"New York", "Singapore", "China"})
        sv.add_note("gs", "gold bearish, sell", ts=1); sv.add_note("dbs", "gold bearish, sell", ts=1)
        self.assertLess(sv.view("GOLD", now=1)["direction"], 0)
        self.assertFalse(sv.view("OIL")["ready"])

    def test_scoring(self):
        sv = StreetView(reader=None)
        sv.add_note("jpm", "bitcoin bullish, buy", ts=0, prices={"BTC": 100})
        sv.add_note("ms", "bitcoin bearish, sell", ts=0, prices={"BTC": 100})
        self.assertEqual(sv.score({"BTC": 110}, now=25 * 3600), 2)
        self.assertGreater(sv.records["jpm"].hit_rate, sv.records["ms"].hit_rate)
        self.assertEqual(sv.score({"BTC": 110}, now=26 * 3600), 0)

    def test_claude_reader_and_roundtrip(self):
        class Fake:
            def read(self, text):
                return [{"market": "OIL", "direction": -0.8, "confidence": 0.9, "summary": "oil lower"}]
        self.assertEqual(StreetView(reader=Fake()).add_note("uob", "x")[0].market, "OIL")
        sv = StreetView(reader=None)
        with self.assertRaises(ValueError):
            sv.add_note("nope", "x")
        sv.add_note("hsbc", "sterling bullish, buy", ts=1)
        sv2 = StreetView(reader=None); sv2.load_json(sv.to_json())
        self.assertEqual((len(sv2.calls), len(sv.board())), (1, 20))


class AtlasSourcesTest(unittest.TestCase):
    def desk(self, bias):
        rnd = random.Random(3)
        d, price = OrderFlowDesk({"BTC-USD": 1.0}), 100.0
        for i in range(900):
            side = BUY if rnd.random() < bias else SELL
            price += (1 if side == BUY else -1) * (rnd.random() < 0.3)
            d.add("BTC-USD", Trade(i, price, rnd.uniform(0.5, 2), side))
        return d

    def test_no_data_atlas_decides(self):
        r = AtlasSources(street=StreetView(reader=None)).check("BTC-USD", "buy")
        self.assertEqual((r["approve"], r["multiplier"]), (True, 1.0))

    def test_agree_boosts_disagree_vetoes(self):
        sv = StreetView(reader=None)
        sv.add_note("gs", "bitcoin bullish, buy, upside, strong")
        a = AtlasSources(flow=self.desk(0.75), street=sv)
        buy, sell = a.check("BTC-USD", "buy"), a.check("BTC-USD", "sell")
        self.assertTrue(buy["approve"]); self.assertGreater(buy["multiplier"], 1.0)
        self.assertFalse(sell["approve"]); self.assertEqual(sell["multiplier"], 0.0)

    def test_price_ticks_then_real_feed(self):
        a = AtlasSources(street=StreetView(reader=None))
        for i in range(10):
            a.on_prices({"BTC-USD": 60000 + i * 20, "bad": "x"}, ts=i)
        self.assertEqual(a.page_data()["books"]["BTC-USD"]["feed"], "price ticks")
        used = a.on_trades("BTC-USD", [{"ts": 100, "price": 60100, "size": 0.5, "side": "buy"}, {"bad": 1}])
        self.assertEqual(used, 1)
        a.on_prices({"BTC-USD": 1}, ts=101)  # ignored once a real feed exists
        book = a.page_data()["books"]["BTC-USD"]
        self.assertEqual((book["feed"], book["profile"]["volume"]), ("trades", 0.5))


class OrderFlowWebTest(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.hub = EventHub(Path(self.tmp.name))
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.hub, env={}))
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self):
        self.server.shutdown(); self.server.server_close(); self.tmp.cleanup()

    def get(self, path):
        with urllib.request.urlopen(self.base + path) as r:
            return r.status, r.read()

    def post(self, path, body):
        req = urllib.request.Request(self.base + path, json.dumps(body).encode(),
                                     {"Content-Type": "application/json"})
        with urllib.request.urlopen(req) as r:
            return json.loads(r.read())

    def test_ticks_feed_page_and_atlas(self):
        for i in range(5):
            self.hub.add({"type": "tick", "shard": 0, "tick": i + 1, "ts": i * 30.0, "pnl": 0,
                          "prices": {"BTC-USD": 60000 + i * 40}})
        status, page = self.get("/orderflow")
        self.assertEqual(status, 200)
        self.assertIn(b"/orderflow.js", page)
        self.assertEqual(self.get("/orderflow.js")[0], 200)
        data = json.loads(self.get("/api/orderflow")[1])
        self.assertIn("BTC-USD", data["books"])
        self.assertEqual(len(data["houses"]), 20)
        note = self.post("/api/street/note", {"house": "dbs", "text": "bitcoin bullish, buy, upside"})
        self.assertEqual(note["calls"][0]["market"], "BTC")
        check = json.loads(self.get("/api/atlas/check?symbol=BTC-USD&side=buy")[1])
        self.assertIn("approve", check)
        used = self.post("/api/orderflow/trades",
                         {"symbol": "ETH-USD", "trades": [{"ts": 1, "price": 2400, "size": 2, "side": "sell"}]})
        self.assertEqual(used["used"], 1)


if __name__ == "__main__":
    unittest.main()
