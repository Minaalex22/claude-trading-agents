import random
import unittest

from trading import conductor as C


class ConductorTest(unittest.TestCase):
    def setUp(self):
        self.c = C.Conductor(seed=5, warmup=80)

    def test_runs_and_keeps_books_straight(self):
        for _ in range(1500):
            self.c._advance()
        s = self.c.snapshot()
        self.assertEqual(len(s["charts"]), 6)
        self.assertLessEqual(len([t for t in self.c.trades.values() if t["kind"] == "main"]), C.MAX_OPEN)
        # Equity = balance + open profit, and realised = every closed trade's profit.
        self.assertAlmostEqual(s["account"]["equity"], s["account"]["balance"] + s["account"]["unrealized"], places=1)
        closed_sum = sum(r["pnl"] for r in self.c.closed)
        open_fees = sum(t["fees"] for t in self.c.trades.values())
        if len(self.c.closed) < self.c.closed.maxlen:
            self.assertAlmostEqual(s["account"]["realized"], closed_sum - open_fees, delta=1.0)
        self.assertGreater(s["stats"]["trades"], 0)
        self.assertGreater(s["stats"]["fees"], 0)  # costs are charged
        for r in self.c.closed:
            self.assertEqual(r["states"][-4:], ["CLOSING", "CLOSED", "LOGGED", "REVIEWED"])
            self.assertIn("OPEN", r["states"])

    def test_risk_sizes_by_stop_distance_and_vetoes(self):
        c = self.c
        c.trades.clear(); c.paused_until = -1; c.killed_day = -1
        c.day_start_equity = c.equity()
        p = {"asset": "GOLD", "side": 1, "entry": 2000.0, "sl": 1990.0, "agent": "entry", "strategy": "trend"}
        verdict, info = c.risk_check(p)
        self.assertIn(verdict, ("APPROVED", "RESIZED"))
        self.assertLessEqual(info["risk_usd"], c.equity() * C.RISK_PCT + 0.01)
        if verdict == "APPROVED":
            self.assertAlmostEqual(info["qty"], c.equity() * C.RISK_PCT / 10.0, places=6)
        c.paused_until = c.bar + 5; c.pause_reason = "US CPI"
        self.assertEqual(c.risk_check(p)[0], "REJECTED")
        c.paused_until = -1
        c.balance -= c.day_start_equity * (C.DAILY_LOSS_PCT + 0.001)
        self.assertEqual(c.risk_check(p), ("REJECTED", {"reason": "daily loss limit"}))

    def test_max_open_trades(self):
        c = self.c
        c.trades.clear(); c.paused_until = -1; c.killed_day = -1; c.day_start_equity = c.equity()
        for i, sym in enumerate(("DAX", "GOLD", "EURUSD")):
            c.trades[100 + i] = {"id": 100 + i, "asset": sym, "side": 1, "kind": "main", "upnl": 0.0, "fees": 0.0}
        v, info = c.risk_check({"asset": "BTC", "side": 1, "entry": 90000.0, "sl": 89000.0})
        self.assertEqual(v, "REJECTED")
        self.assertIn("max", info["reason"])

    def test_correlation_helper(self):
        rng = random.Random(1)
        a = [rng.gauss(0, 1) for _ in range(60)]
        self.assertAlmostEqual(C._corr(a, a), 1.0, places=6)
        self.assertAlmostEqual(C._corr(a, [-x for x in a]), -1.0, places=6)

    def test_step_is_throttled(self):
        bar = self.c.bar
        self.c.step(now=1000.0); self.c.step(now=1000.5)
        self.assertEqual(self.c.bar, bar + 1)


if __name__ == "__main__":
    unittest.main()
