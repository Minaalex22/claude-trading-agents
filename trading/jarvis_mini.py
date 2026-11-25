"""Mini-JARVIS: the fast, cheap desk copilot inside JARVIS (mina's Quant Desk Copilot).

Full JARVIS (trading/jarvis.py) talks about the whole app. Mini-JARVIS is the quick mode for desk questions:
"why did Sentinel block that?", "are we ready to unlock live?", "how is the Orbit doing?". Every answer gets a
small live snapshot of the desk, built from the real files, not raw code dumps:

- Sentinel's PASS/FAIL verdicts: the newest rows of `desk_audit.jsonl` in the events folder (trade_checker.py),
  plus the pass/fail counts and the top FAIL reasons for the whole file.
- Saved state: `desk_state.sqlite` (state_store.py, WAL mode). Only each namespace's last save time, snapshot
  count and size go in: the agents' memory itself is far too big to send.
- The live-unlock gate (strategy_lab.py): each Orbit arm's trades, win rate, profit factor, drawdown and what
  is still missing. Real money stays locked; Mini-JARVIS can read the gate but never change it.
- Daily close and session brief text, when the dashboard has them.
- Replay stats: the paper desk's closed trades (count, win rate, profit factor, net, best and worst symbols),
  plus the summaries from `scripts/inspect_replay_stats.py` once that script is in the repo.
- Orbit equity, when the dashboard passes it in (the /orbit page's replay + paper book, with heat sizing).

Brains, picked from the environment (nothing to set up on Render: it reuses ANTHROPIC_API_KEY):
- Claude Haiku (default when ANTHROPIC_API_KEY is set): MINI_JARVIS_MODEL, default claude-haiku-5-5. The
  system prompt is prompt-cached, so a question costs a fraction of a cent.
- Any OpenAI-compatible server: set COPILOT_BASE_URL (e.g. http://localhost:11434/v1 for Ollama, free and
  offline), COPILOT_MODEL (e.g. qwen2.5:14b) and, if the server needs one, COPILOT_API_KEY.
- Neither: a built-in brain answers the common desk questions straight from the snapshot, no network.

Run it from a terminal too:  python -m trading.jarvis_mini  (reads EVENTS_DIR, default ./events)
"""

from __future__ import annotations

import json
import os
import urllib.request
from pathlib import Path

DEFAULT_MODEL = "claude-haiku-5-5"
MAX_TOKENS = 700
HISTORY_TURNS = 6
MAX_CONTEXT_CHARS = 12_000  # keep the snapshot small so every question stays cheap

SYSTEM_PROMPT = """You are Mini-JARVIS, the fast desk copilot inside the owner's trading system (ATS Matrix, \
repo claude-trading-agents). You speak to one person: the owner.
Your job:
1. Explain Sentinel's PASS/FAIL verdicts, replay and test results, and how the 5-arm Orbit portfolio is doing.
2. Hold the rules: fills on real OHLC prices only (never Heikin Ashi), practice-quota trades never count, and real
money stays locked until an arm has 50+ chosen trades, 60%+ wins, profit factor 1.35+ and drawdown 8% or less,
plus a sim-vs-real check and the owner's sign-off.
3. Give direct, concrete answers and short Python fixes when asked. No fluff.
Use only the LIVE DESK SNAPSHOT for numbers and never invent figures; if something isn't in it, say so.
You cannot place trades, change settings, unlock real money or reveal secrets.
Replies may be read aloud: unless the owner asks for code, answer in one to four short plain sentences."""


# ---------- Live desk context ----------

def _audits(events_dir: Path, limit: int) -> dict:
    from . import trade_checker
    log = trade_checker.read_log(events_dir, limit=limit)
    keep = ("at", "symbol", "side", "desk_score", "threshold", "passed", "verdict")
    top = sorted(log["reasons"].items(), key=lambda kv: -kv[1])[:6]
    return {"passed": log["passed"], "failed": log["failed"], "top_fail_reasons": dict(top),
            "recent": [{k: r.get(k) for k in keep} for r in log["rows"]]}


