"""News fixes: style split, time-based fade, macro shock, pipeline into the desk (mina's fourth batch)."""

import types
import unittest

from trading import hooks, reading_room
from trading.news_pipeline import NewsPipeline
from trading.reading_room import ReadingRoom, View, opposing_news


class FadeTests(unittest.TestCase):
    def test_news_halves_every_90_minutes_and_macro_every_240(self):
        room = ReadingRoom(["AAPL", "SPY"])
        t0 = 1_700_000_000
        room.update({"AAPL": 1, "SPY": 1}, [{"symbol": "AAPL", "text": "AAPL: shares surge on record sales"},
                                            {"symbol": "SPY", "text": "SPY: Fed rate decision above forecast"}], t0)
        a0, s0 = room.news["AAPL"], room.news["SPY"]
        views = room.update({"AAPL": 1, "SPY": 1}, [], t0 + 90 * 60)
        self.assertAlmostEqual(room.news["AAPL"], a0 / 2)
        self.assertAlmostEqual(room.news["SPY"], s0 * 0.5 ** (90 / 240))
        self.assertAlmostEqual(views["AAPL"].news_age_mins, 90)
        self.assertTrue(room.macro["SPY"])
        self.assertFalse(views["SPY"].is_macro_shock)  # 90 minutes old: no longer fresh

    def test_fresh_macro_is_a_shock(self):
        room = ReadingRoom(["SPY"])
        views = room.update({"SPY": 1}, [{"symbol": "SPY", "text": "SPY: tariffs escalate"}], 1_700_000_000)
        self.assertTrue(views["SPY"].is_macro_shock)

    def test_simulator_still_fades_per_tick(self):
        room = ReadingRoom(["X"])
        room.update({"X": 1}, [{"symbol": "X", "text": "X: shares surge"}])
        n = room.news["X"]
        room.update({"X": 1}, [])
        self.assertAlmostEqual(room.news["X"], n * reading_room.NEWS_KEEP)


class SplitTests(unittest.TestCase):
    def test_only_fresh_or_macro_news_stops_reversion(self):
        old = View(direction=-0.8, chart=-1.0, news=-0.5, news_age_mins=120)
        fresh = View(direction=-0.3, chart=0.0, news=-0.5, news_age_mins=20)
        shock = View(direction=-0.3, news=-0.5, news_age_mins=200, is_macro_shock=True)
        self.assertFalse(opposing_news(old, 1.0))  # chart against a reversion buy is the setup
        self.assertTrue(opposing_news(fresh, 1.0))
        self.assertTrue(opposing_news(shock, 1.0))
        self.assertFalse(opposing_news(View(news=-0.2, news_age_mins=5), 1.0))  # too weak

    def test_desk_hook_by_style(self):
        view = View(direction=-0.8, chart=-1.0, news=-0.1, news_age_mins=300)
        rev = hooks.OrderCheck("X", 1.0, 0.0, 100.0, view=view, style="reversion")
        trend = hooks.OrderCheck("X", 1.0, 0.0, 100.0, view=view, style="trend")
        self.assertEqual(hooks.against_the_room(rev)[0], 1.0)
        self.assertEqual(hooks.against_the_room(trend)[0], 0.0)


class PipelineDeskTests(unittest.TestCase):
    def test_only_sure_confirmed_reads_count_and_doubt_brakes(self):
        p = NewsPipeline(seed=1)
        t0 = 1_700_000_000
        h = {"symbol": "AAPL", "text": "AAPL: beats estimates, record buyback, upgrade", "confirmed": False}
        p.feed([h], {}, t0)
        rec = p.log[-1]
        self.assertIsNone(p.desk_lean("AAPL", t0, 0.0))  # one source only
        p.feed([{**h, "confirmed": True}], {}, t0 + 60)
        self.assertTrue(rec["confirmed"])
        lean = p.desk_lean("AAPL", t0, 0.0)
        self.assertAlmostEqual(lean, rec["final_lean"])
        self.assertIsNone(p.desk_lean("AAPL", t0, 1.01))  # never sure enough
        p.human.append({"symbol": "AAPL", "final_conf": 0.4, "at": t0})
        self.assertTrue(p.doubtful("AAPL", t0 + 30 * 60))
        self.assertFalse(p.doubtful("AAPL", t0 + 61 * 60))  # no longer open

    def test_desk_nudge(self):
        from trading.desk import Desk
        pipe = types.SimpleNamespace(desk_lean=lambda s, now, need: 0.5, doubtful=lambda s, now, below: True)
        stub = types.SimpleNamespace(pipeline=pipe, now=0)
        self.assertAlmostEqual(Desk._pipeline_nudge(stub, "X", 0.4), (0.4 + 0.3 * 0.5) * 0.5)


class SnapshotTests(unittest.TestCase):
    def test_macro_shock_weighs_more_and_floor_clears(self):
        from trading import dual_brain
        agent = types.SimpleNamespace(workspace={}, view=View(direction=0.1, news=-0.6, news_age_mins=10,
                                                              is_macro_shock=True))
        snap = dual_brain.snapshot(agent)
        self.assertAlmostEqual(snap["news_score"], -0.75)
        self.assertIs(agent.workspace["dual_snapshot"], snap)
        room = ReadingRoom(["X"])
        room.update({"X": 1}, [{"symbol": "X", "text": "X: shares surge"}], 0)
        room.update({"X": 1}, [], 90 * 60 * 6)  # six half-lives: under the floor
        self.assertEqual(room.news["X"], 0.0)


if __name__ == "__main__":
    unittest.main()
