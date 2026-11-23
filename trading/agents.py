"""The agent team.

- Traders propose a direction for one symbol (+1 buy, -1 sell, 0 flat).
- Analysts judge the regime of one symbol and scale how much the desk trusts
  trend-following versus mean-reversion traders.
- Risk monitors watch volatility and vote to shrink overall exposure.

Agents are staffed into the departments in `org.py`. Each agent keeps its own
workspace (a private notebook of what it has seen and decided) and its own
brain (`brain.py`), which learns from the agent's own results and decides how
hard it votes, or whether it votes at all.
"""

import random
import statistics
from collections import deque
from dataclasses import dataclass

from . import academy, accounts, entry_filters, exits, loss_audit, masters, mind, review, signal_gate
from .brain import AgentBrain
from .org import department_sizes

TREND = "trend"
TRADE_STEP = 0.25  # a position change at least this big counts as a practice trade
REVERSION = "reversion"


GATE_BUCKETS = (0.9, 0.8, 0.7, 0.5, 0.0)  # sureness floors, highest first


def gate_bucket(sure: float) -> str:
    """The sureness bucket a trade falls in ("0.8" = sure 0.8 up to the next floor)."""
    return next(f"{b:.1f}" for b in GATE_BUCKETS if sure >= b) if sure >= 0 else "0.0"


@dataclass(frozen=True)
class Vote:
    agent_id: str
    role: str  # "trader" | "analyst" | "risk"
    symbol: str | None
    value: float  # trader: -1..1 direction, analyst: -1 (ranging) .. 1 (trending), risk: 0..1 exposure


