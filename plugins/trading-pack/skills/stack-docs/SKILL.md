---
name: stack-docs
description: Use before changing the trading desk's web server, agents, brains, risk, brokers, JARVIS, or Claude API calls. Reads the right local doc first so Claude doesn't guess APIs (our own small Context7).
---

# Stack docs (our own Context7)

Before you write code against a part of this app, read its doc in `references/` and the module it
points to. Don't invent function names: check them with a quick search in `trading/`.

| You're touching | Read first |
|---|---|
| Pages, `/api/...` routes, login | `references/web.md` |
| Agents, brains, departments, training | `references/agents.md` |
| Orders, brokers, platforms, risk | `references/trading.md`, then the `trading-safety` skill |
| JARVIS or any Claude API call | `references/claude-api.md` |

If the Context7 MCP server is connected, use it for outside libraries (`resolve-library-id`, then
`query-docs`). For our own code, these local docs and the source win.

When you change how one of these parts works, update its reference file in the same PR.
