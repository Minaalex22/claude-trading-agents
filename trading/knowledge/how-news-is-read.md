# How news is read and fed to the agents

Code: `Minaalex22/claude-trading-agents`, main branch as of 24 Nov 2026 (commit `2b21db5`).
Paper trading only. No news code can place an order.

---

## The short version

News goes down **two roads**:

1. **The reading room** is the road that **really steers the agents' trades**. Every headline gets a score from -1 to +1, gets mixed with the chart, and every trader checks it before opening a trade.
2. **The News Pipeline** (cheap triage, then gate 0.8, then deep read, then the bill) runs on the **same headlines**, but right now it only shows on the **Pipeline page** and adds one vote on the **/decision page**. **It does not change agent trades yet.**

There is also a third, smaller path: the **Research analysts** read simulated headlines with what they learned at the academy, and that tilts the desk's final score by 30%.

## The flow

```
  WHERE NEWS COMES FROM
  - Live mode: 17 free RSS feeds (CNBC, MarketWatch, Yahoo, Seeking Alpha,
    Nasdaq, CoinDesk, Cointelegraph...) + Yahoo/Google News per symbol
  - Practice mode: a simulated news wire (right about 70% of the time)
                 |
                 v
  BREAK IT DOWN  (news_feed.py)
  - Match the headline to ONE market (Apple -> AAPL, "crude" -> WTI)
  - Drop it if it's older than 6 hours
  - Same story from 10 sites counts once
  - 1st source = half strength, 2nd different source = the other half
                 |
       +---------+-------------------------------+
       |                                         |
       v                                         v
  ROAD 1: READING ROOM (steers trades)   ROAD 2: NEWS PIPELINE (display + /decision)
  reading_room.py                        news_pipeline.py
  - Claude reads the headline            1. Seen it before? skip (free)
    (or a word list if no key)           2. TRIAGE: cheap read -> lean + sureness
  - news score fades 3% per tick         3. CHECKS: no market? no evidence? -> unsure
  - chart read on 20 and 60 bars         4. GATE 0.8: sure -> keep
  - VIEW = 60% chart + 40% news             unsure or big news -> DEEP READ
       |                                 5. DEEP READ: headline + news mood + chart
       v                                 6. Still unsure -> human queue
  EVERY TRADER checks the view:          7. BILL: cost of cheap vs deep vs mix
  - no new trade against it              8. Audits move the gate (0.7 to 0.9)
  - get out if it turns against them           |
  - its mind weighs news + chart               v
       |                                 /decision page: one "pipeline" vote
       v                                 (advice only, no orders)
  Votes -> DESK -> risk rules -> paper order

  ROAD 3: Research analysts (academy-trained) read headlines
          -> news_view -> desk score + 0.3 x news
```

---

## Step 1: collecting and breaking down news
File: `trading/news_feed.py`

```python
def match_symbol(title, symbols):
    # "Nvidia beats estimates" -> NVDA, "crude oil falls" -> WTI
    # If it mentions two markets, it's skipped (not clear who it's about)

if sources is None:                 # first site to report it
    weight, confirmed = 0.5, False  # half strength
elif source not in sources:         # a second, different site
    weight, confirmed = 0.5, True   # the other half
else:
    continue                        # 3rd copy: ignored
```

In plain words:
- It checks the feeds **every 5 minutes**. No keys or accounts needed.
- Each headline is matched to **one market**. If it's unclear, it's dropped.
- A **rumour from one site only gets half the push**. It reaches full strength once a second site confirms it.
- In practice mode, `trading/news.py` makes fake headlines (like "Inflation above forecast") that point the right way 70% of the time.

## Step 2 (Road 1): the reading room, which really steers trades
File: `trading/reading_room.py`

```python
def read_headline(text):            # built-in reader (used when Claude is off)
    "tariffs": -0.8, "trade deal": +0.7, "ceasefire": +0.6, "crash": -0.8 ...
    "inflation above forecast" -> -0.7   (hot inflation is bad for stocks)
    "jobs above forecast"      -> +0.6

self.news[s] *= 0.97                         # old news fades every tick
self.news[s] += push * weight                # new headline adds its push
v.direction = 0.6 * chart + 0.4 * news       # one VIEW per market
confident = abs(direction) >= 0.25           # strong enough to block trades
```

In plain words:
- With `ANTHROPIC_API_KEY` set, **Claude reads each headline** and says bullish or bearish and why. Without the key, a **word list** does it.
- The chart is read on two timeframes (20 and 60 bars). It only counts as "up" when **both agree**.
- The final **view = 60% chart + 40% news**, from -1 (down) to +1 (up).

