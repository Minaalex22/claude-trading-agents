# JARVIS / ATS Matrix: the whole system blueprint

*Written 25 Nov 2026 from the code on `Minaalex22/claude-trading-agents` main (last merge PR #49) plus open PR #50. No passwords or keys are in this file.*


## 1. What it is, in one paragraph

One web app (Python, Docker) that runs a trading desk of **1,400 AI agents** on **live market prices** with a **paper (practice) broker**. Agents read charts and news, vote, and three managers turn the votes into orders. Every order passes a stack of filters and safety guards, is booked into a portfolio, and is written to an audit log. The app runs 24/7 on Render, has about 36 pages behind a login, a JARVIS voice assistant, and bridges to real platforms. **Real money is locked** until the live-unlock gate is passed and you type GO LIVE.

## 2. The layers

| # | Layer | What it does | Main files |
|---|---|---|---|
| 1 | **Data in** | Live prices, news, calendars, ATAS feed | live_market.py, news_feed.py, event_guard.py, atas_feed.py, mitrade.py |
| 2 | **Read** | Indicators, order flow / footprint, reading room (60% chart, 40% news), news pipeline, market regime, stale-feed watchdog | indicators.py, orderflow.py, reading_room.py, news_pipeline.py, regimes.py, watchdog.py |
| 3 | **Think (agents)** | 1,400 agents, each with its own brain. Think, then decide. Fast brain proposes, slow brain governs | agents.py, brain.py, brains.py, mind.py, dual_brain.py |
| 4 | **Agree (managers)** | Team relay: Sage names the regime, Sentinel sets size/stop/target, Atlas lets only the matching department trade. Trend anchor keeps the desk with the 200-day trend (PR #50) | team_relay.py, managers.py, trend_anchor.py |
| 5 | **Filter (confluence)** | Entry filters, SMT divergence, smart money, session rhythm (Asia calm, London breaks, NY finishes; first 15-30 min fake), event guard, meta gate (quarter-Kelly), fast gate under 1.5 s | entry_filters.py, smt_filter.py, smart_money.py, sessions.py, meta_gate.py, fast_gate.py, signal_gate.py |
| 6 | **Protect (ops guards)** | 9-stage order check, money rules, risk caps, macro buckets, net-USD guard, rollover shield, constant-R volatility sizing, Sentinel trade checker, passive entries, broker drift guard | master_pipeline.py, money_rules.py, risk.py, correlation_guard.py, ops_guard.py, trade_checker.py, execution_guard.py, execution_integrity.py |
| 7 | **Book (portfolio)** | Orbit 5 arms with heat sizing, Night Shift overnight capsule, 5 session desks, One account across platforms | orbit_portfolio.py, night_shift.py, session_desks.py, one_account.py, desk.py |
| 8 | **Send (brokers)** | Paper desk by default; demo/practice bridges; live only after the gate | broker.py, bridge.py, live_orders.py, platforms.py, metaapi.py, betfair.py, btcc.py |
| ★ | **Governance** | Audit trail per order, 15-min reviews, hourly retrain and exams, daily close, strategy lab 3-gate sign-off, 8% drawdown unlock gate, Demo/Live switch, KILL file, restart-safe state | review.py, academy.py, daily_close.py, strategy_lab.py, state_store.py, memory.py |

### 2a. The agents and managers

- **1,400 agents = 4 departments × 350**: Trend, Reversion, Research, Risk (`org.py`).
- On the Agents page they show as **6 floor departments**: Entry (Trend), Scalper (Reversion), News + Exit (Research split in half), Risk + Hedge (Risk split in half) (`conductor.py`).
- **Managers** (they don't trade): **Atlas** runs Entry + Scalper and can bench a losing department; **Sage** runs News + Exit and names the regime; **Sentinel** runs Risk + Hedge, sizes every trade and vetoes anything over the limits.
- **Training**: academy courses by role, exams set starting trust, 20+ practice trades an hour, hourly refresher + re-exam, 19 masters' methods (Livermore, Gann angles / Square of 9 / time cycles, Wyckoff, Elliott, Turtles, Soros, Simons, Thorp and more).
- Practice-quota trades never count toward learning, win rate or real decisions.
- `brain_distill.py` exports checked trades per department as training files (SFT/DPO + an Ollama model file). Export only.

## 3. Tick to order: the pipeline

1. **Price arrives** (poll). A jump over 8% needs a second reading. Watchdog flags stale feeds.
2. **Read**: indicators update (cached), reading room blends chart and news (headlines de-duplicated and batched before any Claude call).
3. **Agents vote**: each brain thinks, then decides; dual brain checks the setup's track record.
4. **Team relay**: Sage regime → Sentinel size, stop, target → Atlas picks the department. The desk only acts above the 0.72 conviction bar, or with the trend anchor's base size when the 200-day trend agrees (PR #50).
5. **Confluence filters**: sessions, entry filters, SMT, smart money, event guard (pause around FOMC, CPI, earnings), meta/fast gate.
6. **9-stage order check** (`master_pipeline.py`): feed → orbit → events → parent → room → whale → sizing → night → checker. Every order keeps this trail (see /audit, or ask JARVIS "why did we buy X").
7. **Guards**: money rules ($10 risk cap, 2.4× target, 2 open max, pause after 2 losses, 2% daily stop on the money-rules desk), macro bucket cap, net-USD guard, rollover shield, volatility-parity sizing, Night Shift limits overnight.
8. **Execution**: passive limit entries (2 bars, cancel at 0.35R runaway), broker quote drift guard 0.15%, broker-side stops on live orders.
9. **Book**: Orbit arm heat updates on every close; session desk gets the credit; One account limits apply across platforms.
10. **Record**: audit log, trade report (`/api/report`), state saved every 15 min to SQLite (survives restarts), boot reconciliation of any unreported live orders.

## 4. Every page

All behind the login (`/login`). Same black/orange header and menu on every page.

| Page | What it shows |
|---|---|
| `/` and `/news` | News first page: footprint, order flow, analyst houses |
| `/pipeline` | News pipeline: cheap triage, gate 0.8, deep read, bill |
| `/newslab` | News Lab: replays the real news path to test it |
| `/trades` | Every closed trade, won or lost; Night Shift panel |
| `/wallet` | Practice wallet, platform balances, crypto watch list |
| `/account` | One account: all platforms + desks + ATAS, Demo/Live switch, Connect ATAS |
| `/markets` | 25 products, each with its own page |
| `/crypto` `/forex` `/commodities` `/indices` `/shares` | One page per market type |
| `/desks` (+ `/desks/<desk>`) | 24/7 session desks: Sydney, Asia, Europe, London, New York |
| `/orbit` | Orbit portfolio: 5 arms, heat, 7-year replay |
| `/polymarket` | Real Polymarket markets (read-only), paper bets, agents' odds, Betfair Demo/Real |
| `/mitrade` | Mitrade live bid/ask + practice desk |
| `/btcc` | BTCC page + practice desk |
| `/conductor` | Agents floor: 6 departments and managers |
| `/galaxy` | The whole desk on one screen |
| `/3d` | 3D cluster view and agent slider |
| `/duel` | Hunter vs Judge |
| `/decision` | Decision desk: fast gate + money rules |
| `/smart` | Smart-money view |
| `/orderflow` | Order flow and analysts for Atlas |
| `/pairs` | Pairs-trading toolkit |
| `/calculator` | Position size, pip value, R:R, margin, half-Kelly |
| `/timemachine` | Monte Carlo, ORB scanner, Congress trades |
| `/audit` | Sentinel checker results + 9-stage trail per order |
| `/brief` | Session briefs and daily close |
| `/log`, `/events` | Raw desk log and event stream |
| `/jarvis` | JARVIS voice assistant, Iron Man hologram, Apex avatar (pinch to wake) |
| `/health` | Health check for Render |

There are also about 77 `/api/...` endpoints behind the pages, including the read-only `/api/report`.

## 5. Platform bridges

| Platform | Built | Status now | Live money? |
|---|---|---|---|
| **Paper desk** | ✅ | Running 24/7 on live prices | No (it's the practice broker) |
| **MetaTrader 5** (via MetaApi cloud) | ✅ | Ready; needs the account added in MetaApi | Demo orders only; live needs a separate live account id + gate |
| **eToro** | ✅ | Needs eToro API keys on Render | Yes, after the gate (best AU pick for real money) |
| **Betfair AU** | ✅ | Needs Betfair app key/login on Render | Yes, capped, after the gate; each bet needs typed REAL MONEY |
| **Polymarket** | ✅ | Real markets read-only + paper bets | No: not legal in Australia |
| **Mitrade** | ✅ | Live prices from its public feed + practice desk | No: Mitrade has no API |
| **BTCC** | ✅ | Practice desk; read-only client waits | No: BTCC closed its API in Feb 2026 |
| **moomoo** | ✅ | Listed in platforms, practice | Not wired for live |
| **ATAS** | ✅ | One-line installer from One account; never run on your Mac yet | Data only (no orders) |
| **Desktop app** | ✅ | Mac + Windows installers (release `desktop-latest`) | n/a |

## 6. Data sources

- **Prices**: Coinbase, Kraken (crypto); Yahoo Finance (forex, gold, indices, shares); Mitrade public quote feed.
- **News**: Yahoo Finance RSS, Google News, CNBC, Dow Jones, Nasdaq, Seeking Alpha, CoinDesk, Cointelegraph, Decrypt, Bitcoin Magazine.
- **Calendars**: Nasdaq earnings, ForexFactory macro calendar (event guard).
- **Prediction markets**: Polymarket Gamma API (read-only), Betfair Exchange API.
- **Replays/backtests**: 2020-2026 daily history CSVs (crypto, FX, SPY and big tech).
- **Order flow**: estimated from price ticks on the live app; real footprint data comes when ATAS is connected.
- **Optional brain**: Claude (Anthropic API key on Render) for JARVIS and the reading room.

## 7. Deployment

- **Render** web service **ats-matrix-6la7**: https://ats-matrix-6la7.onrender.com. Docker, `python -m trading.cloud`, rebuilt automatically on every merge to `main` (`render.yaml`).
- Login password and secrets live only in Render's Environment tab (never in the repo or chat).
- `MARKET=live` = paper trading on live prices. State is saved to SQLite every 15 minutes.
- Free plan sleeps after 15 min with no visitors and has no permanent disk; the Starter plan gives 24/7 running and a disk.
- Tests: about 59 test files (~700 tests) run on every PR; `integration_wiring.py` fails the tests if any of the 20 wiring steps gets unplugged.

## 8. Results on real prices

Real daily prices 2020-2026, five Orbit arms, $100k paper per arm, costs included (`tools/orbit_backtest.py`).

| Version | Profit factor (all) | Profit factor, unseen 2023-26 | End result |
|---|---|---|---|
| Before trend anchor | 0.32 | 0.55 | −$5.8k |
| **With trend anchor (PR #50)** | **1.65** | **1.22** | **+$13.2k** |

By arm with PR #50: crypto +$7.4k, US shares +$5.4k, USDJPY +$0.9k, gold +$0.5k, **forex majors −$1.0k (still the weak spot)**. Settings were picked on 2020-22 only and checked on 2023-26.

What this means: the desk now makes money on real prices in the replay, mostly by riding long trends. It is still below the bar for real money (sim-vs-real gap, forex losing), so real money stays locked.

## 9. Parked: needs you

1. **ATAS**: run the one line from One account > Connect ATAS on your Mac once.
2. **MetaTrader 5**: add the account in MetaApi with your broker login number and server.
3. **Live keys on Render** (eToro, Betfair and the rest) when you want to go live.
4. **LangChain guide**: upload the "financial services agents in production" PDF.
5. **Whale-flow (0DTE options) feed**: code is ready, needs a paid data source.
6. **PR #50 (trend anchor)**: open; merge it to put the new results on the live app.
