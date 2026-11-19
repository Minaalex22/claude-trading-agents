# claude-trading-agents

## Working preferences

- When the user comes back, start with a trade report before anything else: paper desk profit and loss (session and today), equity, orders, agents' wins and losses, proven and top agents, risk stops, stale prices, and any real-money proposals and fills. Fetch it from the deployed app's read-only `GET /api/report` (bearer `REPORT_TOKEN`, a network secret on the session's environment); if it can't be reached, say so and why.
