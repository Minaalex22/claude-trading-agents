# Latest status (25 Nov 2026)

This file is newer than the blueprints below. Where they differ, this file is right.

## What changed since the blueprints (PR #50 to #54, all merged)

- **PR #50 Trend anchor**: on daily bars the desk trades with the 200-day trend and holds it. Merged.
- **PR #51**: fill tolerance for noisy OHLC bars, and the one-pass replay inspector `tools/inspect_replay_stats.py`
  (checker summary, chosen-trade win rate, profit factor and drawdown per Orbit arm, live-unlock blockers).
- **PR #52 Mini-JARVIS**: the fast, cheap desk copilot inside JARVIS (tick "fast mode" on /jarvis). Claude Haiku on the
  same Anthropic key, or a local Ollama model via COPILOT_BASE_URL. Reads Sentinel's audit log, saved state, the
  live-unlock gate, replay stats and Orbit equity.
- **PR #53 Forex benched**: forex majors are benched (TREND_ANCHOR_FX=majors), USDJPY is kept. Orbit sleeves are now
  crypto 30%, US shares 30%, gold 20%, USDJPY 20%, forex majors 0%. RSI(2) is on for the Scalpers (RSI2_SHARE=1.0, best
  on 2020-22: PF 2.58 vs 1.93). Intraday desks load about 250 daily closes at boot so the 200-day anchor is ready.
- **PR #54 Persistent disk**: Render Starter plan with a 1 GB disk at /events. State SQLite, desk_audit.jsonl, events and
  memory all live on the disk, so restarts and deploys no longer wipe them. MetaApi demo check script
  `scripts/verify_metaapi_demo.py` (read-only).

## Latest results on real prices (after PR #53)

Real daily prices 2020-2026, costs included (`tools/orbit_backtest.py`). Settings were picked on 2020-22 only.

| Version | PF 2020-22 (seen) | PF 2023-26 (unseen) | End result |
|---|---|---|---|
| Before trend anchor | 0.32 (all) | 0.55 | -$5.8k |
| Trend anchor, PR #50 | 1.65 (all) | 1.22 | +$13.2k |
| Forex benched + RSI(2), PR #53 | 2.58 | 1.72 | +$18.8k |
| **USDJPY benched too (25 Nov, now)** | **2.49** | **2.56** | **+$19.6k** |

All forex is now benched (USDJPY won 22% of its 2023-26 trades). Win rate 2023-26 went from 44.6% to 53.8%. The
desk makes money in the replay mostly by riding long trends. On mina's payoff bar the 2023-26 replay passes overall
(80 trades, 53.8% wins, PF 2.56, payoff 2.21); per arm, every arm is still short of 50 trades, and crypto (payoff 1.41)
fails the win bar. mina's other win-rate ideas (pullback-only entries, a trim at +2R with an ATR trail, turning down
the agents' extra confidence) all did worse on both periods and are off (numbers in trading/trend_anchor.py).
Sleeves: crypto 37.5%, US shares 37.5%, gold 25%, forex 0%. Real money is still locked: the live gates need real-price paper trades, not a replay.

## The live-unlock rules (real money stays locked until all pass)

Per Orbit arm, on the desk's own closed trades on real prices, after costs (`strategy_lab.py`):
1. **Proof**: at least 50 chosen trades, profit factor 1.35 or more, drawdown 8% or less of the arm's sleeve, and
   win rate 60% or more, OR win rate 42% or more with a payoff ratio (average win / average loss, worked out as PF x (1 - win) / win)
   of 1.85 or more.
   mina chose the payoff route on 25 Nov 2026 because the trend anchor wins less often but its winners are about
   twice its losers. Practice-quota trades never count.
2. **Sim vs real**: the arm's sim win rate and real-price win rate differ by 15 points or less (mina raised it
   from 8 on 25 Nov 2026).
3. **Owner sign-off**: mina says "sign off <arm>" to JARVIS (only accepted when gates 1 and 2 pass).
Then the Demo/Live switch on /account, live keys on Render, and typing GO LIVE. Every real-money order still needs
mina's approval. JARVIS and Mini-JARVIS can read the gates but can never unlock them.

## Parked: needs mina

1. ATAS: run the one line from One account > Connect ATAS on the Mac once.
2. MetaTrader 5: add the account in MetaApi (broker login number and server), then run the demo check.
3. Live keys on Render (eToro, Betfair and the rest) when going live.
4. LangChain "financial services agents in production" guide: upload the PDF.
5. Whale-flow (0DTE options) feed: code ready, needs a paid data source.

## People and places

- Owner: mina, in Melbourne, Australia. Times are Melbourne time.
- Repo: Minaalex22/claude-trading-agents. Live app: Render service ats-matrix-6la7 (https://ats-matrix-6la7.onrender.com).
- Platforms for real money from Australia: eToro (picked), IBKR, Betfair AU. Polymarket and Kalshi are not legal in
  Australia, so Polymarket is read-only plus paper bets.
