"""Long-term memory: what the agents learned survives a restart.

Claude's agent program gives agents memory that outlives a session (memory files the agent reads back when
it starts again). Before this, every restart of the desk wiped what 1,400 agents had learned: their trust in
their own strategy, the rules their 15-minute reviews wrote, their lessons and their trade record.

Now each engine shard writes its agents' learning to `memory-<shard>.json` every `SAVE_EVERY` ticks and when
it stops, and reads it back when it starts. Agents are matched by id, so a resized team keeps the memories
of the agents that stay. A missing, old or damaged file is ignored: the desk then starts fresh, as before.

What is kept per agent: brain (trust, experience, hits, recent results), its own rules and any rule change
still on trial, its lessons, its trade record, virtual P&L, its practice account (trading/accounts.py), its mind's
feature weights and its dual brain's ledger, shadow trades and probation list (trading/dual_brain.py). Per desk: Brain
Alpha's style weights, the practice clock (hours, reviews, days), the managers' benched and paused lists, the
meta-label models (trading/entry_filters.py), Orbit's arm heat (each arm's recent results) and tonight's Night
Shift capsule (balance, stop, counters; only if it is still the same night). The paper account itself starts
fresh on every run. Paper only.

Every save is also copied into `desk_state.sqlite` (WAL mode, trading/state_store.py); if the JSON file is missing
or damaged on start, the desk reads its learning back from there.

When: every SAVE_EVERY ticks, and also every SAVE_SECONDS (15 minutes) of real time. On live prices a tick is
one 15-second poll, so 600 ticks was 2.5 hours of learning at risk; now it is at most 15 minutes.
"""

import json
import os
import time
from pathlib import Path

VERSION = 1
SAVE_EVERY = 600  # ticks (5 minutes at 0.5 s ticks)
SAVE_SECONDS = 900.0  # and at least every 15 minutes of real time, whatever the tick length
WORKSPACE_KEYS = ("trade_wins", "trade_losses", "trades_total", "reviews", "lessons", "kept", "undone", "benched",
                  "exits", "by_sure", "dual")
NIGHT_FRESH = 2 * 3600  # a saved night capsule only comes back if the restart is this soon (same night)
NIGHT_KEYS = ("start_budget", "current_balance", "halted", "halt_reason", "briefing_delivered_date", "active",
              "session_date", "trades_count", "wins", "losses", "skipped_toxic", "timeline")


def due(last_saved: float | None, now: float, tick: int) -> bool:
    """Time to save: every SAVE_EVERY ticks, or SAVE_SECONDS of real time since the last save."""
    return tick % SAVE_EVERY == 0 or (last_saved is not None and now - last_saved >= SAVE_SECONDS)


def desk_extras(desk) -> dict:
    from . import entry_filters
    out = {"benched": sorted(desk.managers.benched), "paused": sorted(desk.managers.paused),
           "meta": {k: {"w": m.w, "bias": m.bias, "seen": m.seen, "brier_sum": m.brier_sum, "wins": m.wins,
                        "buckets": m.buckets} for k, m in entry_filters.META.items()}}
    orbit = getattr(desk, "orbit", None)
    if orbit is not None:
        out["orbit"] = {"equity": orbit.account_equity, "recorded": orbit.recorded,
                        "arms": {a.key: list(a.recent_pnl_usd) for a in orbit.arms.values()}}
    ns = getattr(desk.risk, "night_shift", None)
    if ns is not None:
        out["night"] = {k: getattr(ns, k) for k in NIGHT_KEYS}
        out["night"]["carried"] = sorted(ns.carried)
        out["night"]["saved_at"] = time.time()
    return out


def restore_extras(desk, d: dict) -> None:
    from . import entry_filters
    from .dual_brain import Ledger
    try:
        desk.managers.benched = set(d.get("benched", ()))
        desk.managers.paused = set(d.get("paused", ()))
        for k, m in (d.get("meta") or {}).items():
            model = entry_filters.META.setdefault(k, entry_filters.MetaLabeler())
            model.w.update({x: float(v) for x, v in m["w"].items() if x in model.w})
            model.bias, model.seen = float(m["bias"]), int(m["seen"])
            model.brier_sum, model.wins = float(m["brier_sum"]), int(m["wins"])
            model.buckets = {b: list(v) for b, v in m["buckets"].items()}
        o, orbit = d.get("orbit"), getattr(desk, "orbit", None)
        if o and orbit is not None:
            orbit.account_equity, orbit.recorded = float(o["equity"]), int(o["recorded"])
            for a in orbit.arms.values():
                a.recent_pnl_usd.clear()
                a.recent_pnl_usd.extend(float(x) for x in o["arms"].get(a.key, ()))
        n, ns = d.get("night"), getattr(desk.risk, "night_shift", None)
        if n and ns is not None and n.get("active") and time.time() - float(n.get("saved_at", 0)) < NIGHT_FRESH:
            for k in NIGHT_KEYS:
                if k in n:
                    setattr(ns, k, n[k])
            ns.carried = set(n.get("carried", ()))
    except (KeyError, TypeError, ValueError, AttributeError):
        pass
    for a in desk.agents:
        dual = a.workspace.get("dual")
        if isinstance(dual, dict) and not isinstance(dual.get("ledger"), Ledger):
            dual["ledger"] = Ledger({k: list(v) for k, v in (dual.get("ledger") or {}).items()})


