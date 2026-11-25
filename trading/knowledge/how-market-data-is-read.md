# How market data and charts are collected and read

Code: `Minaalex22/claude-trading-agents`, main branch as of 24 Nov 2026 (commit `2b21db5`).
Paper trading only. The data code only reads prices and has no way to place an order.

---

## The short version

- **The 1,400 agents only see one thing directly: the price, every tick.** Each agent keeps its own short price history (5 to 60 ticks) and works out its own signal from it (momentum, z-score, breakout and so on).
- On top of that, the **reading room** gives every trader one shared **chart read** (two timeframes) mixed with news.
- The bigger chart tools, like **RSI, MACD, ATR, Bollinger, footprint, order flow and volume profile**, power the **Markets, Order flow and /decision pages**. They **don't feed the 1,400 agents' trades directly**. They are advice on those pages.

## The flow

```
  WHERE PRICES COME FROM
  Practice (default): simulated market (market.py), mixes trending + choppy spells
  Live (MARKET=live): every 15 seconds
     - Crypto (BTC, ETH, SOL): Coinbase, backup Kraken
     - Stocks (AAPL, NVDA...):  Yahoo Finance, backup 2nd Yahoo server
                 |
                 v
  SAFETY CHECKS  (live_market.py)
  - Jump over 8% in one go?  hold it until the next reading confirms
  - Price older than 10 min? mark "stale": exits allowed, no new trades
                 |
                 v
  ONE PRICE PER MARKET PER TICK
        |                         |                                  |
        v                         v                                  v
  EACH AGENT (x1,400)      READING ROOM                     PAGES + /decision
  keeps last 5-60 prices   chart read on 20 + 60 bars       (markets/, orderflow.py)
  - Momentum: % move       both agree -> "up" or "down"     - candles 1m..1d
  - MA cross: fast vs slow  -> 60% of the shared VIEW       - RSI, MACD, ATR, Bollinger
  - Breakout: new high/low                                  - footprint, delta, POC
  - Z-score: how stretched                                  - outlook: buy/sell + why
  - Regime: trending/choppy                                 - order flow read
  - Volatility: too wild?                                     |
  + trend check (4x longer look)                              v
        |                         |                     advice only, no orders
        +-----------+-------------+
                    v
          VOTES -> DESK -> risk rules -> paper order
```

---

## Step 1: collecting prices
Files: `trading/live_market.py` (live), `trading/market.py` (practice)

```python
POLL_SECONDS  = 15     # ask for new prices every 15 seconds
MAX_JUMP      = 0.08   # a move bigger than 8% needs a second reading
CONFIRM_BAND  = 0.02   # ...and that second reading must be within 2%
STALE_SECONDS = 600    # no new price for 10 minutes = stale

# crypto
get("https://api.coinbase.com/v2/prices/BTC-USD/spot")    # backup: Kraken
# stocks
get("https://query1.finance.yahoo.com/v8/finance/chart/NVDA?interval=1m&range=1d")
```

In plain words:
- Free public price feeds. No keys and no accounts.
- **Bad prints are blocked.** A sudden 8%+ jump isn't believed until it shows up twice.
- **Stale prices are blocked.** If a feed goes down or the stock market closes, the desk can exit but can't add.
- In practice mode the simulated market slowly drifts between **trending and choppy** spells, so both kinds of trader get tested.
- Default markets: AAPL, MSFT, NVDA, AMZN, GOOG, TSLA, META, AMD, BTC and ETH (`trading/engine.py`).

## Step 2: what each agent works out from the price
File: `trading/agents.py`

Each agent keeps its **own** window (from 5 to 60 ticks, picked at random so no two agents look the same distance back).

```python
# Momentum (Trend): how far price moved over my window
change = last_price / first_price - 1

# Moving-average cross (Trend): short average vs long average
1 if mean(last quarter) > mean(whole window) else -1

# Breakout (Trend): new high -> buy, new low -> sell

# Z-score (Reversion): how many "normal swings" price is from its average
z = (price - average) / stdev          # far above -> sell, far below -> buy

# Regime analyst (Research): efficiency = straight-line move / total path
efficiency = abs(last - first) / sum(every little move)
# near 1 = clean trend, near 0 = choppy

# Volatility monitor (Risk): if swings are bigger than my limit -> vote smaller size
exposure = vol_limit / stdev(returns)
```

