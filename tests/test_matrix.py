"""Tests for the Whole Matrix desk: regimes, timeframes, stat arb, news filter, end to end."""

import io
import json
import random
import unittest
from collections import Counter

from trading import config as ats_config
from trading import matrix
from trading.events import EventLog
from trading.regimes import REGIMES, RegimeDetector, kmeans, name_clusters
from trading.xmarket import ASSETS, PAIRS

CFG = ats_config.load()


class StubFeed:
    def __init__(self, news: dict):
        self.news = news

    def read(self, asset, tick):
        return self.news.get(asset)


def desk(feed=None) -> matrix.MatrixDesk:
    return matrix.MatrixDesk(CFG, EventLog("matrix", None, io.StringIO()), feed=feed)


class RegimeTests(unittest.TestCase):
    def test_kmeans_finds_separated_clusters(self):
        rng = random.Random(1)
        centers = [(0, 0), (10, 0), (0, 10), (10, 10)]
        points = [(cx + rng.gauss(0, 0.5), cy + rng.gauss(0, 0.5)) for cx, cy in centers for _ in range(50)]
        centroids, labels = kmeans(points, 4, random.Random(2))
        for cx, cy in centers:
            self.assertTrue(any(abs(a - cx) < 1 and abs(b - cy) < 1 for a, b in centroids))
        self.assertEqual(sorted(Counter(labels).values()), [50, 50, 50, 50])

    def test_clusters_get_the_right_names(self):
        # (volatility, efficiency)
        names = name_clusters([(1, 1), (1, 9), (9, 1), (9, 9)])
        self.assertEqual(names, {0: "low_vol_ranging", 1: "low_vol_trending",
                                 2: "high_vol_ranging", 3: "high_vol_trending"})

    def test_detector_classifies_after_fit(self):
        det = RegimeDetector(seed=3)
        rng = random.Random(4)
        for v, e in ((0.001, 0.1), (0.001, 0.8), (0.004, 0.1), (0.004, 0.8)):
            for _ in range(30):
                det.observe((v * rng.uniform(0.9, 1.1), e * rng.uniform(0.9, 1.1)))
        self.assertIsNone(det.classify((0.001, 0.1)))  # not fitted yet
        self.assertTrue(det.fit())
        self.assertEqual(det.classify((0.004, 0.85)), "high_vol_trending")
        self.assertEqual(det.classify((0.001, 0.05)), "low_vol_ranging")


class TimeframeTests(unittest.TestCase):
    def test_confluence_needs_every_timeframe(self):
        tf = matrix.Timeframes()
        for t in range(1, 22 * 1440):
            tf.update(t, 100 * (1.0001 ** t))  # steady uptrend
        self.assertEqual(tf.momentum(), {"1m": 1, "15m": 1, "1h": 1, "1d": 1})
        self.assertEqual(tf.confluence(), 1)
        for t in range(22 * 1440, 22 * 1440 + 120):
            tf.update(t, 100 * (1.0001 ** (22 * 1440)) * (0.999 ** (t - 22 * 1440)))  # 2-hour dip
        m = tf.momentum()
        self.assertEqual((m["1m"], m["1d"]), (-1, 1))  # short timeframes turned, daily has not
        self.assertEqual(tf.confluence(), 0)  # so no trade

    def test_not_ready_means_no_signal(self):
        tf = matrix.Timeframes()
        for t in range(1, 500):
            tf.update(t, 100 + t)
        self.assertEqual(tf.momentum()["1d"], 0)
        self.assertEqual(tf.confluence(), 0)


class StatArbTests(unittest.TestCase):
    def test_rolling_ols_recovers_hedge_ratio_and_sign(self):
        rng = random.Random(5)
        for true_beta in (2.0, -0.5):
            rp = matrix.RollingPair(window=500)
            x = 0.0
            for _ in range(800):
                x += rng.gauss(0, 0.01)
                rp.add(true_beta * x + rng.gauss(0, 0.001), x)
            beta, _ = rp.stats()
            self.assertAlmostEqual(beta, true_beta, delta=0.05)
        rp.add(true_beta * x + 0.05, x)  # sudden rich spread
        self.assertGreater(rp.stats()[1], 3)

    def test_entry_exit_stop_and_cooldown(self):
        arb = matrix.StatArb()
        pair = (PAIRS[0][0], PAIRS[0][1])
        z = {"v": 0.0}
        for key, rp in arb.pairs.items():
            rp.stats = (lambda k=key: (1.5, z["v"]) if k == pair else None)
        z["v"] = 2.5
        self.assertEqual(arb.decide(1, set()), [(*pair, -1, "entry")])  # rich: short the spread
        z["v"] = 0.3
        self.assertEqual(arb.decide(2, set()), [(*pair, 0, "reverted")])
        z["v"] = -2.5
        self.assertEqual(arb.decide(3, set()), [(*pair, 1, "entry")])
        z["v"] = -4.5
        self.assertEqual(arb.decide(4, set()), [(*pair, 0, "stop")])
        z["v"] = -2.5
        self.assertEqual(arb.decide(5, set()), [])  # cooling down after a stop
        self.assertEqual(arb.decide(4 + matrix.STOP_COOLDOWN, {pair}), [])  # blocked by regime
        self.assertEqual(arb.decide(4 + matrix.STOP_COOLDOWN, set()), [(*pair, 1, "entry")])

    def test_targets_are_hedged_with_beta(self):
        arb = matrix.StatArb()
        pair = (PAIRS[2][0], PAIRS[2][1])
        arb.side[pair], arb.last[pair] = 1, (-0.8, -2.5)
        t = arb.targets({})[pair]
        self.assertEqual(t[pair[0]], matrix.ARB_SIZE_USD)
        self.assertAlmostEqual(t[pair[1]], 0.8 * matrix.ARB_SIZE_USD)  # negative beta: both legs long


