# Blueprint: Claude's 11 finance functions, mapped onto our trading system

Made 24 Nov 2026. Paper / demo only. Nothing here turns on real money.


## Which "new Claude system" this is

The closest match is Anthropic's **"Agents for financial services"** (released 5 May 2026, updated Oct 2026). Anthropic's own page lists **10 ready-made agent templates**. Each one is built from three parts: **skills** (instructions and know-how), **connectors** (safe data feeds) and **subagents** (helper Claudes for side jobs). They all run on one **shared engine**, Claude Managed Agents, with per-tool permissions, a locked key vault, a full audit log and a human who approves before anything is acted on. That engine is counted here as **function 11**.

It was built for banks and funds, not for bot trading, so per Mina's rule we **take only what suits us**. If you saw a different post or reel with 11 functions, send a screenshot and this gets redone to match it.

Sources: [Anthropic: Agents for financial services](https://www.anthropic.com/news/finance-agents) · [Analytics Vidhya write-up](https://www.analyticsvidhya.com/blog/2026/05/claude-finance-agents/) · [The Register](https://www.theregister.com/2026/05/05/anthropic_unleashes_finance_agents_claude/)

## The 11 functions, one by one

| # | Anthropic function | What it does (theirs) | Our version | We have | Missing | Take it? |
|---|---|---|---|---|---|---|
| 1 | Pitch builder | Target lists, comparables, pitch decks | **Idea scout**: ranked top-5 setups each session, with the reason | /decision outlook, Markets pages, Pairs page | One daily ranked shortlist with a written "why" | Yes |
| 2 | Meeting preparer | Briefs before client calls | **Session brief**: one page before Asia / London / NY open | News page, Desks (London etc.), JARVIS | Auto-written brief per session | Yes |
| 3 | Earnings reviewer | Reads earnings + filings, flags changes | **Event guard**: earnings and big-news calendar; shrink or pause shares trades around them | News feed reads headlines | Calendar + pause rule (AAPL, NVDA, TSLA...) | Yes, high value |
| 4 | Model builder | Builds and updates financial models | **Strategy lab**: keeps a backtest per strategy on real prices | Sim market, academy, masters, PR #22 filters | Real-price backtest; real win rate is only 47-52% vs ~63% in sim | Yes, high value |
| 5 | Market researcher | Tracks news, flags to risk | **Sage + News Pipeline** (triage, gate 0.8, deep read) | Reading room steers trades; pipeline built | Pipeline only feeds the page and one /decision vote, it doesn't steer trades | Yes, wire it in |
| 6 | Valuation reviewer | Checks numbers against firm standards | **Trade checker**: second-opinion agent checks each desk signal against money rules, writes pass/fail + reason | desk hooks, risk.py, /decision gate | A written check per trade that Sentinel can read | Yes, high value |
| 7 | General ledger reconciler | Matches books, NAV | **Paper ledger check**: fills vs prices vs P&L add up | Trades page, trade log | Daily auto check (later: MT5 statement) | Light |
| 8 | Month-end closer | Close checklist, reports | **Daily close**: P&L, win rate, drawdown, benched agents | Training report, hourly manager bench | Auto end-of-day report | Yes |
| 9 | Statement auditor | Checks statements for mistakes | **Performance audit**: re-run loss audit weekly, check for overfitting | Loss audit (PR #22), practice scorecard | Weekly re-run + sim-vs-real gap check | Yes |
| 10 | KYC screener | Checks client identity for compliance | **Broker check**: broker legal in AU, API up, demo lock on | Demo/Real switch, BTCC auto-off bridge | Small status card | Mostly skip (no KYC needed) |
| 11 | Shared engine (Managed Agents) | Tool permissions, key vault, audit log, human approves | **Our control layer** | risk.py (2% daily loss, 10% drawdown, KILL switch), Demo/Real switch, Atlas/Sage/Sentinel | One audit log of every agent decision + an approval queue before anything real | Yes, core |

## How it fits our system

```
 BEFORE SESSION          FIND + CHECK TRADE                  AFTER TRADE
 1 Idea scout     -->    1,400 agents vote -> DESK   -->     7 Ledger check
 2 Session brief         5 Sage + News Pipeline (steers)     8 Daily close
 3 Event guard  ------>  4 Strategy lab (real backtests)     9 Performance audit
                         6 Trade checker (pass/fail)
                                  |
                                  v
                 risk.py rules -> PAPER order only
 ---------------------------------------------------------------------------
 11 SHARED ENGINE: permissions + key vault + audit log + human approval
    Atlas (trading) . Sage (research) . Sentinel (risk) sit here
 10 Broker check: demo lock on, broker legal in AU
```

## Suggested build order (biggest win-rate effect first)

1. **Event guard (3) + News Pipeline steering trades (5).** Stops trades into earnings and big news.
2. **Trade checker (6).** Every trade gets a written pass/fail before the paper order.
3. **Strategy lab on real prices (4).** Closes the gap between ~63% in sim and 47-52% on real prices.
4. **Daily close + audit log (8, 11).** One report per day and a full record of every decision.
5. Idea scout + session brief (1, 2), then ledger check and broker card (7, 10).

Real money stays locked until the win rate on real prices is good enough for Mina.
