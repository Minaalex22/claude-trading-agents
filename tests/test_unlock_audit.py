"""tools/audit_2023_2026_unlock.py: the replay split at 2023 and checked against the live-unlock bar."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
import audit_2023_2026_unlock as audit  # noqa: E402

T22, T24 = 1_640_000_000, 1_710_000_000  # Dec 2021, Mar 2024


class AuditTests(unittest.TestCase):
    def test_split_and_blockers(self):
        trades = [{"t": T22, "arm": "crypto", "pnl": 100.0, "s": "BTC"}, {"t": T22, "arm": "crypto", "pnl": -50.0, "s": "BTC"}]
        trades += [{"t": T24 + i, "arm": "gold", "pnl": 30.0 if i % 3 else -20.0, "s": "GOLD"} for i in range(60)]
        r = audit.audit({"trades": trades, "desk_cash_per_arm": 100_000.0})
        self.assertEqual(r["in_sample_2020_2022"]["trades"], 2)
        self.assertEqual(r["unseen_2023_2026"]["trades"], 60)
        self.assertEqual(r["unseen_2023_2026"]["profit_factor"], 3.0)
        self.assertTrue(r["replay_passes_unlock_bar"])  # 60 trades, 66.7% win, PF 3, tiny drawdown
        self.assertEqual(r["arms"]["fx"]["unseen_blockers"], ["benched (0% sleeve)"])

    def test_too_few_trades_blocks(self):
        r = audit.audit({"trades": [{"t": T24, "arm": "us", "pnl": 10.0, "s": "SPX"}]})
        self.assertIn("trades 1 < 50", r["unseen_blockers"])


if __name__ == "__main__":
    unittest.main()
