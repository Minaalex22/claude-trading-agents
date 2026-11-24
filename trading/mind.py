"""Every trader's own mind, in two steps: first it THINKS, then it DECIDES.

**Step 1: think** (`Mind.think`). The agent looks at what it can see and forms its own belief: how likely the
price is to go up next, and why. What it can see:

- `strategy`: its own trading style's signal (momentum, moving-average cross, breakout or mean reversion)
- `room`: the reading room's view (news + chart together)
- `chart`: the chart read on its own
- `news`: what the research department says the news means
- `last_move`: the last price move, against its normal size
- `trend`: how far price has run over the agent's own look-back window
- `stretch`: how far price sits above or below its average

Every agent pays attention to its own mix of these (its "attention", drawn from its id, so no two agents
look at the world the same way) and gives each its own weight. After every tick it checks whether the price
went the way it believed and adjusts its weights (online logistic regression): what helped it guess right
gets more weight, what misled it gets less. Its belief is its own: it learns only from its own guesses.

**Step 2: decide** (`Mind.decide`). The agent turns its belief into an action for its own practice account:
buy, sell, or wait, and how big. Two traits of its own decide it:

- `patience`: how sure it must be before it acts at all. A losing trade it took while only a little sure
  makes it more patient; a winning one makes it a bit bolder.
- `appetite`: how much of its account it puts on a full-conviction call. Wins raise it, losses cut it.

The decision then still passes the agent's own learned rules, the masters' advice and the reading-room
confirmation (trading/agents.py), the hard risk limits and the desk checks. Every agent keeps its last
thought and its last decision in words, so the 3D page and JARVIS can show how it thinks and decides.

Paper only: a mind can trade its own practice account and vote, never place a real order.
"""

import math
import random
import statistics

from . import indicators

ENABLED = True  # MINDS=off (engine) turns the two-step minds off, for comparison

FEATURES = ("strategy", "room", "chart", "news", "last_move", "trend", "stretch")
NAMES = {"strategy": "my strategy", "room": "news+chart", "chart": "the chart", "news": "the news",
         "last_move": "the last move", "trend": "the trend", "stretch": "the stretch"}
MAX_WEIGHT = 3.0
SIZE_SCALE = 2.0  # a lean of 0.5 at full appetite is a full position (1.3 and 3.0 were tried: less profit)
PATIENCE = (0.03, 0.6)
APPETITE = (0.3, 1.0)


def _clip(x: float, lim: float = 1.0) -> float:
    return max(-lim, min(lim, x))


