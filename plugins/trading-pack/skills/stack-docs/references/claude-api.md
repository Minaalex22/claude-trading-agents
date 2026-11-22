# Claude API (JARVIS and minds)

- Package: `anthropic` (the only non-standard dependency, see `requirements.txt`). Code must still run
  without it: check for `ANTHROPIC_API_KEY` and fall back to the rule-based answer.
- JARVIS: `trading/jarvis.py`. Model from `JARVIS_MODEL`, default `claude-opus-5-5`. Client is made with
  `anthropic.Anthropic(timeout=30.0, max_retries=1)`; calls use `client.messages.create(model=...,
  max_tokens=..., system=..., messages=[...])`.
- Always set a timeout and a small `max_tokens`. Never log the API key or the full prompt with secrets.
- For the newest SDK features, look them up (Context7 or the `claude-api` skill) instead of guessing.
