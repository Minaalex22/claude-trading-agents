"""The owner's money rules, written down once and checked from real numbers. PRACTICE ONLY.

From the "Gey" Mac system's PROFIT_AND_LOSS_RULES.md (2026-10-25), taking only what fits our desk:
never let one trade hurt, let winners pay for losers, cut costs, keep honest numbers, and pass a graduation test
before any real money. Real money also stays locked in code (platforms.LIVE_TRADING_ENABLED) until the owner says yes.
"""

from __future__ import annotations

RULES = (
    ("risk per trade", "lower of 1% of balance and $10 until the owner raises it"),
    ("stop and target first", "every order has a stop and a target before it is placed"),
    ("target 2.4x the stop", "with 2.4R winners even a 35-40% win rate grows the account"),
    ("2 open max", "never two positions in the same symbol"),
    ("2 losses in a row", "pause 5 minutes, review, adjust, then go again"),
    ("2% daily loss", "stop for the day; never try to win it back"),
    ("spread <= 0.2%", "skip thin books; limit orders for entries"),
    ("no hype", "screenshots of overnight riches never change a rule or a size"),
    ("30 trades before judging", "no conclusions, no new angle gets a vote before 30 scored cases"),
)

GRAD_MIN_TRADES = 30
GRAD_MAX_DRAWDOWN = 0.05


def graduation(pnls: list[float], capital: float = 100_000.0, rule_breaks: int = 0, owner_yes: bool = False) -> dict:
    """The test every item must pass before real money. Built from closed practice trades, oldest first."""
    n = len(pnls)
    wins = [p for p in pnls if p > 0]
    losses = [-p for p in pnls if p < 0]
    expectancy = sum(pnls) / n if n else None
    win_rate = len(wins) / n if n else None
    avg_win = sum(wins) / len(wins) if wins else 0.0
    avg_loss = sum(losses) / len(losses) if losses else 0.0
    edge = (win_rate is not None and win_rate * avg_win > (1 - win_rate) * avg_loss)
    equity, peak, dd = capital, capital, 0.0
    for p in pnls:
        equity += p
        peak = max(peak, equity)
        dd = max(dd, (peak - equity) / peak if peak else 0.0)
    checks = [
        ("30+ closed practice trades", n >= GRAD_MIN_TRADES, f"{n} so far"),
        ("positive expectancy", bool(expectancy and expectancy > 0),
         "no trades yet" if expectancy is None else f"{expectancy:+.2f} per trade"),
        ("wins pay for losses", edge, "no trades yet" if win_rate is None
         else f"win {win_rate:.0%} x {avg_win:.2f} vs loss {1 - win_rate:.0%} x {avg_loss:.2f}"),
        ("max drawdown under 5%", n > 0 and dd < GRAD_MAX_DRAWDOWN, f"{dd:.1%}"),
        ("zero rule breaks", rule_breaks == 0, f"{rule_breaks} breaks"),
        ("owner says yes in own words", owner_yes, "waiting for you" if not owner_yes else "yes"),
    ]
    passed = all(ok for _, ok, _ in checks)
    return {"passed": passed, "checks": [{"check": c, "ok": ok, "detail": d} for c, ok, d in checks],
            "then": "start at $100 per order, 3 real orders a day max", "real_money": "locked"}