def agent_state(a) -> dict:
    b = a.brain
    return {"brain": {"weight": b.weight, "experience": b.experience, "wins": b.wins, "memory": list(b.memory)},
            "rules": dict(a.rules), "trial": a.trial, "pnl": a.pnl,
            "workspace": {k: a.workspace[k] for k in WORKSPACE_KEYS if k in a.workspace},
            "account": a.account.state() if getattr(a, "account", None) is not None else None,
            "mind": a.mind.state() if getattr(a, "mind", None) is not None else None}


def save(desk, path: Path) -> None:
    data = {"version": VERSION, "tick": desk.ticks,
            "desk": {"hours": desk.hours, "reviews": desk.reviews, "days": desk.days,
                     "alpha": dict(desk.alpha.weights) if desk.alpha else None, **desk_extras(desk)},
            "agents": {a.agent_id: agent_state(a) for a in desk.agents}}
    path = Path(path)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
    os.replace(tmp, path)
    _mirror(path, data)


def _mirror(path: Path, data: dict) -> None:
    """Also keep the snapshot in SQLite (WAL mode), so a damaged JSON file never loses the learning."""
    from . import state_store
    if not state_store.ENABLED:
        return
    try:
        store, ns = state_store.for_memory(path)
        store.save_namespace(ns, data)
    except Exception:  # the JSON file is already saved; a SQLite problem must never stop the desk
        pass


def _load(path: Path) -> dict | None:
    """The JSON memory file, or (if it is missing or damaged) the latest SQLite copy of it."""
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    from . import state_store
    if not state_store.ENABLED:
        return None
    try:
        store, ns = state_store.for_memory(path)
        return store.load_namespace(ns) if store.db_path.exists() else None
    except Exception:
        return None


def restore(desk, path: Path) -> int:
    """Load saved learning into the desk's agents. Returns how many agents remembered."""
    try:
        data = _load(path)
        if not isinstance(data, dict) or data.get("version") != VERSION:
            return 0
        saved = data["agents"]
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return 0
    restored = 0
    for a in desk.agents:
        s = saved.get(a.agent_id)
        if not isinstance(s, dict):
            continue
        try:
            b = s["brain"]
            a.brain.weight = float(b["weight"])
            a.brain.experience = int(b["experience"])
            a.brain.wins = int(b["wins"])
            a.brain.memory.clear()
            a.brain.memory.extend(float(x) for x in b["memory"])
            a.rules.update({k: float(v) for k, v in s["rules"].items() if k in a.rules})
            a.trial = s.get("trial")
            a.pnl = float(s.get("pnl", 0.0))
            a.workspace.update({k: v for k, v in s.get("workspace", {}).items() if k in WORKSPACE_KEYS})
            if getattr(a, "account", None) is not None and isinstance(s.get("account"), dict):
                a.account.load(s["account"])  # its practice-account record, so a track record survives restarts
            if getattr(a, "mind", None) is not None and isinstance(s.get("mind"), dict):
                a.mind.load(s["mind"])  # how it thinks and decides, as it learned it
        except (KeyError, TypeError, ValueError):
            continue
        restored += 1
    d = data.get("desk") or {}
    desk.hours = int(d.get("hours", 0))
    desk.reviews = int(d.get("reviews", 0))
    desk.days = int(d.get("days", 0))
    restore_extras(desk, d)
    alpha = d.get("alpha")
    if desk.alpha and isinstance(alpha, dict) and set(alpha) == set(desk.alpha.weights):
        desk.alpha.weights = {k: float(v) for k, v in alpha.items()}
        desk.alpha._pnl_at_last = desk.style_pnl()
    return restored
