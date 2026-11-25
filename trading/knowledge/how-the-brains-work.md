# How the brains are programmed

Code: `Minaalex22/claude-trading-agents`, main branch as of 24 Nov 2026 (commit `2b21db5`).
All of this is **paper trading only**. Nothing here can send a real-money order.

---

## The big picture in one sentence

About **1,400 small agents** each have their **own brain** and cast a vote. The **desk** (the main brain) adds the votes up, the **managers** (Atlas, Sage, Sentinel) bench the bad agents, and the **risk rules** have the final say before any order.

## How one trade decision flows

```
 Price comes in (every tick)
        |
        v
 +-------------------------------------------------------------+
 |  EACH AGENT (x 1,400)            trading/agents.py           |
 |                                                              |
 |  1. Strategy   "price went up 2% -> buy"   (decide)          |
 |  2. Mind       THINK: "62% chance it rises" (mind.py)        |
 |                DECIDE: buy / sell / wait, how big            |
 |  3. Brain      trust in itself: press harder, soften,        |
 |                or SIT OUT                   (brain.py)       |
 |  4. Filters    academy -> masters -> news+chart agree?       |
 |                -> trend check -> (gate, off) -> exit rules   |
 |                                                              |
 |  Result: one VOTE, from -1 (sell) to +1 (buy)                |
 +-------------------------------------------------------------+
        |
        v
 +-------------------------------------------------------------+
 |  MAIN BRAIN = THE DESK            trading/desk.py            |
 |                                                              |
 |  Research says "trending" -> trust Trend traders more        |
 |  Research says "choppy"   -> trust Reversion traders more    |
 |  + news tilt (30%)  x  Risk dept's "shrink size" vote        |
 |  Brain Alpha nudges Trend vs Reversion by who made money     |
 |                                                              |
 |  Score under 0.5 -> do nothing                               |
 +-------------------------------------------------------------+
        |
        v
 Desk checks (hooks.py): news against it? losing streak? chasing?
        |
        v
 Hard risk rules (risk.py): size caps, 2% daily loss, 10% drawdown, KILL switch
        |
        v
 Paper order filled  ->  every agent learns from the result
        |
        v
 Every hour: managers bench losers (managers.py)
 Every 15 min: each agent reviews its own losing trades (review.py)
```

---

## Part 1: the individual brains

Every agent has **three layers**. Each one is its own copy, so no two agents think alike.

### Layer 1: the strategy (its trading style)
File: `trading/agents.py` (line 275 onwards)

Each agent is one kind. The two simplest:

```python
class Momentum(Trader):          # Trend department
    def decide(self):
        change = self.prices[-1] / self.prices[0] - 1
        return max(-1.0, min(1.0, change * 50 * self.trend_trust()))

class MeanReversion(Trader):     # Reversion department
    def decide(self):
        z = (prices[-1] - mean(prices)) / stdev(prices)
        return max(-1.0, min(1.0, -z / 2 * self.reversion_trust()))
```

In plain words:
- **Momentum**: "price went up, so buy." Bigger move, stronger buy.
- **Mean reversion**: "price is stretched far above normal, so sell (it should come back)."
- Others: **moving-average cross**, **breakout**, **regime analyst** (is the market trending or choppy?) and **volatility monitor** (is it too wild? then shrink).

The 1,400 are split into 4 departments of 350 (`build_team`, line 417): Trend, Reversion, Research, Risk. The 6 departments on the Agents page (Entry, Scalper, News, Exit, Risk, Hedge) are these same agents regrouped (`trading/conductor.py` line 560).

### Layer 2: the mind (think, then decide)
File: `trading/mind.py`

```python
def think(self, agent, strategy):
    x = self.see(agent, strategy)        # strategy, news, chart, trend, last move...
    z = self.bias + sum(self.weights[f] * v for f, v in x.items())
    lean = math.tanh(z)                  # -1 sure it falls .. +1 sure it rises
    self.thought = f"{p_up:.0%} chance it goes up ({why})"

def decide(self, lean):
    if abs(lean) < self.patience:
        return 0.0                       # "wait: only 8% sure, I need 12%"
    return lean * self.appetite * 2      # buy/sell, sized by appetite
```

In plain words:
- **Think**: it looks at up to 7 things (its strategy, news, chart, trend...) and gives each a weight. After every tick it checks if it guessed right and **adjusts the weights**: what helped gets more say, what misled gets less.
- **Decide**: if it isn't sure enough (its **patience**), it waits. Otherwise it trades, sized by its **appetite**.
- After a losing trade it becomes more patient and smaller. After a win, a bit bolder.

### Layer 3: the brain (self-trust)
File: `trading/brain.py`

