#!/usr/bin/env python3
"""Real-money guard: a PreToolUse hook for Claude Code.

Claude Code sends the tool call as JSON on stdin. If the call would switch real-money trading on,
this prints why on stderr and exits 2, which blocks the call and tells Claude the reason.
Anything else exits 0 and the call goes ahead. Standard library only.
"""
import json
import re
import sys

# Each pattern is something that turns real money on in this repo.
DANGER = [
    (re.compile(r"LIVE_TRADING_ENABLED\s*=\s*True"), "sets LIVE_TRADING_ENABLED = True"),
    (re.compile(r"BRIDGE_REAL_MONEY\s*=\s*['\"]?I-ACCEPT-THE-RISK"), "sets BRIDGE_REAL_MONEY to the real-money phrase"),
    (re.compile(r"\b[A-Z][A-Z0-9]*_MODE\s*=\s*['\"]?live\b", re.IGNORECASE), "sets a broker <NAME>_MODE to live"),
]


def texts(tool_input: dict) -> list[str]:
    """The parts of a tool call that could carry new code or a command."""
    out = []
    for key in ("new_string", "content", "command", "new_source"):
        value = tool_input.get(key)
        if isinstance(value, str):
            out.append(value)
    for edit in tool_input.get("edits") or []:
        if isinstance(edit, dict) and isinstance(edit.get("new_string"), str):
            out.append(edit["new_string"])
    return out


def check(event: dict) -> str | None:
    """Return the reason to block, or None to allow."""
    tool_input = event.get("tool_input") or {}
    for text in texts(tool_input):
        for pattern, why in DANGER:
            if pattern.search(text):
                return why
    return None


def main() -> int:
    try:
        event = json.load(sys.stdin)
    except (ValueError, OSError):
        return 0  # not a tool call we understand; never break the session
    reason = check(event)
    if reason:
        print(
            f"Blocked by trading-pack real-money guard: this {reason}. Real-money trading stays off "
            "until the owner turns it on by hand. Keep working in paper mode.",
            file=sys.stderr,
        )
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
