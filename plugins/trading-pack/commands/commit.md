---
description: Review the staged changes and write a clean conventional commit
argument-hint: "[optional note about the change]"
allowed-tools: Bash(git status:*), Bash(git diff:*), Bash(git add:*), Bash(git commit:*), Bash(git log:*)
---

Make a clean commit for the current work. Extra note from the user: $ARGUMENTS

1. Run `git status` and `git diff --staged`. If nothing is staged, show `git diff` and ask which files
   to stage. Never stage `.env`, keys, or files with secrets.
2. If the diff mixes unrelated changes, suggest splitting it into separate commits.
3. Write a conventional commit message: `feat:`, `fix:`, `refactor:`, `test:`, `docs:` or `chore:`,
   a short subject (under 72 characters), then a body that says why, not what.
4. If the diff touches orders, brokers, risk or `platforms.py`, check it against the `trading-safety`
   skill first and say so.
5. Show the message and ask for a yes before running `git commit`.