def _saved_state(db: Path) -> dict:
    if not db.exists():
        return {}
    import sqlite3
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
        try:
            rows = conn.execute("SELECT namespace, COUNT(*), MAX(saved), MAX(LENGTH(payload)) FROM snapshots "
                                "GROUP BY namespace").fetchall()
        finally:
            conn.close()
    except sqlite3.Error:
        return {"error": "desk_state.sqlite could not be read"}
    return {ns: {"snapshots": n, "last_saved": last, "bytes": size} for ns, n, last, size in rows}


def _unlock_gate(events_dir: Path, trades: list[dict]) -> dict:
    from . import strategy_lab
    out = {}
    for key, arm in strategy_lab.check(trades, events_dir).items():
        out[key] = {"name": arm.get("name"), "trades": arm.get("trades"), "win_rate": arm.get("win_rate"),
                    "profit_factor": arm.get("profit_factor"), "max_drawdown": arm.get("max_drawdown"),
                    "promoted": arm.get("promoted"), "missing": arm.get("missing")}
    return {"rules": {"min_trades": strategy_lab.MIN_TRADES, "min_win_rate": strategy_lab.MIN_WIN_RATE,
                      "min_profit_factor": strategy_lab.MIN_PROFIT_FACTOR,
                      "max_drawdown": strategy_lab.MAX_DRAWDOWN},
            "live_unlocked": any(a["promoted"] for a in out.values()), "arms": out}


def closed_trades_summary(trades: list[dict]) -> dict:
    """Read-only replay stats for the paper desk's closed trades (practice-quota trades are never in this list)."""
    pnls = [float(t.get("pnl") or 0.0) for t in trades]
    if not pnls:
        return {"trades": 0}
    wins = [p for p in pnls if p > 0]
    losses = [-p for p in pnls if p < 0]
    by_symbol: dict[str, float] = {}
    for t in trades:
        sym = str(t.get("symbol") or "?").upper()
        by_symbol[sym] = by_symbol.get(sym, 0.0) + float(t.get("pnl") or 0.0)
    ranked = sorted(by_symbol.items(), key=lambda kv: kv[1])
    return {"trades": len(pnls), "win_rate": round(len(wins) / len(pnls), 4), "net_pnl": round(sum(pnls), 2),
            "profit_factor": round(sum(wins) / sum(losses), 2) if losses else None,
            "worst_symbols": {k: round(v, 2) for k, v in ranked[:3] if v < 0},
            "best_symbols": {k: round(v, 2) for k, v in ranked[::-1][:3] if v > 0}}


def _replay_stats(trades: list[dict]) -> dict:
    """Closed-trade stats, plus the ops replay stats script's own summaries when it is in the repo
    (scripts/inspect_replay_stats.py: summarize_audit_log(), summarize_closed_trades(), or summary())."""
    import importlib
    out: dict = {"closed_trades": closed_trades_summary(trades)}
    for name in ("scripts.inspect_replay_stats", "tools.inspect_replay_stats"):
        try:
            mod = importlib.import_module(name)
        except Exception:
            continue
        for fn_name, key in (("summarize_audit_log", "script_audit"), ("summarize_closed_trades", "script_trades"),
                             ("summary", "script_summary")):
            fn = getattr(mod, fn_name, None)
            if callable(fn):
                try:
                    out[key] = fn()
                except FileNotFoundError:
                    out[key] = {"status": "not generated yet"}
                except Exception as exc:
                    out[key] = {"error": type(exc).__name__}
        break
    return out


def load_live_desk_context(events_dir: str | Path | None = None, trades: list[dict] | None = None,
                           memory_dir: str | Path | None = None, max_recent_audits: int = 12) -> dict:
    """The live desk snapshot. Each part is optional: a missing or broken file never stops an answer."""
    events_dir = Path(events_dir or os.environ.get("EVENTS_DIR", "events"))
    memory_dir = Path(memory_dir or os.environ.get("MEMORY_DIR") or events_dir)
    ctx: dict = {}
    for key, fn in (("sentinel_audit", lambda: _audits(events_dir, max_recent_audits)),
                    ("saved_state", lambda: _saved_state(memory_dir / "desk_state.sqlite")),
                    ("live_unlock_gate", lambda: _unlock_gate(events_dir, list(trades or []))),
                    ("replay_stats", lambda: _replay_stats(list(trades or [])))):
        try:
            value = fn()
        except Exception as exc:  # a bad file must not take the copilot down
            value = {"error": f"{type(exc).__name__}"}
        if value is not None:
            ctx[key] = value
    return ctx