class Agent:
    role = "agent"
    kind = "agent"
    # The vote range and the "no opinion" value the brain shapes votes around.
    neutral, low, high = 0.0, -1.0, 1.0

    def __init__(self, agent_id: str, symbol: str, window: int):
        self.agent_id = agent_id
        self.symbol = symbol
        self.window = window
        self.prices: deque[float] = deque(maxlen=window + 1)
        # A longer look back for the higher-timeframe check (trading/entry_filters.py); traders only.
        self.history: deque[float] | None = None
        self.position = 0.0  # virtual position, used only to score the agent
        self.pnl = 0.0
        self.department = ""  # set by build_team
        self.brain = AgentBrain(agent_id)
        self.training: academy.Training | None = None  # set by academy.enroll
        self.macro_view: float | None = None  # analysts: what the news says, -1 bearish .. 1 bullish
        self.news_view: float | None = None  # traders: the desk's latest news read for the symbol (Druckenmiller)
        self.view = None  # traders: the reading room's view of the symbol (news + chart), set by the desk
        # The agent's own rules, learned from its own mistakes at every 15-minute review (trading/review.py).
        self.rules = review.starting_rules()  # build_team sets the department's own starting rules
        self.journal: list[dict] = []  # trades closed since the last review
        self.trial: dict | None = None  # the last review's rule change, checked at the next review
        # The agent's own space: nothing else writes here.
        self.workspace = {"votes_cast": 0, "sat_out": 0, "held": 0, "last_signal": None, "last_vote": None,
                          "trades_this_hour": 0, "trades_total": 0, "trade_wins": 0, "trade_losses": 0,
                          "reviews": 0, "lessons": []}
        self.must_practice = False  # set by the desk when the agent is behind its practice-trade quota
        self.account = accounts.PracticeAccount() if self.role == "trader" else None  # its own paper book
        self.clock = None  # sessions.MarketClock: the market's time of day, set by the desk (None in the simulator)
        # Its own two-step mind (trading/mind.py): step 1 thinks, step 2 decides.
        self.mind = mind.Mind(agent_id) if self.role == "trader" and mind.ENABLED else None

    def observe(self, price: float) -> Vote | None:
        if self.account is not None:
            self.account.mark(price)
        if self.prices:
            self.pnl += self.position * (price - self.prices[-1])
            # The brain scores the strategy's last raw signal, even while sitting
            # out, so an agent can earn its way back in.
            if self.workspace["last_signal"] is not None:
                self.brain.learn(self.reward(self.workspace["last_signal"], price))
        self.prices.append(price)
        if self.history is not None:
            self.history.append(price)
        if len(self.prices) <= self.window:
            return None
        signal = self.decide()
        if self.mind is not None:
            # Step 1, think: its own belief from what it sees (its strategy is one of the things it weighs).
            lean = self.mind.think(self, signal)
            self.workspace["sureness"] = abs(lean)
            # Step 2, decide: buy, sell or wait, and how big, by its own patience and appetite.
            signal = self.mind.decide(lean)
        value = self.brain.shape(signal, self.neutral, self.low, self.high)
        self.workspace["last_signal"] = signal
        self.workspace["last_vote"] = value
        if value is None:
            self.workspace["sat_out"] += 1
            if self.role == "trader":
                # Benched traders still keep up their practice trades (scored, never sent to the desk).
                self._trade(self._manage_exit(self._practice(signal)) if self.must_practice else 0.0)
            return None
        if self.role == "trader" and not self.must_practice and not academy.should_act(
                self.training, abs(signal), self.brain.weight):
            # Trained decision makers hold when the call's edge doesn't pay for the trade.
            value = 0.0
            self.workspace["held"] += 1
            self.workspace["last_vote"] = value
        if self.role == "trader" and not self.must_practice:
            value = masters.advise_trader(self, value)  # the masters' rules, as this agent learned them
            self.workspace["last_vote"] = value
        elif self.role == "risk":
            value = masters.advise_risk(self, value)
            self.workspace["last_vote"] = value
        if self.role == "trader" and not self.must_practice:
            value = self._confirm(value, signal)
            value = entry_filters.check(self, value, signal)  # regime, higher timeframe, volatility, meta-label
            value = signal_gate.check(self, value, signal)  # triage -> gate 0.8 -> deep read (SIGNAL_GATE=on)
            self.workspace["last_vote"] = value
        if self.role == "trader" and self.must_practice:
            value = self._practice(value)
            self.workspace["last_vote"] = value
        if self.role == "trader":
            value = self._manage_exit(value)
            self.workspace["last_vote"] = value
        self.workspace["votes_cast"] += 1
        if self.role == "trader":
            self._trade(value)
        return Vote(self.agent_id, self.role, self.symbol, value)

    def _agreement(self, direction: float) -> float:
        """How much the reading room's view (news + chart) agrees with a trade in `direction`, -1..1."""
        if self.view is None or not direction:
            return 0.0
        return self.view.direction * (1.0 if direction > 0 else -1.0)

    def _confirm(self, value: float, signal: float) -> float:
        """The confirmation filter: no new exposure unless news, chart and the agent's own signal agree.

        The bar is the agent's own: its 15-minute reviews raise it after losses it could have avoided.
        An open position that the view now clearly opposes is closed.
        """
        if self.view is None:
            return value  # no reading room (READING_ROOM=off): nothing to confirm with
        d = 1.0 if value > 0 else -1.0 if value < 0 else 0.0
        held = 1.0 if self.position > 0 else -1.0 if self.position < 0 else 0.0
        if held and self._agreement(held) <= -self.rules["exit_against"]:
            self._held()
            return 0.0  # the news and the chart have turned against the open trade: get out
        if held and d != held and self._agreement(held) >= max(review.HOLD_AGREE, self.rules["view_need"]):
            # Let a winner run: the signal wobbled, but the news and the chart still back the open trade.
            self.workspace["rode"] = self.workspace.get("rode", 0) + 1
            return self.position
        if not d or (d == held and abs(value) <= abs(self.position)):
            return value  # no new exposure: closing, trimming or holding needs no confirmation
        if abs(signal) < self.rules["min_edge"] or self._agreement(d) < self.rules["view_need"]:
            self._held()
            return self.position if d == held else 0.0
        return value

    def _manage_exit(self, value: float) -> float:
        """Exit discipline (trading/exits.py): stop at 1R, breakeven after +1R, target 2.4R, time exit."""
        exits.tick_cooldown(self)
        reason = exits.check(self)
        if reason:
            exits.record(self, reason)
            self.workspace["exit_reason"] = reason
            return 0.0
        if exits.ENABLED and not self.position and exits.blocked(self, value):
            return 0.0  # cooling down after a stop: no revenge trade on the same side
        return value

    def _held(self) -> None:
        self.workspace["held"] += 1
        self.workspace["confirm_blocked"] = self.workspace.get("confirm_blocked", 0) + 1

    def _practice(self, value: float) -> float:
        """Behind on its practice quota: make sure this tick is a trade (enter, flip or take profit)."""
        if abs(value - self.position) >= TRADE_STEP and (not value or self._agreement(value) >= 0):
            return value
        if self.position:
            return 0.0  # close the open practice position
        # Open a new one the way the reading room leans; the agent's own signal breaks a tie.
        lean = self.view.direction if self.view is not None and self.view.direction else value
        return 1.0 if lean >= 0 else -1.0

    def _trade(self, position: float) -> None:
        if abs(position - self.position) >= TRADE_STEP:
            self.workspace["trades_this_hour"] += 1
            self.workspace["trades_total"] += 1
        opened = position and (not self.position or (position > 0) != (self.position > 0))
        if self.position and (not position or opened):
            self._close_trade()
        if opened:
            self.workspace["entry"] = self.prices[-1]  # a new position: remember where it started (Seykota's R)
            exits.open_trade(self)
            signal = self.workspace.get("last_signal") or 0.0
            self.workspace["entry_note"] = {"edge": abs(signal), "agree": self._agreement(position),
                                            "forced": self.must_practice,
                                            "sure": self.workspace.get("sureness", abs(signal)),
                                            **entry_filters.features(self, position)}
        if self.account is not None and self.prices:
            self.account.rebalance(position, self.prices[-1])
        self.position = position

    def _close_trade(self) -> None:
        """Write the closed trade in the agent's journal, for its next review."""
        entry = self.workspace.get("entry")
        if not entry:
            return
        side = 1.0 if self.position > 0 else -1.0
        move = side * (self.prices[-1] / entry - 1)
        won = move > 0
        note = self.workspace.get("entry_note", {})
        # Practice-quota trades are scored on their own: they are forced coin flips, not the agent's choice, and
        # counting them hid the real win rate (on real hourly bars 98% of counted trades were practice ones).
        kind = "practice" if note.get("forced") else "trade"
        self.workspace[f"{kind}_wins" if won else f"{kind}_losses"] = (
            self.workspace.get(f"{kind}_wins" if won else f"{kind}_losses", 0) + (move != 0))
        how = self.workspace.pop("exit_reason", None) or "signal"  # stop, breakeven, target, time or signal
        self.journal.append({"won": won, "move": move, "exit": how, **note})
        if move:
            loss_audit.record(self, {"won": won, "move": move, "exit": how, **note})
            if not note.get("forced") and "er" in note:
                entry_filters.meta_for(self).learn(note, won)  # the meta-label model learns from every real trade
        if move and not note.get("forced"):
            # Sureness buckets, so the training report can set the confidence gate from real results.
            b = gate_bucket(note.get("sure", 0.0))
            row = self.workspace.setdefault("by_sure", {}).setdefault(b, [0, 0])
            row[0] += won
            row[1] += 1
        if self.mind is not None and move and not note.get("forced"):
            self.mind.trade_closed(won, note.get("sure", 0.0))  # step 2 learns from its own trades
        if len(self.journal) > review.JOURNAL_MAX:
            del self.journal[0]

    def hear(self, headline: dict) -> None:
        """Read a news headline with what the agent learned at the academy (analysts only)."""
        read = academy.read_headline(self.training, headline)
        if read is not None:
            self.macro_view = max(-1.0, min(1.0, 0.5 * (self.macro_view or 0.0) + read))

    def forget_news(self, keep: float = 0.95) -> None:
        if self.macro_view is not None:
            self.macro_view *= keep

    def reward(self, signal: float, price: float) -> float:
        """How right the last signal was, -1..1. Traders: did the price move their way?"""
        move = price - self.prices[-1]
        return signal if move > 0 else -signal if move < 0 else 0.0

    def decide(self) -> float:
        raise NotImplementedError

    def trend_trust(self) -> float:
        """How far to trust a trend signal at this time of day (trading/sessions.py)."""
        return self.clock.trend_trust(self.symbol) if self.clock is not None else 1.0

    def reversion_trust(self) -> float:
        """How far to trust a fade at this time of day (Asia calm, London breaks it: trading/sessions.py)."""
        return self.clock.reversion_trust(self.symbol) if self.clock is not None else 1.0


