# Blueprint: the day-trader-skill pack's 11 skills, mapped onto ATS Matrix

Made 24 Nov 2026. Paper / demo only. Source: github.com/jakeschincariol/day-trader-skill (MIT, the reel by salmanalfares00). We read it, didn't run it, and rebuilt the useful parts in our own code.

| # | Their skill | What it does | In ATS Matrix | Status |
|---|---|---|---|---|
| 1 | /trader-scanner | Opening-range breakouts, flags, oversold | Opening-range scanner on /timemachine (5/15/30 min, 21 stocks) | Built (PR #41) |
| 2 | /trader-news | Pre-market headlines | News page + News Pipeline | Had it |
| 3 | /trader-entry | First 5-min close outside the range | Entry time on /timemachine, filled by the scanner | Built (PR #41) |
| 4 | /trader-exit | Stop + target | Stop at range middle, target 2.4x (our money rule) | Built (PR #41) |
| 5 | /trader-timemachine | 10,000 replays of the rest of the day | /timemachine: same odds as the reel on the TSLA trade (29.6 / 25.3 / 45.1) | Built (PR #41) |
| 6 | /trader-risk | PASS/BLOCK against limits | Budget and risk card (lower of 1% and $10, 2.4x, 2% daily, Time Machine odds) + Sentinel's checker on every desk order | Built (PR #41, #42) |
| 7 | /trader-budget | Whole shares from the max loss | Same card, 10% cash kept back | Built (PR #41) |
| 8 | /trader-broker | Orders only after two yeses | Demo practice tickets only, after a PASS. Real money locked | Built (PR #41) |
| 9 | /trader-alerts | Price / stop / bell alerts | Trades page + Telegram alerts (existing) | Had it |
| 10 | /trader-analyst | Earnings reports | Event guard keeps the desk out of earnings (24h before to 1h after) | Built (PR #42) |
| 11 | /trader-pelosi | Congress stock trades | Congress trades card (House Clerk filings, links to each report) | Built (PR #41) |

Also from mina's own draft: event guard for big economic news and the audit log page (/audit), PR #42.
