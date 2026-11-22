---
name: trading-safety
description: Use whenever code touches orders, brokers, platforms, risk limits, API keys, logins, or anything that could move real money. Security checklist plus the desk's real-money rules.
---

# Trading safety and security

This desk is **paper trading only**. Real money stays locked until the owner is happy with the win
rate and turns it on by hand. The `real_money_guard` hook in this plugin blocks edits that flip it.

## Real-money rules (never break)

- `LIVE_TRADING_ENABLED` in `trading/platforms.py` stays `False`. Never change it, never add a way
  around it.
- Never set `BRIDGE_REAL_MONEY` to the real-money phrase, and never set a broker `<NAME>_MODE=live`.
- New broker or platform code goes in demo/paper mode first and must go through
  `trading/risk.py` (`RiskManager.approve`) before any order.
- The kill switch must keep working. Never remove a risk stop to make a test pass.
- Polymarket and Kalshi are not legal for Australian users. Don't wire real orders to them.

## Secrets

- Never put an API key, token, password or cookie secret in code, tests, logs, or commits. Read them
  from environment variables, and add the name (no value) to `.env.example`.
- Never print a secret, even partly. Log "key set" or "key missing" only.
- If a user pastes a key into chat, tell them to revoke it and make a new one.

## Web and API (OWASP basics)

- Every `/api/` route that changes something checks the login (`trading/webauth.py`) first.
- Compare tokens with `hmac.compare_digest`, never `==`.
- Validate every input: type, range, allowed symbols. Reject, don't guess.
- Escape anything shown in HTML. No `innerHTML` with data from the user or a feed; use `textContent`.
- No `eval`, no `pickle` on outside data, no shell commands built from user input.
- Fetch only from known hosts; time out every network call.

## Before you finish

Run this list against your diff, then run the tests:
`python -m unittest discover -s tests -t .`