In plain words:
- **Momentum**: "it's been going up, so buy."
- **Z-score**: "it's stretched too far from normal, so bet it comes back."
- **Efficiency** (Research): "is this a clean trend or a messy zig-zag?" This decides **whether the desk listens to Trend or Reversion traders**.
- **Volatility** (Risk): "markets are wild, so trade smaller."

**Extra checks before a Trend trade** (`trading/entry_filters.py`):
```python
er  = efficiency(my prices)                        # must be 0.25+ (really trending)
htf = move over a 4x longer window / its own noise # the bigger trend must agree
```
A Trend trader won't buy if the bigger picture is falling, or if the market is choppy.

## Step 3: the shared chart read (reading room)
File: `trading/reading_room.py`

```python
fast = move over last 20 bars / normal noise
slow = move over last 60 bars / normal noise
if fast and slow disagree: chart = 0          # no clear trend
else: chart = strength, from -1 to +1

view = 0.6 * chart + 0.4 * news
```

In plain words: one chart read per market that **every trader checks**. It only says "up" when **both the short and the long view agree**. With Claude switched on, Claude also looks at a small chart of every market every 15 minutes, and its read counts for half.

## Step 4: the chart tools on the pages
Files: `trading/markets/feeds.py`, `indicators.py`, `outlook.py`, `flow.py`

```python
TIMEFRAMES = {"1m", "5m", "15m", "1h", "4h", "1d"}
# Coinbase candles for crypto, Yahoo candles for everything else.
# If both fail, a simulated feed fills in, and the page says so.

# outlook.py: a buy/sell read with reasons
price > MA20 > MA50     -> +0.30 "Uptrend"
MACD rising             -> +0.20 "momentum rising"
RSI > 70                -> -0.15 "overbought, pullback likely"
RSI < 30                -> +0.15 "oversold, bounce likely"
ATR (average bar size)  -> sets the stop and target distance
```

In plain words: the **Markets pages** (Crypto, Forex, Gold, Indices, Shares) draw candles on 6 timeframes with **moving averages, RSI, MACD, Bollinger bands and ATR**, plus a plain-English outlook ("Uptrend, MACD rising, RSI 62: buyers in control").

## Step 5: order flow and footprint
File: `trading/orderflow.py` (our own ATAS-style engine)

```python
# each trade = price, size, and who was aggressive (buyer or seller)
# if the feed doesn't say: price ticked up = buyer, ticked down = seller

score += 1.5 * delta share     # buyers vs sellers in control
-0.4 if price up but selling delta  ("weak rally")
+0.25 per stacked buy imbalance, -0.25 per stacked sell imbalance
+0.3 x big-trade tilt          # big orders lean buy or sell
+-0.3 absorption               # heavy selling at the low but price closed up
+-0.15 above/below value area  # from the volume profile (POC, VAH, VAL)
```

In plain words: it builds **footprint bars** (buy vs sell volume at each price), **delta**, **volume profile** and **big trades**, then gives one read (direction, confidence and reasons).

**Honest note:** on the live app it has **no real trade feed**. Every price tick counts as one trade of size 1, so the order flow read is a rough estimate. It gets real data only when trades are pushed in (for example from an ATAS add-on on your computer). Its read is one vote on **/decision** (`decision_desk.py`), which is advice only.

## Other data used elsewhere
- **Regime detector** (`trading/regimes.py`): sorts each market into one of four groups (calm trend, calm chop, wild trend, wild chop) using k-means. It's used by the **Whole Matrix desk** (`matrix.py`), not by the 1,400 agents.
- **Market clock** (`trading/sessions.py`): trusts trend signals more in London and New York hours and fades more in quiet Asia hours.

---

## Where to look

| Part | File |
|---|---|
| Live price feeds + bad-price guards | `trading/live_market.py` |
| Practice price generator | `trading/market.py` |
| Agents' own signals | `trading/agents.py` |
| Trend + bigger-timeframe checks | `trading/entry_filters.py` |
| Shared chart read | `trading/reading_room.py` |
| Candles, timeframes, sources | `trading/markets/feeds.py` |
| RSI, MACD, ATR, Bollinger | `trading/markets/indicators.py` |
| Buy/sell outlook with reasons | `trading/markets/outlook.py` |
| Order flow, footprint, volume profile | `trading/orderflow.py`, `trading/markets/flow.py` |
| Regimes (k-means) | `trading/regimes.py` |
| Time-of-day trust | `trading/sessions.py` |

Browse online: https://github.com/Minaalex22/claude-trading-agents/tree/main/trading