```python
def learn(self, reward):                 # reward: -1 (wrong) .. +1 (right)
    self.weight = self.weight * math.exp(self.learning_rate * reward)

def shape(self, signal, ...):
    if self.weight < self.sit_out_below:
        return None                      # lost confidence: sit out
    return signal * self.weight          # trusted: press harder
```

In plain words: one number, **trust in itself** (starts at 1.0).
- Right calls raise it, wrong calls lower it.
- High trust = votes louder. Low trust = votes softer. Too low = **sits out** until it earns trust back on paper.
- Young agents learn fast; veterans change slowly.

### The filters every trader's vote passes
File: `trading/agents.py`, `observe()` line 79

In order:
1. **Academy** (`academy.py`): trained agents hold when the edge is too small to pay for the trade.
2. **Masters** (`masters.py`, `methods.py`): rules from Livermore, Seykota, Turtles, Gann, etc. Example: Seykota "cut a loser, let a winner run."
3. **Confirm** (line 143): no new trade unless news, chart and its own signal agree. If news and chart turn against an open trade, get out.
4. **Entry filters** (`entry_filters.py`): Trend traders only trade when the market is really trending and the bigger timeframe agrees.
5. **Signal gate** (`signal_gate.py`): triage, gate 0.8, deep read. **Currently OFF** (it helped the simulator a little but hurt on real prices).
6. **Exits** (`exits.py`): stop at 1R, breakeven after +1R, take profit at 2.4R, no revenge trade after a stop.

### How agents learn over time
- **Every 15 min** (`review.py`): each trader reads its own losing trades and asks "did I fight the news? trade weak signals? hold too long?", then tightens its own rules. Each change is on trial and **undone if the win rate gets worse**.
- **Every day** (`share_best`): the worst 20% copy the rules of the best 20% in their department.

---

## Part 2: the main brain

### The desk (adds up the votes)
File: `trading/desk.py`, `_score()` line 359

```python
regime = average(analyst votes)          # +1 trending .. -1 choppy
trend_weight = (1 + regime) / 2
score = trend_weight * trend_votes + (1 - trend_weight) * reversion_votes
score = score + 0.3 * news_view          # analysts' read of the news
score = score * exposure                 # Risk dept says "shrink" in wild markets
if abs(score) < 0.5: score = 0           # not convinced enough: no trade
```

In plain words: Research decides **whose advice to trust**. Trending market, so listen to Trend traders. Choppy market, so listen to Reversion traders. News tilts it, Risk shrinks it, and if the final score isn't strong (0.5 or more) **nothing happens**.

**Brain Alpha** (`trading/brains.py`) watches which style is making money and slowly gives it more weight.

### The managers (Atlas, Sage, Sentinel)
File: `trading/managers.py`, `hour()` line 35

Every hour:
- **Atlas** (Head of Trading): benches traders who won under **35%** of 6+ trades. Benched = still practises, but its vote doesn't count for an hour.
- **Sage** (Head of Research): benches analysts who were right under **45%** of the time.
- **Sentinel** (Head of Risk): flags a symbol where the desk kept losing; new trades there go in at **half size**.
- No manager can bench more than 20% of a department.

### Desk checks and hard risk rules
Files: `trading/hooks.py`, `trading/risk.py`

```python
max_position_value = 20_000   # per symbol
max_order_value    = 5_000
max_drawdown       = 0.10     # 10% below peak -> only exits allowed
max_daily_loss     = 0.02     # 2% down today  -> no new risk until tomorrow
# a file named KILL closes everything
```

Before an order, the desk blocks it if news and chart point the other way, after a losing streak, at bad times of day, or when chasing a move. Then the risk rules **always have the final say**. Agents can only propose.

### JARVIS (the voice you talk to)
File: `trading/jarvis.py`, `ClaudeBrain` line 304

JARVIS **does not trade**. It reads a live snapshot of the desk plus a handbook of the whole system and answers your questions. With `ANTHROPIC_API_KEY` set it uses Claude; without it, a simple built-in brain answers from the handbook.

---

## Where to look in the code

| Part | File |
|---|---|
| Agents, strategies, filters | `trading/agents.py` |
| Each agent's self-trust | `trading/brain.py` |
| Each agent's think/decide | `trading/mind.py` |
| Main brain (votes to order) | `trading/desk.py` |
| Brain Alpha (style weights) | `trading/brains.py` |
| Managers | `trading/managers.py`, `trading/org.py` |
| Masters' rules | `trading/masters.py`, `trading/methods.py` |
| Training | `trading/academy.py`, `trading/review.py` |
| Safety rules | `trading/hooks.py`, `trading/risk.py`, `trading/exits.py` |
| JARVIS | `trading/jarvis.py` |

Browse it online: https://github.com/Minaalex22/claude-trading-agents/tree/main/trading
