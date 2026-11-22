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
   - What does "done" look like: a number, a page, a passing test? What is **out of scope**?
   - Does it touch money, orders, or a broker? (If yes, load the `trading-safety` skill.)
4. **Offer 2 approaches**, each in this shape, and say which one you'd pick and why:
   - **Approach:** a short name
   - **Pros / cons**
   - **Effort:** S, M or L
   - **Risk:** what could go wrong (money, data, breaking a page)
5. **Wait for a pick.** Then write a short plan, one line per task:
   `[ ] task - file(s) to touch - how to check it (test or command)`
6. **Build test-first:** write a failing test, make it pass, then the next task
   (`python -m unittest discover -s tests -t .`).
7. **Finish with review**: run the `reviewer` agent on the diff before you call it done.

## When to skip

Skip the questions for a one-line fix, a typo, or when the user says "just do it". Still say which
reading of the ask you chose.