**How agents use the view** (`trading/agents.py`, `_confirm`):
- **No new trade** unless the news, the chart and the agent's own signal agree.
- If the view **turns against an open trade**, the agent gets out.
- If its signal wobbles but the view still backs the trade, it **lets the winner run**.
- The agent's **mind** (`mind.py`) also weighs "news" and "news+chart" as 2 of its 7 inputs, and learns how much to trust them.
- The **desk** blocks any order that goes against a confident view (`hooks.py`, `against_the_room`).

## Step 3 (Road 2): the News Pipeline
File: `trading/news_pipeline.py`

```python
# 1. TRIAGE: cheap first read
BULL = {"beats": 0.7, "upgrade": 0.6, "stimulus": 0.6, "rally": 0.5 ...}
BEAR = {"misses": -0.7, "downgrade": -0.6, "war": -0.7, "default": -0.8 ...}
confidence = 0.55 + 0.2 * (words found) + 0.25 * |lean|   (cut if words disagree)

# 2. CHECKS: any failure means "not sure"
"no market named", "no evidence", "answers differ"  ->  confidence capped at 0.39

# 3. GATE
if big news (central bank, war, sanctions, election, tariff...): go deep
elif confidence < gate (0.8):                                   go deep
else:                                                           keep the cheap read

# 4. DEEP READ: headline 50% + news mood 30% + chart 20%
lean = 0.5 * headline + 0.3 * news + 0.2 * chart

# 5. HUMAN QUEUE: deep read still under 0.6 sure -> flagged for you
# 6. BILL: cheap $0.0025, deep $0.0235 per headline -> shows the money saved
# 7. AUDIT: some kept answers get a deep read too.
#    Agree under 80% -> gate goes up. Agree over 95% -> gate comes down (0.7 to 0.9).
```

In plain words:
- A **cheap reader** looks at every headline first and says buy/sell/wait and **how sure** it is.
- **Sure (0.8+)**: keep it, done, cheap. **Not sure, or big news**: send it to the **deep read**, which also looks at the news mood and the chart.
- If even the deep read can't tell, it goes to the **human queue** on the Pipeline page.
- The **bill** compares the cost of "cheap only", "deep only" and "our mix".
- **Honest note:** this runs in **demo mode**. The "cheap read" is a word list and the "deep read" is a formula, not real Haiku/Opus calls, and the costs are estimates. The labels say `claude-haiku-5-5` and `claude-opus-5-5` because those are the models it would use.

**Where its result goes** (`trading/decision_desk.py` line 66):
```python
pl, n = pipeline_leans.get(sym)      # recent pipeline reads, newest counts most
heads.append(head_from_value("pipeline", pl, ...))   # one vote on /decision
```
It's one of several votes on the **/decision** page, alongside trend, reversion, order flow, analyst houses and insiders. That page **gives advice only and places no orders**.

## Step 4 (Road 3): Research analysts and the desk score
Files: `trading/academy.py`, `trading/desk.py`

```python
# analyst reads a headline with what it learned at the academy
read = tanh(student.score({event: value}) - student.bias)
macro_view = 0.5 * old_view + read          # fades 5% per tick

# desk
news_view = average(analysts' macro_view)   # given to every trader
score = score + 0.3 * news                  # 30% tilt on the final call
```

In plain words: Research analysts went to school on politics and economy. They score each headline, and their average **tilts the desk's final decision by 30%**. This only works on the simulated wire, because real headlines don't carry the event codes they were trained on.

---

## What this means

- News **already shapes every trade**, through the reading room's view and the analysts' 30% tilt.
- The **News Pipeline** with its gate and bill is built and visible, but it **isn't wired into agent trades**. Its result is only advice on /decision.
- The same gate idea **was tested on trade signals** (`signal_gate.py`). It helped the simulator a little but **hurt on real prices**, so it's **off**.

## Where to look

| Part | File |
|---|---|
| Live news feeds, matching, 2-source check | `trading/news_feed.py` |
| Simulated news wire | `trading/news.py` |
| Reading room (news + chart view) | `trading/reading_room.py` |
| News Pipeline (triage, gate, deep read, bill) | `trading/news_pipeline.py` |
| How traders use the view | `trading/agents.py` (`_confirm`), `trading/mind.py` |
| Desk block against the view | `trading/hooks.py` (`against_the_room`) |
| Analysts' news reading | `trading/academy.py` (`read_headline`) |
| Desk's 30% news tilt | `trading/desk.py` (`_score`) |
| /decision page votes | `trading/decision_desk.py` |

Browse online: https://github.com/Minaalex22/claude-trading-agents/tree/main/trading
