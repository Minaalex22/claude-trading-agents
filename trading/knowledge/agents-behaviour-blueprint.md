# How the agents behave: blueprint

*Written 25 Nov 2026 from `Minaalex22/claude-trading-agents` main (PR #49) plus open PR #50 (trend mode). Paper trading. Real money stays locked.*


## 1. Who is on the floor

There are **1,400 agents**. In the code that's 4 departments of 350. The Agents page splits Research and Risk in half to make **6 departments**.

| Department | Agents | Made from | Manager | Job | Strategy styles |
|---|---|---|---|---|---|
| **Entry** | 350 | Trend traders | Atlas | Find trends and breakouts, propose entries | Momentum, moving-average cross, breakout |
| **Scalper** | 350 | Reversion traders | Atlas | Fade stretched moves for small, fast targets | Z-score mean reversion (RSI(2) built, off) |
| **News** | 175 | Research analysts (even numbers) | Sage | Read headlines and the calendar, call a pause before big news | Regime analyst + news read |
| **Exit** | 175 | Research analysts (odd numbers) | Sage | Say "trending" or "choppy", so trades get out in time | Regime analyst (efficiency ratio) |
| **Risk** | 175 | Volatility monitors (even numbers) | Sentinel | Size every trade, veto anything over the limits | Volatility monitor |
| **Hedge** | 175 | Volatility monitors (odd numbers) | Sentinel | Shrink or offset exposure when markets get wild | Volatility monitor |

**No two agents are alike.** Each agent's ID number sets its look-back (5 to 60 bars), its market, learning speed, patience (0.03 to 0.6) and appetite (0.3 to 1.0). Agents with the same strategy and speed form a **cluster**: fast (5-20 bars), medium (21-40) or slow (41+). There are 22 clusters in total.

## 2. What one trader does every bar

This all happens in `observe()` in `agents.py`, for each of the 700 traders.

1. **Learn.** It checks whether its last call was right. **Self-trust** (one number, starts at 1.0, kept between 0.05 and 2.0) is multiplied by e^(learning rate × reward). Its **mind weights** move as well. With *learn on close* switched on, both stay frozen while a trade is open, and the closed trade teaches them once, by its result in R.
2. **Signal.** Its strategy gives a raw number from −1 to +1.
3. **Think.** The mind weighs **7 inputs**: its own strategy, news + chart together, the chart, the news, the last move, the trend, and how stretched the price is. It turns them into a sentence like "62% chance it goes up (my strategy, the trend)". Inputs that misled it get less say next time.
4. **Decide.** If the lean is below its **patience**, it waits. Otherwise **appetite** sets the size (a 0.5 lean at full appetite is a full position).
5. **Trust.** If self-trust has fallen below its floor, it **sits out**. It keeps learning on paper until it earns trust back. High trust makes its vote louder.
6. **Slow brain** (`dual_brain.py`, on). The fast half has already turned the signal into a setup: side, setup tag, expected move, and where it is wrong. The slow half checks that setup before any *new* trade. Exits are never blocked.
    - **Anti-tilt:** after 3 losses in a row, it waits 5 bars.
    - **Fees:** the expected move must cover 2× the round-trip costs.
    - **Setup ledger:** it remembers how each setup did in each market type (trend, chop, volatility spike), starting from a 48% prior. If the last 12 trades of a setup lose on average, that setup goes on **shadow probation**. It sits out, but its skipped trades are still followed on paper. It comes back when that paper record turns positive, or after 24 hours.
    - **Premortem:** no trade against a strong news + chart read (0.6 or more) during a volatility spike.

7. **Checks.**
    - Academy: trained agents hold when the edge won't pay for the trade.
    - Masters' rules: Livermore, Seykota, Turtles, Gann and the others, who can adjust or veto.
    - News by style: Entry traders need news + chart to agree. Scalpers are only stopped by fresh or shock news against them.
    - Trend filters: Entry traders need a real trend that the bigger timeframe agrees with.

8. **Exits** (`exits.py`):
    - Stop and target come from Sentinel's blackboard (see section 4).
    - Bank half at +1.2R and lift the stop on the rest to +0.15R.
    - Trend trades: first stop at 1.2R, breakeven from +1.5R, then trail 1R behind the best price.
    - **Stall stop:** close the trade if it hasn't reached +0.4R after 8 bars (12 bars on 15-minute charts).
    - Time limit of 3 windows.
    - No revenge trade: wait 3 bars (trend) or 2 bars (reversion) before reopening the same side after a stop.

9. **Vote.** What is left becomes one number from −1 (sell) to +1 (buy), and it goes to Atlas's floor.

**Analysts and risk agents are simpler.** They only do Learn, Signal, Trust and Vote. Analysts vote trending vs choppy and add their news read. Risk agents vote a size from 0 to 1.

## 3. Sitting out: every way an agent stays quiet

| Why | Who decides | How long |
|---|---|---|
| Not sure enough (lean below patience) | The agent's mind | That bar |
| Self-trust too low | The agent's brain | Until trust is earned back on paper |
| On tilt (3 losses in a row) | Slow brain | 5 bars |
| Setup on probation in this market type | Slow brain | Until its paper record is positive, or 24 h |
| Vote under 0.25 conviction | Team relay | That bar |
| Wrong department for the regime | Atlas (relay) | While the regime lasts |
| Fast cluster with no medium/slow backing | Atlas (relay) | That bar |
| Benched for a bad hour | Atlas / Sage | 1 hour |
| Whisper vote (under 0.35) | Desk | That bar |

A benched or sitting-out agent **keeps practising and learning**. Its vote just doesn't reach the desk.

## 4. How the managers work together: the team relay

Each bar, for each market, the departments pass **one shared blackboard** down the floor instead of voting blind (`team_relay.py`, on).

1. **Sage's floor (News + Exit).** It measures how efficiently price is moving and labels the market:
    - **BREAKOUT** at an efficiency of 0.45 or more
    - **CHOP** at 0.25 or less
    - **TRANSITION** in between

    It adds the news lean and the whale bias.

2. **Sentinel's floor (Risk + Hedge).** It reads Sage's label and sets the size multiplier (×0.65 if the spread is wide) and the exit geometry:

    | Regime | Stop | Target |
    |---|---|---|
    | BREAKOUT | 1.15R | 2.2R |
    | TRANSITION | 1.25R | 1.5R |
    | CHOP | 1.35R | 0.95R (bank the snap-back) |

3. **Atlas's floor (Entry + Scalper).** The traders vote *after* reading the blackboard, and only the department that suits the regime counts:
    - **Entry** in BREAKOUT
    - **Scalper** in CHOP
    - **both** in TRANSITION

    Fast clusters never fire alone. They need medium or slow clusters to agree, and if fast and slow disagree, that's a veto.

4. **The desk.**
    - It drops whisper votes and groups the rest by setup. Each group counts √(its size), so 200 clones of one idea can't outvote independent strategies.
    - A score from a single setup is capped at 0.72.
    - The score must clear the **0.72 bar** ("A+ calls only").
    - Then come the whale boost, the guards, the 9-stage order check, pullback entry and bucket sizing, and finally the paper order.

Every order keeps its blackboard, so /audit and JARVIS can show what each floor said.

## 5. Practice quota (training without polluting the record)

- Every trader makes **at least 20 practice trades an hour**. These are forced trades that win about 47%. They keep the agents active and learning in quiet markets.
- Practice trades **never reach the desk** and never count toward self-trust, the setup ledger, the tilt counter, win rates, brain training files or real decisions.
- Before this wall went in (PR #28), practice trades were 98% of counted trades and hid the real win rate.

## 6. The learning clock

| When | Who | What happens |
|---|---|---|
| Every bar / every closed trade | Each agent | Self-trust and mind weights move. The ledger records the setup. A win makes it bolder; a loss taken while barely sure makes it more patient and smaller |
| Every 15 min | Each trader | Reads its own losers ("did I fight the news? trade a weak signal? hold too long?") and tightens one rule. Each change is **on trial** and is undone if the next win rate is 5 points worse (`review.py`) |
| Every hour | **Atlas** | Benches traders who won under **35%** of 6+ trades (at most 20% of a department). If a whole department won under **45%**, he can bench up to **60%** of it |
| Every hour | **Sage** | Benches analysts who were right under **45%** of the time, after 300+ calls |
| Every hour | **Sentinel** | Flags a market where the desk lost most trades and lost money overall. New risk there goes in at **half size** for the next hour |
| Every hour | Academy | Refresher lessons and a re-exam. Exam scores set starting trust (0.75 to 1.25) |
| Every day | Everyone | The worst 20% in each department copy the rules and ledger of the best 20% |
| On demand | `brain_distill.py` | Exports checked trades per department as training files (export only) |

## 7. Trend mode (PR #50): what changes

**The problem it fixes.** On real daily prices 2020-26, the desk:
- shorted against the big trend about half the time, and those shorts lost most of the money
- closed trades after a day or two, because the agents' conviction dipped under 0.72 (average winner +1.2%, average loser −2.0%)
- sat out whole years after 2023, because conviction rarely reached the bar

**The new behaviour** (`trend_anchor.py`, daily bars only):

| Rule | What the agents and desk now do |
|---|---|
| **Direction** | Price above its 200-day average = up, below = down. Re-read every bar |
| **Veto** | Any desk call **against** that direction is dropped. No counter-trend trades |
| **Base hold** | **With** the trend, the desk holds at least **75%** of normal size even when agents go quiet, until price crosses the average |
| **Agents add on top** | When agents agree strongly, their conviction above 75% sizes the position up to full |
| **Band** | The held size only rebalances when it is more than 25% off target, so fees don't churn |
| **Intraday desks** | Idle until they have 200 days of history |
| **Switch** | `TREND_ANCHOR=off` goes back to the old behaviour |

**Result** (real prices, costs included):

| | Profit factor | End equity |
|---|---|---|
| Before PR #50, all years | 0.32 | −$5.8k |
| With PR #50, all years | **1.65** | **+$13.2k** |
| With PR #50, unseen 2023-26 | **1.22** | |

Forex majors still lose. In plain words: the agents still pick the timing and the extra size, but they can no longer fight the big trend, and they no longer drop a winning trend just because they went quiet.

## 8. Where it lives

| Behaviour | File |
|---|---|
| Agents, strategies, `observe()` | `trading/agents.py` |
| Self-trust | `trading/brain.py` |
| Think / decide | `trading/mind.py` |
| Fast + slow brain | `trading/dual_brain.py` |
| Departments, managers, 6-department view | `trading/org.py`, `trading/managers.py`, `trading/conductor.py` |
| Team relay | `trading/team_relay.py` |
| Exits | `trading/exits.py` |
| Reviews and copying the best | `trading/review.py` |
| Training and exams | `trading/academy.py`, `trading/masters.py`, `trading/methods.py` |
| Desk scoring, consensus, 0.72 bar | `trading/desk.py` |
| Trend mode | `trading/trend_anchor.py` (PR #50) |

See also: [whole-system-blueprint](../whole-system/whole-system-blueprint.md), and brain-code/ in project files.
