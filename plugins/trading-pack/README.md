# trading-pack: our own Claude Code plugin

Our version of the "top plugins" idea, built for the JARVIS trading desk. Plain markdown plus one small
Python hook. No outside APIs.

| Piece | Type | Copy of | What it does |
|---|---|---|---|
| `brainstorm` | skill | Superpowers | Asks up to 3 questions and offers 2 approaches before any code |
| `frontend-design` | skill | Frontend Design | Our black and orange galaxy HUD look (`#000` + `#ff5c00`) |
| `code-simplifier` | skill | Code Simplifier | Finds code to delete without changing behaviour |
| `trading-safety` | skill | Security Guidance | Real-money rules plus OWASP basics |
| `stack-docs` | skill | Context7 | Reads our own docs for the part of the app it's about to change |
| `reviewer` | agent | PR Review Toolkit | Senior review of the diff: real money, bugs, security, tests |
| `/trading-pack:commit` | command | Commit Commands | Clean conventional commit, asks before committing |
| `real_money_guard` | hook | (ours) | Blocks any edit or command that turns real-money trading on |

## Install

In Claude Code:

```
/plugin marketplace add Minaalex22/claude-trading-agents
/plugin install trading-pack@jarvis-plugins
```

Or from a clone, without GitHub:

```
claude plugin marketplace add ./claude-trading-agents
claude plugin install trading-pack@jarvis-plugins
```

Update later with `/plugin marketplace update jarvis-plugins`.

## Use it

- Skills switch on by themselves when the task fits (building a page loads `frontend-design`).
  You can also say "use the brainstorm skill".
- Reviewer: "use the reviewer agent on my changes".
- Commit: type `/trading-pack:commit`.
- The guard runs on every edit. Try it: ask Claude to set `LIVE_TRADING_ENABLED = True` and it gets blocked.

## Add your own

New skill: make `skills/<name>/SKILL.md` with a `name` and `description` at the top, then bump
`version` in `.claude-plugin/plugin.json` and in `../../.claude-plugin/marketplace.json`.