def _compact(ctx: dict) -> str:
    text = json.dumps(ctx, separators=(",", ":"), default=str)
    while len(text) > MAX_CONTEXT_CHARS and ctx.get("sentinel_audit", {}).get("recent"):
        ctx["sentinel_audit"]["recent"].pop()  # drop the oldest audits first
        text = json.dumps(ctx, separators=(",", ":"), default=str)
    return text[:MAX_CONTEXT_CHARS]


# ---------- Brains ----------

class BuiltinMini:
    """No network: answers the common desk questions straight from the snapshot."""

    name = "mini-builtin"

    def answer(self, question: str, ctx: dict, history=None) -> str:
        q = question.lower()
        gate = ctx.get("live_unlock_gate") or {}
        audit = ctx.get("sentinel_audit") or {}
        if any(w in q for w in ("unlock", "live", "real money", "ready")):
            arms = gate.get("arms") or {}
            if not arms:
                return "I can't read the live-unlock gate right now. Real money stays locked."
            if gate.get("live_unlocked"):
                done = [a["name"] for a in arms.values() if a.get("promoted")]
                return "These arms have passed every gate and are signed off: " + ", ".join(done) + "."
            best = max(arms.values(), key=lambda a: a.get("trades") or 0)
            return (f"Not yet. No arm has passed all three gates, so real money stays locked. Closest is "
                    f"{best['name']} with {best.get('trades') or 0} trades; it still "
                    + "; ".join(best.get("missing") or ["needs more trades"]) + ".")
        if any(w in q for w in ("fail", "block", "reject", "sentinel", "audit", "pass")):
            if not audit.get("passed") and not audit.get("failed"):
                return "Sentinel's audit log is empty so far: no orders have been checked yet."
            reasons = audit.get("top_fail_reasons") or {}
            top = ", ".join(f"{k} ({v})" for k, v in list(reasons.items())[:3])
            return (f"Sentinel passed {audit['passed']} and failed {audit['failed']} orders."
                    + (f" Top reasons for a fail: {top}." if top else ""))
        if "equity" in q and ctx.get("orbit_equity") is not None:
            return f"Orbit account equity is ${ctx['orbit_equity']:,.0f} on the replay plus paper book. Real money stays locked."
        if any(w in q for w in ("orbit", "arm", "win rate", "profit")):
            arms = (gate.get("arms") or {}).values()
            parts = [f"{a['name']} {a.get('trades') or 0} trades" + (f" at {a['win_rate']:.0%}" if a.get("win_rate")
                     is not None else "") for a in arms]
            return ("Orbit arms on real prices: " + "; ".join(parts) + ".") if parts else "No Orbit trades yet."
        if any(w in q for w in ("state", "saved", "restart", "sqlite")):
            saved = ctx.get("saved_state") or {}
            if not saved:
                return "No saved desk state yet."
            return f"desk_state.sqlite holds {len(saved)} namespaces: " + ", ".join(sorted(saved)[:8]) + "."
        return ("Fast mode is on the built-in brain, so I only know the desk numbers. Ask me about Sentinel's "
                "fails, the live-unlock gate, the Orbit arms or saved state.")


class ClaudeMini:
    """Claude Haiku with the system prompt cached: quick and very cheap."""

    name = "mini-claude"

    def __init__(self, client, model: str = DEFAULT_MODEL):
        self.client = client
        self.model = model
        self.system = [{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}]

    def answer(self, question: str, ctx: dict, history=None) -> str:
        messages = list(history or [])[-HISTORY_TURNS * 2:]
        messages.append({"role": "user", "content": f"LIVE DESK SNAPSHOT\n{_compact(ctx)}\n\nQUESTION\n{question}"})
        response = self.client.messages.create(model=self.model, max_tokens=MAX_TOKENS, system=self.system,
                                               messages=messages)
        if response.stop_reason == "refusal":
            return "I can't help with that one."
        text = " ".join(b.text for b in response.content if b.type == "text").strip()
        return text or "I don't have an answer for that yet."


