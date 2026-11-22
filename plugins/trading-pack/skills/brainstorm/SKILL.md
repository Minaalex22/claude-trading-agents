---
name: brainstorm
description: Use before building any new feature, page, agent, or strategy in the trading desk. Asks the questions first, offers two ways to do it, and waits for a yes before writing code.
---

# Brainstorm before building

Never write code on the first message of a new feature. Think first, then build.

## Steps

1. **Say back the goal** in one plain sentence, in the user's own words.
2. **Look before asking.** Read the files the feature touches (start with `CLAUDE.md`, `README.md`
   and the module in `trading/` that fits). Don't ask what the code already answers.
3. **Ask up to 3 short questions**, only ones that change what you build. Good ones:
   - Who sees it: the paper desk, JARVIS, a page, or an agent?
   - What does "done" look like: a number, a page, a passing test?
   - Does it touch money, orders, or a broker? (If yes, load the `trading-safety` skill.)
4. **Offer 2 approaches** in a few lines each: what it is, what it costs, what could go wrong.
   Say which one you'd pick and why.
5. **Wait for a yes.** Then make a short plan: small steps, each one testable.
6. **Build in that order**, test first where you can (`python -m unittest discover -s tests -t .`).
7. **Finish with review**: run the `reviewer` agent on the diff before you call it done.

## When to skip

Skip the questions for a one-line fix, a typo, or when the user says "just do it". Still say which
reading of the ask you chose.
