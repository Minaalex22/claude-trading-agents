"""Session brief and Idea Scout (blueprint functions #1, #2 and #10). PRACTICE ONLY.

Before each session opens in Melbourne time (mina's sessions) the desk writes one page:
- Asia opens 10:00, London 18:00, New York 00:30 (Melbourne). The brief is written 15 minutes before.
- Idea Scout: the top 5 setups across our markets, ranked by the Decision desk's confidence (trading/decision_desk.py:
  every head, the money rules and the drift breaker), each with a written "why".
- Upcoming earnings and big-news blackouts from the event guard (trading/event_guard.py).
- What the managers did (Atlas, Sage, Sentinel) and the account switch (One account: Demo or Live).
It goes to the /brief page, to JARVIS ("session brief", "top 5") and to the phone when Telegram alerts are set up.
It is advice only: nothing here places an order.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from .night_shift import _local

# (key, name, open hour, open minute) in Melbourne time
SESSIONS = (("asia", "Asia (Tokyo, Sydney, ASX)", 10, 0),
            ("london", "London (EU and UK open)", 18, 0),
            ("ny", "New York (US open, London overlap)", 0, 30))
LEAD_MINUTES = 15
MIN_CONFIDENCE = 0.15       # weaker ideas aren't worth a line in the brief
TOP = 5


def active_session(now=None) -> str:
    """mina's Melbourne sessions: Asia 9-17, London 17-23, New York 23-9."""
    h = _local(now).hour
    return "asia" if 9 <= h < 17 else "london" if 17 <= h < 23 else "ny"


def next_open(now=None) -> tuple[str, datetime]:
    """The next session open (Melbourne time) after `now`."""
    t = _local(now)
    best = None
    for key, _, hh, mm in SESSIONS:
        at = t.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if at <= t:
            at += timedelta(days=1)
        if best is None or at < best[1]:
            best = (key, at)
    return best


def due(now, last_key: str | None) -> str | None:
    """The session key whose brief is due now (15 minutes before its open), if it wasn't written yet."""
    key, at = next_open(now)
    t = _local(now)
    stamp = f"{key}:{at.strftime('%Y-%m-%d')}"
    if at - t <= timedelta(minutes=LEAD_MINUTES) and stamp != last_key:
        return stamp
    return None


def _why(d: dict) -> str:
    want = "up" if d["direction"] == "buy" else "down"
    agree = sorted((h for h in d.get("heads", []) if h.get("vote") == want),
                   key=lambda h: -(h.get("score", 0) * h.get("weight", 1)))[:3]
    parts = [h["head"] + (f" ({h['why']})" if h.get("why") else "") for h in agree]
    text = f"{d['agree']} of {d['voting']} heads agree on {d['direction']}"
    if parts:
        text += ": " + "; ".join(parts)
    if d.get("reasons"):
        text += ". Watch: " + d["reasons"][0]
    return text


def top_ideas(decisions: list[dict], blocked: dict[str, str] | None = None, top: int = TOP) -> list[dict]:
    """Idea Scout: the strongest buy/sell setups with a written why. Symbols inside an event blackout are left out."""
    blocked = blocked or {}
    out = []
    for d in sorted(decisions, key=lambda x: -x.get("confidence", 0)):
        if d.get("direction") not in ("buy", "sell") or d.get("confidence", 0) < MIN_CONFIDENCE:
            continue
        if d["symbol"] in blocked:
            continue
        out.append({"rank": len(out) + 1, "symbol": d["symbol"], "side": d["direction"].upper(),
                    "confidence": round(d["confidence"], 3), "verdict": d.get("verdict"), "why": _why(d)})
        if len(out) == top:
            break
    return out


def build(decision: dict, calendar: list[dict], guard_check, managers: list, mode: str, armed: bool,
          now=None, session: str | None = None) -> dict:
    """One brief. `guard_check(symbol)` -> (allowed, size, reason) from the event guard."""
    t = _local(now)
    symbols = [d["symbol"] for d in decision.get("decisions", [])]
    blocked, warnings = {}, {}
    for s in symbols:
        ok, size, reason = guard_check(s)
        if not ok:
            blocked[s] = reason
        elif size < 1:
            warnings[s] = reason
    ideas = top_ideas(decision.get("decisions", []), blocked)
    for i in ideas:
        i["event"] = warnings.get(i["symbol"], "clear")
    key = session or active_session(t)
    name = next(n for k, n, _, _ in SESSIONS if k == key)
    totals = decision.get("totals") or {}
    return {
        "session": key, "session_name": name,
        "written": t.strftime("%Y-%m-%d %H:%M Melbourne"),
        "account": {"mode": mode, "armed": armed,
                    "label": "LIVE (real money, One account switch)" if mode == "live" else "DEMO (paper)"},
        "top_ideas": ideas, "blocked": [{"symbol": k, "why": v} for k, v in blocked.items()],
        "events": calendar[:10],
        "managers": [{"name": m.get("manager"), "role": m.get("title"), "call": m.get("call"),
                      "active": f"{m.get('active', 0)}/{m.get('agents', 0)} active"} for m in managers or []][:3],
        "desk": {"trades": totals.get("trades"), "win_rate": totals.get("win_rate"), "pnl": totals.get("pnl"),
                 "drift": (decision.get("drift") or {}).get("why")},
        "note": "Advice only. Nothing in the brief places an order.",
    }


def text(b: dict) -> str:
    """The brief as one message for JARVIS and the phone."""
    lines = [f"{b['session_name']} session brief, {b['written']}. Account: {b['account']['label']}."]
    if b["top_ideas"]:
        lines.append("Top ideas: " + "; ".join(f"{i['rank']}. {i['side']} {i['symbol']} ({i['confidence']:.0%})"
                                                for i in b["top_ideas"]) + ".")
    else:
        lines.append("No strong setups right now: the desk waits.")
    if b["blocked"]:
        lines.append("Blacked out: " + "; ".join(f"{x['symbol']} ({x['why']})" for x in b["blocked"]) + ".")
    soon = [e for e in b["events"] if e.get("impact") == "HIGH"][:3]
    if soon:
        lines.append("Coming up: " + "; ".join(f"{e['name']} at {e['at']}" for e in soon) + ".")
    return " ".join(lines)
