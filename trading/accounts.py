"""Every trader's own practice account: a small paper book only that agent trades, so its record is its own.

Votes alone can't show which agents are any good: a vote is never filled, never pays costs, never has a
drawdown. Each trader therefore trades its own $10,000 paper account with every call it makes (its own
position sizing: a full-strength call is all-in, a half-strength call half), pays COST_BPS on every change,
and keeps a real record: equity, closed trades, wins and losses, profit factor and worst drawdown.

An agent is **proven** once its record clears every bar in `proven()`: enough closed trades, money made after
costs, gross wins well above gross losses, and a drawdown it survived. Only proven agents get a say in
real-money proposals (trading/live_orders.py via the engine's mirror). The accounts are saved with the rest
of each agent's memory, so a record built on live prices survives restarts. Paper only.
"""

START = 10_000.0
COST_BPS = 3.0  # 1 bp fee + 2 bp slippage, as in the backtest
MIN_TRADES = 20
MIN_PROFIT_FACTOR = 1.2
MAX_DRAWDOWN = 0.15


class PracticeAccount:
    def __init__(self):
        self.cash = START
        self.shares = 0.0
        self.entry = 0.0  # average entry of the open position
        self.peak = START
        self.max_dd = 0.0
        self.wins = 0
        self.losses = 0
        self.gross_win = 0.0
        self.gross_loss = 0.0
        self.costs = 0.0
        self.last_price = 0.0

    def equity(self, price: float | None = None) -> float:
        return self.cash + self.shares * (price if price is not None else self.last_price)

    def mark(self, price: float) -> None:
        self.last_price = price
        e = self.equity(price)
        self.peak = max(self.peak, e)
        if self.peak > 0:
            self.max_dd = max(self.max_dd, 1 - e / self.peak)

    def rebalance(self, fraction: float, price: float) -> None:
        """Hold `fraction` (-1..1) of current equity in the symbol: positive long, negative short."""
        if not price > 0:
            return
        self.mark(price)
        target = max(-1.0, min(1.0, fraction)) * max(self.equity(price), 0.0) / price
        delta = target - self.shares
        if abs(delta * price) < 1.0:
            return
        if self.shares and (target == 0 or (target > 0) != (self.shares > 0) or abs(target) < abs(self.shares)):
            closed = self.shares if (target == 0 or (target > 0) != (self.shares > 0)) else self.shares - target
            pnl = closed * (price - self.entry)
            if pnl > 0:
                self.wins += 1
                self.gross_win += pnl
            elif pnl < 0:
                self.losses += 1
                self.gross_loss -= pnl
        if target and (not self.shares or (target > 0) != (self.shares > 0)):
            self.entry = price
        elif target and abs(target) > abs(self.shares):
            self.entry = (self.entry * self.shares + price * (target - self.shares)) / target
        cost = abs(delta * price) * COST_BPS / 10_000
        self.cash -= delta * price + cost
        self.costs += cost
        self.shares = target

    @property
    def trades(self) -> int:
        return self.wins + self.losses

    @property
    def profit_factor(self) -> float | None:
        if not self.gross_loss:
            return None if not self.gross_win else float("inf")
        return self.gross_win / self.gross_loss

    def snapshot(self) -> dict:
        pf = self.profit_factor
        return {"equity": round(self.equity(), 2), "pnl": round(self.equity() - START, 2), "trades": self.trades,
                "win_rate": round(self.wins / self.trades, 4) if self.trades else None,
                "profit_factor": None if pf is None else (99.0 if pf == float("inf") else round(pf, 2)),
                "max_drawdown": round(self.max_dd, 4), "costs": round(self.costs, 2), "proven": proven(self)}

    def state(self) -> dict:
        return {k: getattr(self, k) for k in ("cash", "shares", "entry", "peak", "max_dd", "wins", "losses",
                                               "gross_win", "gross_loss", "costs", "last_price")}

    def load(self, s: dict) -> None:
        for k, v in s.items():
            if hasattr(self, k):
                setattr(self, k, float(v) if k not in ("wins", "losses") else int(v))


def proven(acct: PracticeAccount) -> bool:
    pf = acct.profit_factor
    return (acct.trades >= MIN_TRADES and acct.equity() > START and pf is not None and pf >= MIN_PROFIT_FACTOR
            and acct.max_dd <= MAX_DRAWDOWN)
