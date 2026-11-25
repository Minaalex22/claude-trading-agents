"""Brain distill: turn the desk's own checked trades into training files for each department's brain.

From mina's matrix_lora_distill.py (23 Nov 2026), taking only what suits our system:

- Read the desk's event log (`events-*.jsonl`). Each desk order writes its nine-stage `pipeline` trail and the
  team-relay blackboard (Sage's regime, Sentinel's stop and target, Atlas's chosen style); each round trip writes
  a `closed` line with won/lost and P&L.
- Keep **verified traces only**: a closed trade whose opening order has a full pipeline trail, where every stage
  passed (no stale feed, no event-guard, room or whale block) and the checker was on. Practice-quota trades
  never reach the desk, so they can never get in.
- Split by department brain: trades the relay gave to **Entry** (trend/breakout) and to **Scalper** (reversion);
  anything without a relay board goes to **Desk**.
- Write, per department:
  - `sft.jsonl`: winning trades as prompt -> answer (what the desk saw -> what it did and why). Fine-tuning rows.
  - `dpo.jsonl`: preference pairs with trading-mistake tags. A loss gives chosen = "STAND ASIDE" over rejected = what
    we did; a win gives chosen = what we did over rejected = "STAND ASIDE". The tag says why (`chop_entry`,
    `low_conviction`, `wide_stop`, `win`).
  - `Modelfile`: an Ollama model file with that department's system prompt, for anyone who runs the brain on
    their own computer. (The Render app can't run Ollama, so nothing here changes how the live desk decides.)
- `summary.json`: rows per department, win rate of the verified trades, mistakes by tag.

CLI: python -m trading.brain_distill <events.jsonl ...> --out brains/
Paper only: this reads logs and writes files; it never places an order.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

DEPARTMENTS = {"TREND": "entry", "BREAKOUT": "entry", "REVERSION": "scalper"}
SYSTEM = {
    "entry": "You are an Entry (trend) trader on the ATS Matrix desk. You only buy strength in a confirmed trend and "
             "stand aside in chop. Answer with BUY, SELL or STAND ASIDE, then one line of reasons.",
    "scalper": "You are a Scalper (mean-reversion) trader on the ATS Matrix desk. You fade stretched moves back to "
               "the mean inside a range and stand aside when a real trend breaks out. Answer with BUY, SELL or "
               "STAND ASIDE, then one line of reasons.",
    "desk": "You are the ATS Matrix desk. Weigh the departments' votes and the nine checks. Answer with BUY, SELL "
            "or STAND ASIDE, then one line of reasons.",
}
LOW_CONVICTION = 0.8  # |score| below this on a losing trade: tagged low_conviction


def read_events(paths) -> list[dict]:
    out = []
    for p in paths:
        try:
            lines = Path(p).read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            if isinstance(ev, dict) and ev.get("type") in ("pipeline", "closed"):
                out.append(ev)
    return out


def verified(trail: dict) -> bool:
    st = trail.get("stages") or {}
    return (str(st.get("feed", "")).startswith("fresh") and st.get("events") == "PASS" and st.get("room") == "PASS"
            and st.get("whale") == "PASS" and st.get("checker") == "PASS")


def pair_trades(events: list[dict]) -> list[tuple[dict, dict]]:
    """(opening pipeline trail, closed line) for each round trip, per shard and symbol."""
    opened: dict[tuple, dict] = {}
    pairs = []
    for ev in events:
        key = (ev.get("shard"), ev.get("symbol"))
        if ev["type"] == "pipeline":
            opened.setdefault(key, ev)  # the order that opened the position; later adds/trims don't replace it
        elif ev["type"] == "closed":
            trail = opened.pop(key, None)
            if trail is not None:
                pairs.append((trail, ev))
    return pairs


def department(trail: dict) -> str:
    board = trail.get("board") or {}
    return DEPARTMENTS.get(str(board.get("active_style", "")).upper(), "desk")


def prompt(trail: dict) -> str:
    board = trail.get("board") or {}
    st = trail.get("stages") or {}
    parts = [f"Symbol {trail.get('symbol')} at {trail.get('price')}. Desk score {trail.get('score')}."]
    if board:
        parts.append(f"Sage: regime {board.get('regime_label')} (efficiency {board.get('regime_efficiency')}), "
                     f"news lean {board.get('news_lean')}, whale bias {board.get('whale_bias')}. "
                     f"Sentinel: stop {board.get('suggested_stop_r')}R, target {board.get('suggested_target_r')}R, "
                     f"size x{board.get('risk_multiplier')}. Atlas: {board.get('active_style')} with "
                     f"{board.get('participating_agents')} agents, consensus {board.get('cluster_consensus')}.")
    parts.append(f"Orbit {st.get('orbit')} at {st.get('heat')}x heat; {st.get('night')} session.")
    return " ".join(parts)


def action(trail: dict) -> str:
    side = "BUY" if (trail.get("qty") or 0) > 0 else "SELL"
    board = trail.get("board") or {}
    style = str(board.get("active_style") or "desk vote").lower()
    return f"{side}. {style} setup, score {trail.get('score')}, all nine checks passed."


def mistake_tag(trail: dict, closed: dict) -> str:
    if closed.get("won"):
        return "win"
    board = trail.get("board") or {}
    style = str(board.get("active_style", "")).upper()
    if style in ("TREND", "BREAKOUT") and board.get("regime_label") == "CHOP":
        return "chop_entry"
    if abs(float(trail.get("score") or 0)) < LOW_CONVICTION:
        return "low_conviction"
    if float(board.get("suggested_stop_r") or 0) >= 1.3:
        return "wide_stop"
    return "loss"


def build(events: list[dict]) -> dict[str, dict]:
    out: dict[str, dict] = defaultdict(lambda: {"sft": [], "dpo": [], "wins": 0, "trades": 0, "tags": Counter()})
    for trail, closed in pair_trades(events):
        if not verified(trail):
            continue
        d = out[department(trail)]
        p, act, tag = prompt(trail), action(trail), mistake_tag(trail, closed)
        d["trades"] += 1
        d["tags"][tag] += 1
        if closed.get("won"):
            d["wins"] += 1
            d["sft"].append({"prompt": p, "completion": act, "pnl": closed.get("pnl")})
            d["dpo"].append({"prompt": p, "chosen": act, "rejected": "STAND ASIDE.", "tag": tag})
        else:
            d["dpo"].append({"prompt": p, "chosen": f"STAND ASIDE. Skip it: {tag.replace('_', ' ')}.",
                             "rejected": act, "tag": tag})
    return dict(out)


def modelfile(dept: str, base: str = "llama3.1:8b") -> str:
    return f'FROM {base}\nPARAMETER temperature 0.2\nSYSTEM """{SYSTEM[dept]}"""\n'


def write(built: dict[str, dict], out_dir: Path) -> dict:
    out_dir = Path(out_dir)
    summary = {}
    for dept, d in built.items():
        folder = out_dir / dept
        folder.mkdir(parents=True, exist_ok=True)
        for name in ("sft", "dpo"):
            (folder / f"{name}.jsonl").write_text("".join(json.dumps(r) + "\n" for r in d[name]), encoding="utf-8")
        (folder / "Modelfile").write_text(modelfile(dept), encoding="utf-8")
        summary[dept] = {"verified_trades": d["trades"], "win_rate": round(d["wins"] / d["trades"], 3) if d["trades"] else None,
                         "sft_rows": len(d["sft"]), "dpo_pairs": len(d["dpo"]), "tags": dict(d["tags"])}
    (out_dir).mkdir(parents=True, exist_ok=True)
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("events", nargs="+")
    ap.add_argument("--out", default="brains")
    a = ap.parse_args(argv)
    print(json.dumps(write(build(read_events(a.events)), Path(a.out)), indent=2))


if __name__ == "__main__":
    main()