class TrendRuleTests(unittest.TestCase):
    def setUp(self):
        self.d = desk()
        for a in ASSETS:
            self.d.tf[a].confluence = lambda: 1  # all timeframes agree: up

    def test_trend_trades_only_in_trending_regimes_and_half_size_in_high_vol(self):
        self.d.regime = {a: "low_vol_ranging" for a in ASSETS}
        self.assertEqual(self.d._trend_targets(), {})
        self.d.regime["GOLD"], self.d.regime["BTC"] = "low_vol_trending", "high_vol_trending"
        self.assertEqual(self.d._trend_targets(), {"GOLD": matrix.TREND_SIZE_USD, "BTC": matrix.TREND_SIZE_USD / 2})

    def test_high_vol_trend_blocks_new_arb_entries(self):
        self.d.regime = {a: "low_vol_ranging" for a in ASSETS}
        self.d.regime["BTC"] = "high_vol_trending"
        calls = []
        self.d.arb.decide = lambda tick, blocked: calls.append(blocked) or []
        self.d.step()
        self.assertEqual(calls[0], {("NDX", "BTC")})


class NewsFilterTests(unittest.TestCase):
    def test_strong_contrary_news_suppresses(self):
        d = desk(StubFeed({"GOLD": matrix.Sentiment(-0.9, 0.9)}))
        self.assertEqual(d._apply_news("trend_confluence", {"GOLD": 30_000}, "GOLD"), {})

    def test_strong_contrary_news_exits_open_position(self):
        out = io.StringIO()
        d = matrix.MatrixDesk(CFG, EventLog("matrix", None, out), feed=StubFeed({"BTC": matrix.Sentiment(-1.0, 0.8)}))
        d._virtual["stat_arb"]["BTC"] = 0.5  # already long BTC through a pair
        self.assertEqual(d._apply_news("stat_arb", {"NDX": -25_000, "BTC": 25_000}, "NDX/BTC"), {})
        event = json.loads(out.getvalue().splitlines()[-1])
        self.assertEqual((event["action"], event["trade"]), ("exit", "NDX/BTC"))  # whole pair exits

    def test_mild_contrary_news_downsizes_every_leg(self):
        d = desk(StubFeed({"AUDUSD": matrix.Sentiment(0.6, 0.5)}))  # weight 0.3, bullish vs a short leg
        out = d._apply_news("stat_arb", {"GOLD": 25_000, "AUDUSD": -25_000}, "GOLD/AUDUSD")
        self.assertAlmostEqual(out["GOLD"], 25_000 * 0.7)
        self.assertAlmostEqual(out["AUDUSD"], -25_000 * 0.7)

    def test_aligned_or_irrelevant_news_changes_nothing(self):
        d = desk(StubFeed({"GOLD": matrix.Sentiment(0.9, 0.9), "WTI": matrix.Sentiment(-0.9, 0.1)}))
        self.assertEqual(d._apply_news("trend_confluence", {"GOLD": 30_000}, "GOLD"), {"GOLD": 30_000})
        self.assertEqual(d._apply_news("trend_confluence", {"WTI": 30_000}, "WTI"), {"WTI": 30_000})


class EndToEndTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        out = io.StringIO()
        cls.desk = matrix.run({"TICKS": "4000", "TICK_SECONDS": "0"}, stream=out)
        cls.events = [json.loads(line) for line in out.getvalue().splitlines()]

    def test_runs_all_four_layers(self):
        kinds = Counter(e["type"] for e in self.events)
        self.assertEqual(kinds["tick"], 4000)
        self.assertGreater(kinds["order"], 0)
        warmup = next(e for e in self.events if e["type"] == "warmup")
        self.assertEqual(warmup["sim_days"], 21.0)
        self.assertGreater(warmup["regime_fits"], 0)
        regimes = {v["regime"] for e in self.events if e["type"] == "matrix" for v in e["assets"].values()}
        self.assertGreaterEqual(len(regimes & set(REGIMES)), 3)
        reasons = {e["reason"] for e in self.events if e["type"] == "signal"}
        self.assertIn("entry", reasons)
        self.assertNotEqual(self.desk.pnl["stat_arb"], 0)

    def test_every_order_passed_jev_and_limits(self):
        orders = sum(1 for e in self.events if e["type"] == "order")
        self.assertEqual(self.desk.beta.jev.counts["approved"], orders)
        prices = {a: q.price for a, q in self.desk.quotes.items()}
        for a in ASSETS:
            self.assertLessEqual(abs(self.desk.broker.position(a) * prices[a]),
                                 self.desk.risk.limits.max_position_value * 1.2)
        self.assertLessEqual(max(e["gross"] for e in self.events if e["type"] == "tick"),
                             self.desk.risk.limits.max_gross_exposure * 1.2)

    def test_loops_stay_under_target(self):
        self.assertEqual(self.desk.beta.overruns, 0)


class SlippageReportTests(unittest.TestCase):
    def test_jev_gets_slippage_in_percent(self):
        d = desk()
        d.warm_up(3 * 1440)
        seen = []
        real = d.beta.jev.validate

        def spy(proposal):
            seen.append(proposal)
            return real(proposal)

        d.beta.jev.validate = spy
        for _ in range(300):
            d.step()
        self.assertTrue(seen)
        for p in seen:
            pct = p["qty"] * p["price"] / p["depth_usdc"] * 100
            self.assertAlmostEqual(p["expected_slippage_pct"], pct)
            self.assertLessEqual(p["expected_slippage_pct"], CFG.risk.slippage_tolerance_pct)


if __name__ == "__main__":
    unittest.main()


class LiveCrossMarketTests(unittest.TestCase):
    def test_real_price_feed_drives_the_desk(self):
        import io
        import math as _m
        from trading import config as ats_config
        from trading.events import EventLog
        from trading.matrix import MatrixDesk
        from trading.xmarket import ASSETS, YAHOO, LiveCrossMarket

        base = {t: ASSETS[a][0] for a, t in YAHOO.items()}

        def fetch(ticker, interval, range_):
            n = 2000 if range_ != "1d" else 5
            return [(1_800_000_000 + 60 * i, base[ticker] * (1 + 0.01 * _m.sin(i / 50))) for i in range(n)]

        market = LiveCrossMarket(fetch=fetch, start_thread=False)
        desk = MatrixDesk(ats_config.load(None), EventLog("matrix", None, io.StringIO()))
        desk.market = market
        desk.warm_up(1500)
        self.assertEqual(desk.sim_tick, 1500)  # warmed on the real-bar history
        market.poll_once()
        for _ in range(5):
            desk.step()
        self.assertEqual(set(desk.quotes), set(ASSETS))

    def test_missing_asset_waits(self):
        from trading.xmarket import LiveCrossMarket
        m = LiveCrossMarket(fetch=lambda t, i, r: [] if t == "^NDX" else [(1.0, 2.0)], start_thread=False)
        m.poll_once()
        self.assertNotIn("NDX", m.tick())
        self.assertEqual(m.history("1d"), [])


class LiveNewsTests(unittest.TestCase):
    def test_headlines_move_the_assets_news_score(self):
        from trading.matrix import LiveNews

        class Feed:
            def __init__(self):
                self.batches = [[{"symbol": "GOLD", "text": "GOLD: Gold price surges to record high", "weight": 1.0},
                                 {"symbol": "USDCAD", "text": "USDCAD: Canadian dollar surges", "weight": 0.5}], []]

            def tick(self, drift, tick):
                return self.batches.pop(0) if self.batches else []

        news = LiveNews(feed=Feed())
        gold = news.read("GOLD", 1)
        self.assertGreater(gold.score, 0)
        self.assertLess(news.read("USDCAD", 1).score, 0)  # a stronger Canadian dollar means USDCAD falls
        self.assertIsNone(news.read("NDX", 1))
        later = news.read("GOLD", 100)  # fades with time
        self.assertTrue(later is None or later.score < gold.score)

    def test_gold_feeds_use_the_futures_ticker(self):
        from trading.news_feed import match_symbol, symbol_feeds
        self.assertTrue(any("GC%3DF" in u for u in symbol_feeds("GOLD")))
        self.assertEqual(match_symbol("Gold price climbs as dollar slips", ["GOLD", "NDX"]), "GOLD")
        self.assertIsNone(match_symbol("Goldman Sachs earnings", ["GOLD"]))
