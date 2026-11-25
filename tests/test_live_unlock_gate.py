"""mina's payoff-aware unlock bar (25 Nov 2026): PF >= 1.35 and (WR >= 60% or WR >= 42% with payoff >= 1.85)."""

import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

from trading import strategy_lab
from trading.live_unlock_gate import evaluate_live_unlock_eligibility

ROOT = Path(__file__).resolve().parents[1]


class PayoffBarTests(unittest.TestCase):
    def test_implied_payoff(self):
        self.assertAlmostEqual(strategy_lab.payoff_ratio(0.446, 1.72), 2.137, places=2)
        self.assertIsNone(strategy_lab.payoff_ratio(None, 1.5))

    def test_win_bar(self):
        self.assertTrue(strategy_lab.win_bar_ok(0.61, 0.5))  # scalper route
        self.assertTrue(strategy_lab.win_bar_ok(0.446, 2.14))  # trend route
        self.assertFalse(strategy_lab.win_bar_ok(0.446, 1.80))  # payoff too small
        self.assertFalse(strategy_lab.win_bar_ok(0.41, 3.0))  # win rate under 42%

    def test_trend_profile_only_needs_signoff(self):
        with mock.patch.dict(os.environ, {"PAPER_ONLY_LOCK": "true"}):
            ok, b = evaluate_live_unlock_eligibility(120, 44.6, 1.72, 46.0, 5.8)
        self.assertFalse(ok)
        self.assertEqual([x.split(" ")[0] for x in b], ["paper_only_lock_active", "awaiting_explicit_GO_LIVE_signoff"])

    def test_safety_barriers_stay(self):
        ok, b = evaluate_live_unlock_eligibility(30, 44.6, 1.72, 70.0, 9.0, mina_explicit_signoff=True)
        joined = " ".join(b)
        for key in ("sample_size", "sim_vs_real_gap", "max_drawdown"):
            self.assertIn(key, joined)
        self.assertFalse(ok)
        ok, b = evaluate_live_unlock_eligibility(60, 44.6, 1.30, 44.6, 2.0, mina_explicit_signoff=True)
        self.assertIn("profit_factor", " ".join(b))
        ok, b = evaluate_live_unlock_eligibility(60, 50.0, 1.40, 50.0, 2.0, mina_explicit_signoff=True)
        self.assertIn("expectancy_profile", " ".join(b))  # payoff 1.40 < 1.85
        self.assertEqual(evaluate_live_unlock_eligibility(60, 44.6, 1.72, 55.0, 2.0, mina_explicit_signoff=True),
                         (True, []))  # 10.4-point gap is inside the new 15-point drift

    def test_strategy_lab_arm_passes_on_trend_profile(self):
        # 60 trades: 27 winners of +$300, 33 losers of -$100 -> 45% wins, PF 2.45, payoff 3.0
        trades = [{"symbol": "GOLD", "pnl": 300.0 if i % 20 < 9 else -100.0, "qty": 0, "entry": 0} for i in range(60)]
        st = strategy_lab.check(trades)["gold"]
        self.assertTrue(st["gates"]["proof"], st["missing"])
        self.assertAlmostEqual(st["payoff"], 3.0, places=2)
        self.assertFalse(st["promoted"])  # still needs sim vs real and the sign-off

    def test_check_script_runs(self):
        out = subprocess.run([sys.executable, str(ROOT / "scripts" / "check_live_gate.py"), "/nonexistent"],
                             capture_output=True, text=True, timeout=60, cwd=ROOT)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn('"ready_for_signoff": true', out.stdout)
        self.assertIn('"to_go": 50', out.stdout)


if __name__ == "__main__":
    unittest.main()


# mina's three cases (25 Nov 2026), as written
def test_trend_payoff_profile_passes_quantitative_hurdles(monkeypatch):
    monkeypatch.setenv("PAPER_ONLY_LOCK", "false")
    # 44.6% WR at 1.72 PF => implied payoff = 1.72 * (0.554 / 0.446) = 2.136x (>= 1.85x)
    unlocked, blockers = evaluate_live_unlock_eligibility(
        chosen_trades=65, real_win_rate_pct=44.6, profit_factor=1.72, sim_win_rate_pct=46.0,
        max_drawdown_pct=5.8, mina_explicit_signoff=True)
    assert unlocked is True
    assert blockers == []


def test_low_win_rate_with_poor_payoff_stays_blocked(monkeypatch):
    monkeypatch.setenv("PAPER_ONLY_LOCK", "false")
    # 44.6% WR at 1.10 PF => implied payoff = 1.37x (< 1.85x) -> must fail
    unlocked, blockers = evaluate_live_unlock_eligibility(
        chosen_trades=65, real_win_rate_pct=44.6, profit_factor=1.10, sim_win_rate_pct=45.0,
        max_drawdown_pct=5.0, mina_explicit_signoff=True)
    assert unlocked is False
    assert any("expectancy_profile" in b for b in blockers)


def test_sample_size_under_50_stays_locked(monkeypatch):
    monkeypatch.setenv("PAPER_ONLY_LOCK", "false")
    unlocked, blockers = evaluate_live_unlock_eligibility(
        chosen_trades=49, real_win_rate_pct=44.6, profit_factor=1.72, sim_win_rate_pct=45.0,
        max_drawdown_pct=4.5, mina_explicit_signoff=True)
    assert unlocked is False
    assert any("sample_size (49/50" in b for b in blockers)
