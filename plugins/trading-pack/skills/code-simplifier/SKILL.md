---
name: code-simplifier
description: Use after a feature works, or when asked to clean up, refactor, or shrink code. Finds code to delete without changing behaviour.
---

# Code simplifier

Goal: the same behaviour with less code. Tests must pass before and after.

## Checklist

1. Run the tests first so you know they pass: `python -m unittest discover -s tests -t .`
2. Find **3 places to delete code** without changing what it does:
   - dead code, unused imports, unused parameters, commented-out blocks
   - a helper or class used once: inline it
   - nesting deeper than 3: return early instead
   - copy-pasted blocks: one function
   - settings nobody changes: a constant
3. Prefer the standard library. This app runs on the standard library plus `anthropic`; don't add a
   package to save five lines.
4. Keep names plain and comments short. A comment says why, not what.
5. Don't touch risk limits, the kill switch, or real-money locks while simplifying.
6. Run the tests again. Show the line count before and after.