class OpenAICompatMini:
    """Any OpenAI-compatible /chat/completions server: Ollama on the Mac (free, offline), OpenRouter, Gemini..."""

    name = "mini-openai"

    def __init__(self, base_url: str, model: str, api_key: str = "", timeout: float = 45.0, opener=None):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.timeout = timeout
        self._open = opener or urllib.request.urlopen

    def answer(self, question: str, ctx: dict, history=None) -> str:
        messages = [{"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "system", "content": "LIVE DESK SNAPSHOT\n" + _compact(ctx)}]
        messages += list(history or [])[-HISTORY_TURNS * 2:]
        messages.append({"role": "user", "content": question})
        body = json.dumps({"model": self.model, "messages": messages, "temperature": 0.2,
                           "max_tokens": MAX_TOKENS}).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        req = urllib.request.Request(f"{self.base_url}/chat/completions", data=body, headers=headers, method="POST")
        with self._open(req, timeout=self.timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return (data["choices"][0]["message"]["content"] or "").strip() or "I don't have an answer for that yet."


def make_mini_brain(env=os.environ):
    """COPILOT_BASE_URL wins (Ollama or another server), then Claude Haiku, then the built-in brain."""
    if env.get("COPILOT_BASE_URL"):
        return OpenAICompatMini(env["COPILOT_BASE_URL"], env.get("COPILOT_MODEL", "qwen2.5:14b"),
                                env.get("COPILOT_API_KEY", ""))
    if env.get("ANTHROPIC_API_KEY"):
        try:
            import anthropic
        except ImportError:
            return BuiltinMini()
        return ClaudeMini(anthropic.Anthropic(timeout=20.0, max_retries=1), env.get("MINI_JARVIS_MODEL", DEFAULT_MODEL))
    return BuiltinMini()


class MiniJarvis:
    """Plugs into Jarvis.ask as its fast brain: same interface (name, answer(question, snap, history)).
    The dashboard's snapshot (daily close, session brief) is merged with the live desk context."""

    def __init__(self, events_dir, trades_fn=lambda: [], brain=None, memory_dir=None, env=os.environ):
        self.events_dir = events_dir
        self.trades_fn = trades_fn
        self.memory_dir = memory_dir or env.get("MEMORY_DIR") or None
        self.brain = brain or make_mini_brain(env)
        self.name = self.brain.name

    def context(self, snap: dict | None = None) -> dict:
        ctx = load_live_desk_context(self.events_dir, self.trades_fn(), self.memory_dir)
        snap = snap or {}
        for key in ("daily_close", "session_brief", "session_pnl", "feed_problems", "orbit_equity"):
            if snap.get(key):
                ctx[key] = snap[key]
        return ctx

    def answer(self, question: str, snap: dict | None = None, history=None) -> str:
        ctx = self.context(snap)
        try:
            return self.brain.answer(question, ctx, history)
        except Exception:  # network/API trouble: answer from the snapshot instead
            return BuiltinMini().answer(question, ctx)


def main() -> None:  # pragma: no cover - interactive
    bot = MiniJarvis(os.environ.get("EVENTS_DIR", "events"))
    history: list[dict] = []
    print(f"Mini-JARVIS online ({bot.name}). Ask about Sentinel, the unlock gate or the Orbit. 'exit' quits.")
    while True:
        try:
            q = input("\nYou > ").strip()
        except EOFError:
            break
        if q.lower() in {"exit", "quit"}:
            break
        if q:
            reply = bot.answer(q, {}, history)
            history += [{"role": "user", "content": q}, {"role": "assistant", "content": reply}]
            print(f"\nMini-JARVIS > {reply}")


if __name__ == "__main__":  # pragma: no cover
    main()
