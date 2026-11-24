"""Diverse consensus, pullback entries, track-record sizing and dead-zone hours (mina's third batch)."""

import io
import json
import types
import unittest
from collections import deque
from datetime import datetime, timezone

from trading import desk as desk_mod
from trading import engine, hooks


class ConsensusTests(unittest.TestCase):
    def test_whispers_are_dropped(self):
        self.assertEqual(desk_mod.Desk._cluster([("a", 0.2), ("b", -0.3)]), 0.0)

    def test_one_setup_is_cut_and_clones_do_not_outvote(self):
        clones = [("momentum:trend", 0.8)] * 200
        self.assertAlmostEqual(desk_mod.Desk._cluster(clones), 0.8 * desk_mod.SINGLE_SETUP)
        # 200 clones buying vs one independent seller: sqrt weighting, not a 200:1 vote
        mixed = clones + [("breakout:chop", -0.8)]
        value = desk_mod.Desk._cluster(mixed)
        self.assertAlmostEqual(value, (0.8 * 200 ** 0.5 - 0.8) / (200 ** 0.5 + 1))
        self.assertLess(value, 0.8)


class BucketSizingTests(unittest.TestCase):
    def mult(self, moves):
        stub = types.SimpleNamespace(bucket={"X": deque(moves, maxlen=40)})
        return desk_mod.Desk._bucket_mult(stub, "X")

    def test_tiers(self):
        self.assertEqual(self.mult([0.01] * 5), 0.75)  # too few trades
        self.assertEqual(self.mult([0.01, -0.01] * 10), 0.25)  # no edge
        self.assertEqual(self.mult([0.02] * 7 + [-0.01] * 3), 1.4)  # 70% wins, big expectancy
        self.assertEqual(self.mult([0.012] * 11 + [-0.01] * 9), 1.0)  # small edge

    def test_off_is_one(self):
        self.addCleanup(setattr, desk_mod, "BUCKET_SIZING", desk_mod.BUCKET_SIZING)
        desk_mod.BUCKET_SIZING = False
        self.assertEqual(self.mult([0.01, -0.01] * 10), 1.0)


class DeadZoneTests(unittest.TestCase):
    def check(self, symbol, when):
        ts = datetime(*when, tzinfo=timezone.utc).timestamp()
        clock = types.SimpleNamespace(ts=ts)
        return hooks.dead_zone(hooks.OrderCheck(symbol, 1.0, 0.0, 100.0, clock=clock))[0]

    def test_rollover_and_close(self):
        self.assertEqual(self.check("EURUSD", (2025, 3, 4, 21, 30)), 0.0)
        self.assertEqual(self.check("EURUSD", (2025, 3, 4, 14, 0)), 1.0)
        self.assertEqual(self.check("SPY", (2025, 3, 4, 20, 55)), 0.0)  # 15:55 New York (EST)
        self.assertEqual(self.check("SPY", (2025, 3, 4, 18, 0)), 1.0)
        self.assertEqual(self.check("BTC", (2025, 3, 4, 21, 30)), 1.0)  # crypto never closes


class PullbackTests(unittest.TestCase):
    def test_staged_order_expires_without_chasing(self):
        stub = types.SimpleNamespace(pending={"X": {"qty": 1.0, "limit": 99.0, "left": 2, "why": "w"}},
                                     pending_cool={}, ticks=10, risk=types.SimpleNamespace(killed=False))
        work = lambda price: desk_mod.Desk._work_pending(stub, "X", {"X": price}, 0.5)
        self.assertEqual(work(101.0), 0)
        self.assertIn("X", stub.pending)
        self.assertEqual(work(102.0), 0)
        self.assertNotIn("X", stub.pending)  # ran away: dropped, not chased
        self.assertEqual(stub.pending_cool["X"], 10 + desk_mod.PULLBACK_BARS)

    def test_engine_fills_at_the_limit(self):
        env = {"AGENT_COUNT": "200", "TICKS": "300", "TICK_SECONDS": "0", "SEED": "3", "MEMORY": "off",
               "COACH": "off", "LEARNING_INTERVAL_SECONDS": "0"}
        out = io.StringIO()
        engine.run(env, stream=out)
        events = [json.loads(line) for line in out.getvalue().splitlines()]
        staged = [e for e in events if e["type"] == "staged"]
        fills = [e for e in events if e["type"] == "order" and e["why"].endswith("(pullback entry)")]
        self.assertTrue(staged)
        for f in fills:  # a buy fills at or below where it was staged, never above
            self.assertTrue(any(s["symbol"] == f["symbol"] and (f["price"] - s["limit"]) * f["qty"] <= 1e-6
                                for s in staged))


if __name__ == "__main__":
    unittest.main()
