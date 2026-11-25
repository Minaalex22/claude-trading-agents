# Day trader skills (installed)

The 11 skills from github.com/Jakeschincariol/day-trader-skill (MIT, commit 3d0f4ef), installed as
project skills. Any Claude Code session opened in this repo gets them as slash commands:

| Command | Job | In the ATS Matrix app |
|---|---|---|
| /trader-scanner | Opening-range breakouts, flags, oversold | Scanner on /timemachine |
| /trader-news | Pre-market headlines | News page, News Pipeline |
| /trader-entry | First 5-min close outside the range | Entry time on /timemachine |
| /trader-exit | Stop + target | Stop at range middle, target 2.4x |
| /trader-timemachine | 10,000 replays of the rest of the day | /timemachine |
| /trader-risk | PASS/BLOCK against limits | Risk card + Sentinel's checker |
| /trader-budget | Whole shares from the max loss | Budget card |
| /trader-broker | Orders only after a PASS and your typed yes | Demo practice tickets |
| /trader-alerts | Price / stop alerts | Trades page + Telegram |
| /trader-analyst | Earnings reports | Event guard |
| /trader-pelosi | Congress stock trades | Congress card on /timemachine |

House rules on top of the pack: paper / demo only. Real money stays locked behind the One account
Live switch and the live unlock gate; /trader-broker is used with Alpaca **paper** keys only, and keys
never go in chat. Each skill writes its files to a `trader/` folder in the working directory (ignored by git).

On a Mac with Claude Code, the same pack installs everywhere with:

```
/plugin marketplace add Jakeschincariol/day-trader-skill
/plugin install day-trader-skill@day-trader-skill
```