class Mind:
    def __init__(self, agent_id: str):
        rng = random.Random(f"mind:{agent_id}")
        # Attention: its strategy always, plus its own pick of the rest.
        self.attention = ["strategy"] + [f for f in FEATURES[1:] if rng.random() < 0.6]
        self.weights = {f: (1.0 if f == "strategy" else rng.gauss(0.0, 0.15)) for f in self.attention}
        self.bias = 0.0
        self.learning_rate = rng.uniform(0.002, 0.02)
        self.patience = rng.uniform(0.05, 0.2)
        self.appetite = rng.uniform(0.6, 1.0)
        self.guesses = 0
        self.right = 0
        self._last: tuple[dict, float, float] | None = None  # (features, belief, price) of the last thought
        self.thought = ""
        self.decision = ""

    # ---- step 1: think ------------------------------------------------------------------------------
    def see(self, agent, strategy: float) -> dict:
        prices = list(agent.prices)
        st = indicators.series(agent.symbol, prices)  # shared by every agent on this symbol (trading/indicators.py)
        rets = st["rets"] or [0.0]
        vol = st["ret_sd"] or 1e-9
        mean = st["mean"]
        sd = st["sd"] or 1e-9
        view = agent.view
        seen = {"strategy": _clip(strategy),
                "room": view.direction if view is not None else 0.0,
                "chart": view.chart if view is not None else 0.0,
                "news": agent.news_view or 0.0,
                "last_move": _clip(rets[-1] / vol / 3),
                "trend": _clip((prices[-1] / prices[0] - 1) / (vol * math.sqrt(len(rets))) / 3),
                "stretch": _clip((prices[-1] - mean) / sd / 2)}
        return {f: seen[f] for f in self.attention}

    def think(self, agent, strategy: float) -> float:
        """Form a belief from what it sees. Returns its lean, -1 (sure it falls) .. 1 (sure it rises)."""
        price = agent.prices[-1]
        self.learn(price)
        x = self.see(agent, strategy)
        z = self.bias + sum(self.weights[f] * v for f, v in x.items())
        lean = math.tanh(z)
        self._last = (x, lean, price)
        p_up = (1 + lean) / 2
        top = sorted(((self.weights[f] * v, f) for f, v in x.items() if v), key=lambda t: -abs(t[0]))[:2]
        why = ", ".join(f"{NAMES[f]} {'up' if c > 0 else 'down'}" for c, f in top) or "nothing clear"
        self.thought = f"{p_up:.0%} chance it goes up ({why})"
        return lean

    frozen = False  # set by the agent while a chosen trade is open (agents.LEARN_ON_CLOSE)

    def learn(self, price: float) -> None:
        """Check the last belief against what the price did, and adjust what it pays attention to."""
        if self._last is None or self.frozen:
            return
        x, lean, before = self._last
        if price == before:
            return
        up = 1.0 if price > before else 0.0
        self.guesses += 1
        self.right += (lean > 0) == (up > 0)
        step = self.learning_rate * (up - (1 + lean) / 2)
        for f, v in x.items():
            self.weights[f] = _clip(self.weights[f] + step * v, MAX_WEIGHT)
        self.bias = _clip(self.bias + step * 0.1, 0.5)

    def learn_trade(self, x: dict | None, side: float, reward: float) -> None:
        """A closed chosen trade, as one lesson: what it saw at entry, nudged toward the side that paid."""
        if not x:
            return
        up = 1.0 if side * reward > 0 else 0.0 if side * reward < 0 else 0.5
        z = self.bias + sum(self.weights.get(f, 0.0) * v for f, v in x.items())
        step = self.learning_rate * abs(reward) * (up - (1 + math.tanh(z)) / 2)
        for f, v in x.items():
            if f in self.weights:
                self.weights[f] = _clip(self.weights[f] + step * v, MAX_WEIGHT)

    # ---- step 2: decide -----------------------------------------------------------------------------
    def decide(self, lean: float) -> float:
        """Turn the belief into a target position for its own account, -1..1 (0 = wait)."""
        if abs(lean) < self.patience:
            self.decision = f"wait: only {abs(lean):.0%} sure, I need {self.patience:.0%}"
            return 0.0
        size = _clip(lean * self.appetite * SIZE_SCALE)
        self.decision = f"{'buy' if size > 0 else 'sell'} {abs(size):.0%} of my account"
        return size

    def trade_closed(self, won: bool, sureness: float) -> None:
        """Step 2 learns from its own closed trades: how patient to be and how much to risk."""
        lo, hi = PATIENCE
        if won:
            self.patience = max(lo, self.patience - 0.003)
            self.appetite = min(APPETITE[1], self.appetite + 0.01)
        else:
            if sureness < self.patience + 0.1:
                self.patience = min(hi, self.patience + 0.01)  # it acted when barely sure, and lost
            self.appetite = max(APPETITE[0], self.appetite - 0.02)

    # ---- reporting and memory -----------------------------------------------------------------------
    @property
    def accuracy(self) -> float | None:
        return self.right / self.guesses if self.guesses else None

    def snapshot(self) -> dict:
        top = sorted(self.weights.items(), key=lambda kv: -abs(kv[1]))[:3]
        return {"thinks": self.thought, "decides": self.decision,
                "trusts": {NAMES[f]: round(w, 2) for f, w in top},
                "patience": round(self.patience, 3), "appetite": round(self.appetite, 3),
                "guessed_right": None if self.accuracy is None else round(self.accuracy, 3)}

    def state(self) -> dict:
        return {"attention": self.attention, "weights": self.weights, "bias": self.bias,
                "patience": self.patience, "appetite": self.appetite, "guesses": self.guesses, "right": self.right}

    def load(self, s: dict) -> None:
        att = [f for f in s.get("attention", []) if f in FEATURES]
        if not att:
            return
        self.attention = att
        self.weights = {f: _clip(float(s["weights"].get(f, 0.0)), MAX_WEIGHT) for f in att}
        self.bias = float(s.get("bias", 0.0))
        self.patience = max(PATIENCE[0], min(PATIENCE[1], float(s.get("patience", self.patience))))
        self.appetite = max(APPETITE[0], min(APPETITE[1], float(s.get("appetite", self.appetite))))
        self.guesses = int(s.get("guesses", 0))
        self.right = int(s.get("right", 0))
