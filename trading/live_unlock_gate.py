"""mina's trend-payoff aware unlock check (25 Nov 2026), as one plain function.

The live gate itself stays trading/strategy_lab.py (per Orbit arm, on the desk's own real-price trades); this is the
same bar for a single set of numbers, used by scripts/check_live_gate.py and the unlock audit. Thresholds come from
strategy_lab so the two can't drift apart:

  - >= 50 real-price chosen trades (practice-quota trades excluded)
  - profit factor >= 1.35
  - win rate >= 60%, OR win rate >= 42% with an implied payoff ratio (PF x (1 - WR) / WR) >= 1.85
  - max drawdown <= 8%, sim-vs-real win-rate gap <= 15 points, and mina's explicit sign-off

It only reports. It never unlocks anything: real money also needs the Demo/Live switch, live keys and GO LIVE.
"""
from __future__ import annotations

import os

from . import strategy_lab


def evaluate_live_unlock_eligibility(
    chosen_trades: int,
    real_win_rate_pct: float,
    profit_factor: float,
    sim_win_rate_pct: float,
    max_drawdown_pct: float,
    mina_explicit_signoff: bool = False,
    min_trades: int = strategy_lab.MIN_TRADES,
    min_profit_factor: float = strategy_lab.MIN_PROFIT_FACTOR,
    max_allowed_dd_pct: float = 100 * strategy_lab.MAX_DRAWDOWN,
) -> tuple[bool, list[str]]:
    """(ready, blockers). Every safety barrier stays: sample, drawdown, sim-vs-real and explicit sign-off."""
    blockers: list[str] = []

    if os.getenv("PAPER_ONLY_LOCK", "true").strip().lower() == "true" and not mina_explicit_signoff:
        blockers.append("paper_only_lock_active (set PAPER_ONLY_LOCK=false & type GO LIVE after hurdles pass)")

    if chosen_trades < min_trades:
        blockers.append(f"sample_size ({chosen_trades}/{min_trades} chosen real-price trades)")

    if profit_factor < min_profit_factor:
        blockers.append(f"profit_factor ({profit_factor:.2f} < {min_profit_factor:.2f})")

    implied_payoff_ratio = strategy_lab.payoff_ratio(real_win_rate_pct / 100.0, profit_factor)
    if not strategy_lab.win_bar_ok(real_win_rate_pct / 100.0, implied_payoff_ratio):
        blockers.append(
            f"expectancy_profile (WR {real_win_rate_pct:.1f}% / Payoff {implied_payoff_ratio:.2f}x; "
            f"need >={100 * strategy_lab.MIN_WIN_RATE:.0f}% WR OR >={100 * strategy_lab.ALT_MIN_WIN_RATE:.0f}% WR "
            f"with >={strategy_lab.MIN_PAYOFF}x payoff)"
        )

    gap = abs(sim_win_rate_pct - real_win_rate_pct)
    if gap > 100 * strategy_lab.MAX_DRIFT:
        blockers.append(f"sim_vs_real_gap ({gap:.1f}% > {100 * strategy_lab.MAX_DRIFT:.1f}% max drift)")

    if max_drawdown_pct > max_allowed_dd_pct:
        blockers.append(f"max_drawdown ({max_drawdown_pct:.2f}% > {max_allowed_dd_pct:.2f}%)")

    if not mina_explicit_signoff:
        blockers.append("awaiting_explicit_GO_LIVE_signoff")

    return (len(blockers) == 0), blockers
