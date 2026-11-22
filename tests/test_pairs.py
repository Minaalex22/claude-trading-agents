import math
import random
import unittest

from trading import pairs as P


class PairsToolkitTest(unittest.TestCase):
    def test_ols_recovers_line(self):
        x = [i / 10 for i in range(100)]
        y = [2 + 1.5 * v for v in x]
        a, b = P.ols(x, y)
        self.assertAlmostEqual(a, 2, places=9)
        self.assertAlmostEqual(b, 1.5, places=9)

    def test_adf_separates_stationary_from_random_walk(self):
        rng = random.Random(3)
        s, w, st, rw = 0.0, 0.0, [], []
        for _ in range(800):
            s = 0.8 * s + rng.gauss(0, 1); w += rng.gauss(0, 1)
            st.append(s); rw.append(w)
        self.assertLess(P.adf_stat(st), P.EG_CRIT["1%"])
        self.assertGreater(P.adf_stat(rw), P.EG_CRIT["5%"])

    def test_ou_half_life(self):
        rng = random.Random(4)
        phi, s, xs = math.exp(-math.log(2) / 10), 0.0, []
        for _ in range(5000):
            s = phi * s + rng.gauss(0, 1); xs.append(s)
        self.assertAlmostEqual(P.ou_halflife(xs)["half_life"], 10, delta=1.5)

    def test_kalman_tracks_a_drifting_beta(self):
        rng = random.Random(5)
        lx, ly, tb, b, x = [], [], [], 1.0, 0.0
        for t in range(600):
            x += rng.gauss(0, 1); b += 0.002 if t > 300 else 0
            lx.append(x); ly.append(b * x + rng.gauss(0, 0.1)); tb.append(b)
        k = P.kalman_beta(lx, ly, q=1e-5, r=0.01)
        self.assertLess(abs(k["beta"][-1] - tb[-1]), 0.05)

    def test_z_rule(self):
        self.assertEqual(P.pair_position(2.5, 0)[0], -1)
        self.assertEqual(P.pair_position(-2.5, 0)[0], 1)
        self.assertEqual(P.pair_position(1.0, 0)[0], 0)
        self.assertEqual(P.pair_position(0.3, -1), (0, "take profit"))
        self.assertEqual(P.pair_position(-4.2, 1), (0, "stop"))
        self.assertEqual(P.pair_position(-3.0, 1)[0], 1)
        self.assertEqual(P.pair_position(4.5, 0)[0], 0)  # past the stop: never enter

    def test_study_on_the_simulated_pair(self):
        s = P.study(P.simulated_pair())
        self.assertTrue(s["eg"]["cointegrated"])
        self.assertAlmostEqual(s["eg"]["beta"], 1.5, delta=0.15)
        self.assertAlmostEqual(s["ou"]["half_life"], 10, delta=4)
        self.assertLess(s["kalman"]["rmse"]["kalman"], s["kalman"]["rmse"]["ols"])  # Kalman follows the drift better
        self.assertEqual(s["split"], 750)
        self.assertTrue(s["verdict"])
        for k in ("ols", "kalman"):
            self.assertIn("sharpe", s["backtest"][k])

    def test_costs_are_charged(self):
        lx = [0.0] * 50
        ly = [0.0] * 50
        z = [None] + [3.0, 0.0] * 24 + [0.0]  # enter and exit again and again on a flat spread
        bt = P.backtest(lx, ly, [1.0] * 50, z)
        self.assertLess(bt["total"], 0)
        self.assertGreater(bt["trades"], 10)


if __name__ == "__main__":
    unittest.main()
