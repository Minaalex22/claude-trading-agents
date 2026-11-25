# Every request, checked against the code (25 Nov 2026)

I read the whole project chat, from 23 Oct to today, and checked each request against the live code (Minaalex22/claude-trading-agents, main). Real money stays locked.

**Key:** ✅ Done · 🆕 Built now (PR #48) · 🔑 Needs you (parked to the end, as you asked)

## Agents and brains

| You asked for | Status | Where in the code |
|---|---|---|
| 1,400 agents in 6 departments, managers Atlas, Sage and Sentinel | ✅ | org.py, managers.py, /conductor |
| Every agent has its own brain, learns and decides on its own | ✅ | brain.py, mind.py |
| Each brain in 2 steps: how it thinks, then how it decides | ✅ | mind.py (think, then decide) |
| Fast + slow brain (bicameral: creator and governor) | ✅ | dual_brain.py, wired into agents.py |
| Train each department (politics and economy, reading numbers, deciding) | ✅ | academy.py, hourly exams |
| Demo training, 20+ trades an hour, upgrade every hour | ✅ | desk.py practice quota, hourly retrain |
| Review every 15 minutes and learn from mistakes | ✅ | review.py |
| Study the great traders (Gann angles, Square of 9, time cycles, Livermore, Wyckoff, Elliott and more) | ✅ | masters.py, methods.py (19 masters) |
| Read news and charts, and use Claude's agent program ideas | ✅ | reading_room.py, coach.py, hooks.py, memory.py |
| Every file from the pinned chat in the brain | ✅ | brain update, 23 Nov (training report) |
| Practice-quota trades never leak into learning or real decisions | ✅ | agents.py, dual_brain.py, night_shift.py |
| Atlas can bench more of a department when it's losing | ✅ | managers.py deep bench (ATLAS_DEEP) |
| Team relay: Sage regime, then Sentinel size and stop, then Atlas picks the department | ✅ | team_relay.py (PR #47) |
| Brain code explained (main brain and the individual brains) | ✅ | project files brain-code/ (4 guides + cluster graph) |
| matrix_lora_distill.py: train each role's brain from checked trades | 🆕 | brain_distill.py: SFT + DPO files and an Ollama model file per department |
| Confidence router with quarter-Kelly and superforecasting | ✅ | meta_gate.py (quarter-Kelly), fast_gate.py (half Kelly), reading_room.py |

## Your trading rules

| You asked for | Status | Where in the code |
|---|---|---|
| Asia calm, London breaks it, New York finishes the move | ✅ | sessions.py session rhythm |
| First 15 to 30 minutes are the banks setting up, the first move is fake | ✅ | sessions.py (OPEN_MINUTES, OPEN_FAKE, 15-min stock set-up) |
| Money rules: $10 risk cap, 2.4x target, 2 open max, pause after 2 losses, 2% daily stop | ✅ | money_rules.py, /decision |
| Fast decision gate under 1.5 seconds | ✅ | fast_gate.py |
| All your patch batches switched on and measured | ✅ | PRs #28 to #31, #35 to #37, #39 (Sniper and Heikin Ashi off by your choice) |
| Stop the 5m fee bleed, separate trend and reversion exits | ✅ | hooks.py, exits.py |
| Correlation and exposure cap across markets | ✅ | risk.py macro buckets + gross exposure cap |
| ATR volatility-parity sizing | ✅ | ops_guard.py, risk.py (VOL_SIZING) |
| Rollover shield, stale-feed watchdog, cheaper news reads | ✅ | ops_guard.py, watchdog.py, reading_room.py (PR #45) |
| 3-gate live promotion before real money | ✅ | strategy_lab.py ("sign off" in JARVIS) |
| master_pipeline.py: 9 checks on every order | ✅ | master_pipeline.py, trail on /audit |
| maker_execution.py: passive limits, 2 bars, 0.35R runaway, broker drift 0.15% | ✅ | execution_guard.py (PASSIVE_ENTRIES, BROKER_DRIFT) |
| state_store.py: save learning in SQLite WAL so restarts never wipe it | 🆕 | state_store.py + memory.py |
| integration_wiring.py: everything wired together | 🆕 | integration_wiring.py: 20-step map with a test that fails if any step is unplugged |
| Claude's 11 functions blueprint | ✅ | blueprints/; Time Machine, event guard, checker, session brief, daily close (PR #41, #42, #44) |
| Day-trader skill pack (11 skills) | ✅ | /timemachine, /audit (PR #41, #42) |
| Night Shift overnight desk | ✅ | night_shift.py (PR #33) |
| Orbit: 5 strategies, 7-year replay | ✅ | orbit_portfolio.py, /orbit (PR #43) |
| RSI(2) strategy | ✅ | added, switched off (lowered the win rate on real prices) |

## Pages and design

| You asked for | Status |
|---|---|
| Black and orange, same menu and header on every page | ✅ |
| News first page with footprint, order flow and analyst houses (NY, London, China, Singapore) | ✅ |
| Trades page (every trade, won or lost) | ✅ |
| News Pipeline page (triage, gate 0.8, deep read, bill) and News Lab | ✅ |
| A page for each market: Crypto, Forex, Gold, Indices, Shares, plus BTCC | ✅ |
| Polymarket platform (real markets, demo bets, agents' odds, Demo/Real switch) | ✅ |
| Clusters and graphs (3D cluster view, galaxy, cluster graph) | ✅ |
| JARVIS as an Iron Man hologram (no ghost), voice, answers only you; Apex avatar | ✅ |
| Trading calculator, wallet, 24/7 session desks, desktop app | ✅ |
| One account across all platforms + ATAS, Demo/Live switch any time | ✅ (/account) |

## Platforms

| You asked for | Status |
|---|---|
| Mitrade bridge (built from its website) | ✅ /mitrade, live prices + practice desk |
| ATAS one-click connect | ✅ built; 🔑 needs one test run on your Mac |
| eToro, MetaTrader 5, moomoo, Polymarket, Betfair, BTCC bridges | ✅ built, practice mode |

## Needs you (parked to the end)

1. 🔑 **ATAS:** run the one line from One account > Connect ATAS on your Mac once.
2. 🔑 **MetaTrader 5:** add the account in MetaApi with your AvaTrade login number and server.
3. 🔑 **Live keys on Render** for eToro, Betfair and the rest, when you want to go live.
4. 🔑 **LangChain guide:** upload the PDF.
5. 🔑 **Whale-flow (0DTE options) data feed:** the code is ready, it needs a paid data source.

Not in this list on purpose: the real-price win rate, which another thread is working on.
