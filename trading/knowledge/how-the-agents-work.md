# The agents: who they are and how they work

Code: `Minaalex22/claude-trading-agents`, main branch as of 24 Nov 2026 (commit `fd2d88c`, PR #28).
The two-speed brain is **PR #29 and isn't merged yet**. It's shown at the end as "coming".
Paper trading only.

---

## 1. The breakdown: 1,400 agents

In the code there are **4 departments of 350**. The Agents page shows them as **6 departments** by splitting Research and Risk in half (`trading/org.py`, `trading/conductor.py`).

| Department (page) | How many | Made from | Manager | What they do |
|---|---|---|---|---|
| **Entry** | 350 | Trend traders | Atlas | Find trends and breakouts, propose buys and sells |
| **Scalper** | 350 | Reversion traders | Atlas | Bet a stretched price snaps back |
| **News** | 175 | Research analysts (even numbers) | Sage | Read headlines, say bullish or bearish |
| **Exit** | 175 | Research analysts (odd numbers) | Sage | Say "trending" or "choppy", so the desk knows who to trust |
| **Risk** | 175 | Volatility monitors (even numbers) | Sentinel | Vote smaller size when markets are wild |
| **Hedge** | 175 | Volatility monitors (odd numbers) | Sentinel | Vote to shrink exposure when markets get wild |

**The strategy styles inside them** (`trading/agents.py`):
- **Entry (Trend), about 117 of each:** **Momentum** ("it went up, so buy"), **Moving-average cross** ("the short average is above the long one, so buy") and **Breakout** ("new high, so buy; new low, so sell").
- **Scalper (Reversion), 350:** **Mean reversion / z-score** ("price is 2 swings above normal, so sell"). Larry Connors' RSI(2) is built but switched off, because tests showed a lower win rate.
- **News + Exit (Research), 350:** **Regime analyst**, which works out a straight-line move ÷ total zig-zag. Near 1 means a clean trend, near 0 means chop. The analysts were also schooled on politics and economy headlines.
- **Risk + Hedge, 350:** **Volatility monitor**: "the swings are bigger than my limit, so vote for smaller size."

**No two agents are the same.** Each one gets its own look-back (5 to 60 bars), its own market, its own learning speed, its own patience and its own risk appetite, all drawn from its ID number.

### The clusters (the graph)


A **cluster** is a group of agents with the same strategy and the same look-back speed: **fast** (5-20 bars), **medium** (21-40) or **slow** (41+). The counts below come from running the real `build_team` code with 1,400 agents.

| Department | Cluster | Agents |
|---|---|---|
| Entry (350) | Momentum fast / medium / slow | 35 / 36 / 46 |
| | MA cross fast / medium / slow | 32 / 47 / 38 |
| | Breakout fast / medium / slow | 33 / 29 / 54 |
| Scalper (350) | Z-score fade fast / medium / slow | 102 / 121 / 127 |
| News (175) | Regime analyst fast / medium / slow | 27 / 49 / 99 |
| Exit (175) | Regime analyst fast / medium / slow | 31 / 47 / 97 |
| Risk (175) | Volatility monitor fast / medium | 62 / 113 |
| Hedge (175) | Volatility monitor fast / medium | 45 / 130 |

All 22 clusters feed one desk. Each department is also spread evenly over the 10 markets (35 agents per market). Fast clusters react quickly but get fooled by noise. Slow clusters are steadier but late. The mix is on purpose, so the desk always hears both.

**The 3 managers** (`trading/managers.py`):
- **Atlas** (Head of Trading) runs Entry and Scalper.
- **Sage** (Head of Research) runs News and Exit.
- **Sentinel** (Head of Risk) runs Risk and Hedge.

---

## 2. How one agent works, step by step

This happens **every tick** (each new price) for every agent, in `observe()` in `trading/agents.py`.

```
 new price
    |
 1. LEARN    "was my last call right?"  -> self-trust goes up or down
    |
 2. SIGNAL   run my strategy             -> e.g. +0.6 (fairly strong buy)
    |
 3. THINK    my mind weighs 7 things     -> "62% chance it goes up"
    |        (my strategy, news, chart, news+chart, trend, last move, stretch)
 4. DECIDE   sure enough? (my patience)  -> buy 40% of my account, or wait
    |
 5. TRUST    self-trust too low?         -> sit out (still learning on paper)
    |        high trust                  -> vote louder
 6. CHECKS   academy: worth the cost?
    |        masters: Livermore, Seykota, Turtles, Gann... adjust or veto
    |        confirm: news + chart must agree, else no new trade
    |        entry filters: really trending? does the bigger trend agree?
    |        exits: stop 1R, breakeven, trailing stop, time limit, no revenge trade
    |
 7. VOTE     one number, -1 (sell) to +1 (buy)  -> sent to the desk
```

In plain words:
1. **Learn:** it checks whether its last call was right. Right calls raise its self-trust and wrong calls lower it.
2. **Signal:** its strategy gives a raw buy or sell number.
3. **Think:** its mind blends what it sees. It has learned how much weight to give each input, for example "the news has been misleading me, so trust it less."
4. **Decide:** if it isn't sure enough, it waits. If it is, its appetite sets the size.
5. **Trust:** if its self-trust has dropped too low, it **sits out** until it earns trust back.
6. **Checks:** the vote must pass the academy, the masters' rules, the news + chart check, the trend checks and the exit rules.
7. **Vote:** what's left goes to the desk.

**Practice trades are walled off.** Every trader must make 20 practice trades an hour. These are forced trades that win about 47%. Since PR #28 they **never reach the desk and never count** toward an agent's real record. Before this change they made up 98% of counted trades and hid the real win rate.

**Analysts and Risk agents are simpler.** They do steps 1, 2, 5 and 7. Analysts vote "trending vs choppy" and add their news read. Risk agents vote a size from 0 (none) to 1 (full).

---

## 3. From votes to a trade (the desk)

File: `trading/desk.py`

```
 1,400 votes (benched agents ignored, practice trades ignored)
        |
 Group by market: Trend votes, Reversion votes, analyst votes, risk votes, news
        |
 Analysts' regime vote below 0.25?  -> ignore Trend, Reversion decides
        |
 Regime router (desk's own check on each market):
   breakout -> Trend x1.25, Reversion x0.4
   chop     -> Trend muted, Reversion x1.2
   between  -> Trend x0.5
        |
 score = Trend part + Reversion part + 30% news, then x risk vote
        |
 Score under 0.5?                       -> no trade
 Expected move won't pay the fees?      -> no new trade
        |
 Desk checks: news against it? losing streak? chasing? bad time of day?
        |
 Hard risk rules: $5k per order, $20k per market, 2% daily loss, 10% drawdown, KILL switch
        |
 PAPER ORDER
```

In plain words: the desk listens to **Trend traders in trending markets** and **Reversion traders in choppy markets**. News tilts the score, Risk shrinks it, and if the final score isn't strong enough or the move won't cover the fees, **nothing happens**. The risk rules always have the last word.

---

## 4. How agents learn and get benched

| When | Who | What happens |
|---|---|---|
| Every tick | Each agent | Self-trust and mind weights adjust to whether it was right |
| After each closed trade | Each trader | Wins make it bolder. A loss taken while barely sure makes it more patient and smaller |
| Every 15 min | Each trader | Reads its own losers and asks "did I fight the news? trade weak signals? hold too long?", then tightens its own rules. Each change is **on trial** and undone if results get worse (`review.py`) |
| Every hour | **Atlas** | Benches traders who won under **35%** of 6+ trades. If a whole department won under **45%**, he can bench up to **60%** of it. Otherwise the cap is 20% |
| Every hour | **Sage** | Benches analysts who were right under **45%** of the time (after 300+ calls) |
| Every hour | **Sentinel** | Flags a market where the desk kept losing. New trades there go in at **half size** |
| Every hour | Academy | Refresher lessons and a re-exam for every agent |
| Every day | Everyone | The worst 20% in each department copy the rules of the best 20% |

**Benched** means the agent keeps practising and learning, but **its vote doesn't count for an hour**.

---

## 5. Coming next: the two-speed brain (PR #29, not merged)

File: `trading/dual_brain.py` (on the PR branch)

Every trader gets two halves:
- **Fast half** turns the signal into a real setup: which side, what kind of market, how far it expects price to move, and where it's wrong.
- **Slow half** checks the setup before any **new** trade (exits are never blocked):
  1. **Anti-tilt:** after 3 losses in a row, it waits 5 bars.
  2. **Fees:** the expected move must cover 2× the trading costs.
  3. **Ledger:** it remembers how each setup did in each market type (trend, chop, wild). After 12 trades, a setup that loses on average is **banned in that market type**.
  4. **Premortem:** no trade against a strong news + chart read during a volatility spike.

It's on by default once merged (switch: `DUAL_BRAIN`).

---

## Where to look

| Part | File |
|---|---|
| Agents, strategies, step-by-step `observe()` | `trading/agents.py` |
| Self-trust | `trading/brain.py` |
| Think / decide | `trading/mind.py` |
| Departments and managers | `trading/org.py`, `trading/managers.py`, `trading/conductor.py` (6-department view) |
| Masters' rules | `trading/masters.py`, `trading/methods.py` |
| Training and reviews | `trading/academy.py`, `trading/review.py` |
| Trend checks, exits | `trading/entry_filters.py`, `trading/exits.py` |
| Desk (votes to order) | `trading/desk.py` |
| Risk rules | `trading/hooks.py`, `trading/risk.py` |

See also: [how-the-brains-work.md](how-the-brains-work.md), [how-news-is-read.md](how-news-is-read.md), [how-market-data-is-read.md](how-market-data-is-read.md)