class Trader(Agent):
    role = "trader"
    style = TREND

    def __init__(self, agent_id: str, symbol: str, window: int):
        super().__init__(agent_id, symbol, window)
        self.history = deque(maxlen=entry_filters.HTF_MULT * window + 1)


class Momentum(Trader):
    kind = "momentum"

    def decide(self):
        change = self.prices[-1] / self.prices[0] - 1
        return max(-1.0, min(1.0, change * 50 * self.trend_trust()))


class MovingAverageCross(Trader):
    kind = "ma_cross"

    def decide(self):
        prices = list(self.prices)
        fast = statistics.fmean(prices[-max(2, self.window // 4):])
        slow = statistics.fmean(prices)
        return 1.0 if fast > slow else -1.0


class Breakout(Trader):
    kind = "breakout"

    def decide(self):
        prior = list(self.prices)[:-1]
        trust = min(1.0, self.trend_trust())  # breakouts in New York's opening hour are often fakes
        if self.prices[-1] > max(prior):
            return trust
        if self.prices[-1] < min(prior):
            return -trust
        return 0.0


class MeanReversion(Trader):
    kind = "mean_reversion"
    style = REVERSION

    def decide(self):
        prices = list(self.prices)
        stdev = statistics.pstdev(prices)
        if stdev == 0:
            return 0.0
        z = (prices[-1] - statistics.fmean(prices)) / stdev
        return max(-1.0, min(1.0, -z / 2 * self.reversion_trust()))


class RSI2(Trader):
    """Larry Connors' RSI(2) (added at mina's request): buy a sharp 2-bar pullback in an uptrend, sell a sharp 2-bar pop in a downtrend.

    - Trend: price above (long) or below (short) its long moving average (the agent's long look back, standing
      in for Connors' 200-day average).
    - Entry: RSI over the last 2 moves below `RSI2_LOW` (long) or above `RSI2_HIGH` (short); deeper means
      stronger.
    - Exit: RSI(2) back above `RSI2_EXIT` (long) / below 100 - `RSI2_EXIT` (short), or price back across its
      5-bar average. The desk's exit discipline (trading/exits.py) still applies on top.
    """

    kind = "rsi2"
    style = REVERSION

    def rsi2(self) -> float:
        p = list(self.prices)[-3:]
        gains = sum(max(0.0, b - a) for a, b in zip(p, p[1:]))
        losses = sum(max(0.0, a - b) for a, b in zip(p, p[1:]))
        if gains + losses == 0:
            return 50.0
        return 100.0 * gains / (gains + losses)

    def decide(self):
        prices = list(self.prices)
        if len(prices) < 6:
            return 0.0
        rsi = self.rsi2()
        trend = statistics.fmean(self.history) if self.history else statistics.fmean(prices)
        ma5 = statistics.fmean(prices[-5:])
        now = prices[-1]
        trust = self.reversion_trust()
        if self.position > 0:  # in a long: hold until the bounce is done
            return 0.0 if rsi > RSI2_EXIT or now > ma5 else self.position
        if self.position < 0:
            return 0.0 if rsi < 100 - RSI2_EXIT or now < ma5 else self.position
        if now > trend and rsi < RSI2_LOW:
            return min(1.0, (RSI2_LOW - rsi) / RSI2_LOW + 0.5) * trust
        if now < trend and rsi > RSI2_HIGH:
            return -min(1.0, (rsi - RSI2_HIGH) / (100 - RSI2_HIGH) + 0.5) * trust
        return 0.0


class RegimeAnalyst(Agent):
    """Efficiency ratio: net move / total path. Near 1 = trending, near 0 = choppy."""

    role = "analyst"
    kind = "regime_analyst"

    def reward(self, signal, price):
        # A "trending" call is right when the next move continues the last one.
        if len(self.prices) < 2:
            return 0.0
        continued = (price - self.prices[-1]) * (self.prices[-1] - self.prices[-2])
        return signal if continued > 0 else -signal if continued < 0 else 0.0

    def decide(self):
        prices = list(self.prices)
        path = sum(abs(b - a) for a, b in zip(prices, prices[1:]))
        if path == 0:
            return 0.0
        efficiency = abs(prices[-1] - prices[0]) / path
        return max(-1.0, min(1.0, efficiency * 4 - 1))


class VolatilityMonitor(Agent):
    """Votes for less exposure as realised volatility rises above its limit."""

    role = "risk"
    kind = "volatility_monitor"
    neutral, low, high = 1.0, 0.0, 1.0

    def reward(self, signal, price):
        # Cutting size is right before a big move; full size is right in calm markets.
        big = abs(price / self.prices[-1] - 1) > self.vol_limit
        return 1 - 2 * signal if big else 2 * signal - 1

    def __init__(self, agent_id, symbol, window, vol_limit):
        super().__init__(agent_id, symbol, window)
        self.vol_limit = vol_limit

    def decide(self):
        prices = list(self.prices)
        returns = [b / a - 1 for a, b in zip(prices, prices[1:])]
        vol = statistics.pstdev(returns)
        return max(0.0, min(1.0, self.vol_limit / vol)) if vol else 1.0


TREND_TYPES = (Momentum, MovingAverageCross, Breakout)
REVERSION_TYPES = (MeanReversion, RSI2)
TRADER_TYPES = TREND_TYPES + REVERSION_TYPES
RSI2_LOW, RSI2_HIGH, RSI2_EXIT = 10.0, 90.0, 70.0
# Share of the Reversion department that trades RSI(2); the rest trade the z-score fade. Off by default (env
# RSI2_SHARE): on unseen practice runs half RSI(2) cut Reversion's win rate from 60.2% to 59.1%, and on real
# BTC, ETH and SOL bars from 50.4% to 47.6% (15m) and 56.0% to 53.2% (5m). It did cut Reversion's loss on the
# 5m bars (-41.7k to -7.5k). Stricter entries (RSI below 5 or 2) didn't help.
RSI2_SHARE = 0.0


def build_team(count: int, symbols: list[str], seed: int = 0) -> list[Agent]:
    """Build `count` agents split evenly across the departments, spread across symbols, with varied parameters.

    The agents come out untrained; `academy.enroll` trains each one for its department's job.
    """
    rng = random.Random(seed)
    sizes = department_sizes(count)

    def staff(department: str, make) -> list[Agent]:
        members = []
        for i in range(sizes[department]):
            agent = make(i, symbols[i % len(symbols)])
            agent.department = department
            agent.rules = review.starting_rules(department)
            members.append(agent)
        return members

    def trend(i, symbol):
        cls = TREND_TYPES[i % len(TREND_TYPES)]
        return cls(f"trend-{i:04d}-{cls.kind}", symbol, rng.randint(5, 60))

    def reversion(i, symbol):
        window = rng.randint(5, 60)
        if RSI2_SHARE and (i * RSI2_SHARE) % 1 + RSI2_SHARE >= 1:  # every 1/RSI2_SHARE-th agent
            return RSI2(f"reversion-{i:04d}-rsi2", symbol, window)
        return MeanReversion(f"reversion-{i:04d}", symbol, window)

    return (
        staff("trend", trend)
        + staff("reversion", reversion)
        + staff("research", lambda i, s: RegimeAnalyst(f"research-{i:04d}", s, rng.randint(10, 80)))
        + staff("risk", lambda i, s: VolatilityMonitor(f"risk-{i:04d}", s, rng.randint(10, 40),
                                                       rng.uniform(0.008, 0.02)))
    )
