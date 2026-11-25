"""Sentinel's trade checker and the desk audit log (blueprint functions #6 and #11). PRACTICE ONLY.

Before any order that ADDS risk reaches the risk manager (trading/risk.py), Sentinel writes a PASS or FAIL
with the reasons, and every verdict goes into an append-only audit log (desk_audit.jsonl in the events folder),
so every decision the desk takes can be read back later on the Audit page. Orders that only cut risk are never
checked: getting out is always allowed.

The checks (each one uses the desk's own settings, nothing hard-coded twice):
- desk score at or above the desk's signal threshold (Desk.signal_threshold)
- event guard: no earnings or high-impact news window (trading/event_guard.py)
- cost hurdle: the expected move pays for the round trip (Desk._clears_costs), when the hurdle is on
- news and chart: no confident reading-room view against the trade
"""
from __future__ import annotations

import json
import os
import threading
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

MAX_LOG_BYTES = 5_000_000      # then desk_audit.jsonl rolls over to desk_audit.1.jsonl
FILE = "desk_audit.jsonl"


@dataclass
class AuditRecord:
    at: str
    shard: int
    tick: int
    symbol: str
    side: str
    qty: float
    price: float
    desk_score: float
    threshold: float
    event: str
    costs_ok: bool | None
    room: float | None
    passed: bool
    verdict: str
    board: dict | None = None  # the team relay's blackboard for this order (trading/team_relay.py)


class TradeChecker:
    def __init__(self, folder: str | Path | None, shard: int = 0):
        self.path = Path(folder) / FILE if folder else None
        self.shard = shard
        self.counts = {"pass": 0, "fail": 0}
        self.recent: list[dict] = []          # last 50, for tests and the in-process view
        self._lock = threading.Lock()

    def review(self, *, symbol: str, qty: float, price: float, desk_score: float, threshold: float,
               event_ok: bool = True, event_reason: str = "PASS", costs_ok: bool | None = None,
               room_direction: float | None = None, room_confident: bool = False, tick: int = 0,
               now: float | None = None, board: dict | None = None) -> AuditRecord:
        side = 1.0 if qty > 0 else -1.0
        reasons = []
        if abs(desk_score) < threshold:
            reasons.append(f"desk score {abs(desk_score):.2f} below the threshold {threshold:.2f}")
        if not event_ok:
            reasons.append(event_reason)
        if costs_ok is False:
            reasons.append("expected move doesn't pay for the round trip")
        if room_confident and room_direction is not None and side * room_direction < 0:
            reasons.append(f"news and chart point the other way ({room_direction:+.2f})")
        passed = not reasons
        when = datetime.fromtimestamp(now, timezone.utc) if now else datetime.now(timezone.utc)
        rec = AuditRecord(
            at=when.strftime("%Y-%m-%d %H:%M:%S UTC"), shard=self.shard, tick=tick, symbol=symbol.upper(),
            side="BUY" if side > 0 else "SELL", qty=round(qty, 6), price=round(price, 6),
            desk_score=round(desk_score, 4), threshold=round(threshold, 4), event=event_reason,
            costs_ok=costs_ok, room=None if room_direction is None else round(room_direction, 3), passed=passed,
            verdict="PASS: score, event, cost and news checks cleared" if passed else "FAIL: " + "; ".join(reasons),
            board=board)
        row = asdict(rec)
        with self._lock:
            self.counts["pass" if passed else "fail"] += 1
            self.recent = ([row] + self.recent)[:50]
            if self.path:
                try:
                    if self.path.exists() and self.path.stat().st_size > MAX_LOG_BYTES:
                        os.replace(self.path, self.path.with_name("desk_audit.1.jsonl"))
                    with self.path.open("a", encoding="utf-8") as f:
                        f.write(json.dumps(row) + "\n")
                except OSError:
                    pass   # the log must never stop the desk
        return rec


def read_log(folder: str | Path | None, limit: int = 200) -> dict:
    """The newest `limit` verdicts from the audit log, newest first, plus pass/fail counts for the file."""
    if not folder:
        return {"rows": [], "passed": 0, "failed": 0, "reasons": {}}
    path = Path(folder) / FILE
    try:
        with path.open("rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - 2_000_000))
            lines = f.read().decode("utf-8", "replace").splitlines()
    except OSError:
        lines = []
    rows = []
    for line in lines:
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    passed = sum(1 for r in rows if r.get("passed"))
    reasons: dict[str, int] = {}
    for r in rows:
        if not r.get("passed"):
            for part in str(r.get("verdict", ""))[6:].split("; "):
                key = part.split(":")[0].split(" in ")[0].split(" (")[0][:40]
                reasons[key] = reasons.get(key, 0) + 1
    return {"rows": rows[::-1][:limit], "passed": passed, "failed": len(rows) - passed, "reasons": reasons}
