"""Real-money order queue: the desk proposes, the owner approves, the bridge places.

The cloud app never holds broker credentials and never talks to the broker. Instead:

1. Shard 0 mirrors the desk's orders as small **proposals** (LIVE_MAX_ORDER_USD each, whole shares, symbols
   in LIVE_SYMBOLS only, buys and sells of what the real account holds: no shorting).
2. The owner approves or rejects each one on the dashboard. Approving needs a fresh authenticator code
   (two-step login must be on). A proposal not approved within PROPOSAL_TTL seconds expires: its price is old.
3. The bridge (trading/bridge.py), running next to moomoo's OpenD app on the owner's computer, collects
   approved orders over HTTPS with BRIDGE_TOKEN, places them, and reports the result back.

Every limit is checked here when an order is proposed and approved, and again by the bridge before it places.
State lives in live_orders.json in the events folder, so the dashboard and the shards share it.

**Audit trail.** Every step (proposed, approved, rejected, sent to the bridge, filled or failed) is also appended
to audit.jsonl and never rewritten. Each line carries the hash of the line before it, so a deleted or edited line
breaks the chain and `verify_audit()` says where. This is the governance record regulated firms keep for agent
decisions: who decided what, when, and with what limits in force.
"""

import hashlib
import json
import os
import secrets
import threading
import time
from pathlib import Path

PROPOSAL_TTL = 300.0
KEEP = 200  # finished orders kept for the record
_lock = threading.Lock()


class Limits:
    def __init__(self, env=os.environ):
        self.max_order = float(env.get("LIVE_MAX_ORDER_USD", 0) or 0)
        self.max_day = float(env.get("LIVE_MAX_DAY_USD", 0) or 0)
        self.symbols = {s.strip().upper() for s in env.get("LIVE_SYMBOLS", "").split(",") if s.strip()}
        # the owner's JARVIS rules: at most 2 positions open and 3 real orders a day
        self.max_positions = int(env.get("LIVE_MAX_POSITIONS", 2) or 2)
        self.max_orders_day = int(env.get("LIVE_MAX_ORDERS_DAY", 3) or 3)

    @property
    def on(self) -> bool:
        return self.max_order > 0 and self.max_day > 0 and bool(self.symbols)

    def as_dict(self) -> dict:
        return {"max_order_usd": self.max_order, "max_day_usd": self.max_day, "symbols": sorted(self.symbols),
                "max_positions": self.max_positions, "max_orders_day": self.max_orders_day, "on": self.on}


def _path(events_dir: Path) -> Path:
    return Path(events_dir) / "live_orders.json"


def load(events_dir: Path) -> dict:
    try:
        data = json.loads(_path(events_dir).read_text())
        if isinstance(data, dict) and isinstance(data.get("orders"), list):
            data.setdefault("positions", {})
            return data
    except (OSError, ValueError):
        pass
    return {"orders": [], "positions": {}}


def _save(events_dir: Path, data: dict) -> None:
    path = _path(events_dir)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data))
    os.replace(tmp, path)


def _audit_path(events_dir: Path) -> Path:
    return Path(events_dir) / "audit.jsonl"


def _last_hash(path: Path) -> str:
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 4096))
            lines = f.read().splitlines()
        return json.loads(lines[-1])["hash"] if lines else "genesis"
    except (OSError, ValueError, KeyError, IndexError):
        return "genesis"


def audit(events_dir: Path, step: str, order: dict, clock=time.time, **extra) -> None:
    """Append one hash-chained line. Call with the module lock held, so lines never interleave."""
    path = _audit_path(events_dir)
    entry = {"at": round(clock(), 3), "step": step, "order": order["id"], "symbol": order["symbol"],
             "side": order["side"], "qty": order["qty"], "price": order["price"], **extra, "prev": _last_hash(path)}
    entry["hash"] = hashlib.sha256(json.dumps(entry, sort_keys=True).encode()).hexdigest()
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, sort_keys=True) + "\n")


def verify_audit(events_dir: Path) -> dict:
    """Check the chain: {"ok": True, "entries": n} or where it breaks."""
    prev, n = "genesis", 0
    try:
        lines = _audit_path(events_dir).read_text(encoding="utf-8").splitlines()
    except OSError:
        return {"ok": True, "entries": 0}
    for i, line in enumerate(lines, 1):
        try:
            e = json.loads(line)
            body = {k: v for k, v in e.items() if k != "hash"}
            good = e["prev"] == prev and e["hash"] == hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
        except (ValueError, KeyError, TypeError):
            good = False
        if not good:
            return {"ok": False, "entries": n, "broken_at_line": i}
        prev, n = e["hash"], n + 1
    return {"ok": True, "entries": n}


def _day(t: float) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(t))


def spent_today(data: dict, now: float) -> float:
    today = _day(now)
    return sum(o["qty"] * o["price"] for o in data["orders"]
               if o["status"] in ("approved", "sent", "filled") and _day(o.get("approved_at") or 0) == today)


def orders_today(data: dict, now: float) -> int:
    today = _day(now)
    return sum(1 for o in data["orders"]
               if o["status"] in ("approved", "sent", "filled") and _day(o.get("approved_at") or 0) == today)


def open_positions(data: dict) -> set:
    """Symbols held, plus symbols with a buy on its way: what counts against LIVE_MAX_POSITIONS."""
    held = {s for s, q in data["positions"].items() if int(q) > 0}
    return held | {o["symbol"] for o in data["orders"]
                   if o["side"] == "buy" and o["status"] in ("proposed", "approved", "sent")}


def _expire(data: dict, now: float) -> None:
    for o in data["orders"]:
        if o["status"] == "proposed" and now - o["proposed_at"] > PROPOSAL_TTL:
            o["status"] = "expired"
    done = [o for o in data["orders"] if o["status"] not in ("proposed", "approved", "sent")]
    if len(done) > KEEP:
        drop = {id(o) for o in done[:-KEEP]}
        data["orders"] = [o for o in data["orders"] if id(o) not in drop]


def propose(events_dir: Path, symbol: str, side: str, price: float, why: str, limits: Limits,
            clock=time.time) -> dict | None:
    """Turn one desk order into a capped real-money proposal, or None if it doesn't qualify."""
    symbol = symbol.upper()
    if not limits.on or symbol not in limits.symbols or side not in ("buy", "sell") or not price > 0:
        return None
    now = clock()
    with _lock:
        data = load(events_dir)
        _expire(data, now)
        if any(o["symbol"] == symbol and o["status"] in ("proposed", "approved", "sent") for o in data["orders"]):
            return None  # one open order per symbol
        held = int(data["positions"].get(symbol, 0))
        qty = int(limits.max_order // price) if side == "buy" else held
        if qty <= 0 or (side == "buy" and spent_today(data, now) + qty * price > limits.max_day):
            return None
        if orders_today(data, now) >= limits.max_orders_day:
            return None
        if side == "buy" and symbol not in open_positions(data) and len(open_positions(data)) >= limits.max_positions:
            return None
        order = {"id": secrets.token_hex(6), "symbol": symbol, "side": side, "qty": qty, "price": round(price, 4),
                 "why": str(why)[:300], "status": "proposed", "proposed_at": now}
        data["orders"].append(order)
        _save(events_dir, data)
        audit(events_dir, "proposed", order, clock, why=order["why"],
              limits={"max_order": limits.max_order, "max_day": limits.max_day})
        return order


def decide(events_dir: Path, order_id: str, approve: bool, limits: Limits, clock=time.time, by: str = "owner") -> dict:
    """The owner's call on a proposal. The caller has already checked the authenticator code."""
    now = clock()
    with _lock:
        data = load(events_dir)
        _expire(data, now)
        order = next((o for o in data["orders"] if o["id"] == order_id), None)
        if order is None or order["status"] != "proposed":
            raise ValueError("That order is no longer waiting for a decision (it may have expired).")
        if approve:
            if not limits.on or order["symbol"] not in limits.symbols or order["qty"] * order["price"] > limits.max_order:
                raise ValueError("That order is outside the current limits.")
            if order["side"] == "buy" and spent_today(data, now) + order["qty"] * order["price"] > limits.max_day:
                raise ValueError("Approving it would pass today's real-money limit.")
            if orders_today(data, now) >= limits.max_orders_day:
                raise ValueError(f"Already {limits.max_orders_day} real orders today (LIVE_MAX_ORDERS_DAY).")
        order["status"] = "approved" if approve else "rejected"
        order["approved_at" if approve else "rejected_at"] = now
        _save(events_dir, data)
        audit(events_dir, order["status"], order, clock, by=by)
        return order


def next_for_bridge(events_dir: Path, clock=time.time) -> dict | None:
    """Hand the bridge the oldest approved order and mark it sent, so it is placed once."""
    now = clock()
    with _lock:
        data = load(events_dir)
        order = next((o for o in data["orders"] if o["status"] == "approved"), None)
        if order is None:
            return None
        if now - order["approved_at"] > PROPOSAL_TTL:
            order["status"] = "expired"
            _save(events_dir, data)
            return None
        order["status"], order["sent_at"] = "sent", now
        _save(events_dir, data)
        audit(events_dir, "sent", order, clock)
        return dict(order)


def record_result(events_dir: Path, order_id: str, ok: bool, detail: str, filled_qty: int = 0,
                  fill_price: float | None = None, positions: dict | None = None, clock=time.time) -> dict:
    with _lock:
        data = load(events_dir)
        order = next((o for o in data["orders"] if o["id"] == order_id and o["status"] == "sent"), None)
        if order is None:
            raise ValueError("unknown order, or not sent")
        order["status"] = "filled" if ok else "failed"
        order.update({"detail": str(detail)[:300], "filled_qty": int(filled_qty), "fill_price": fill_price,
                      "done_at": clock()})
        if positions is not None:
            data["positions"] = {str(k).upper(): int(v) for k, v in positions.items()}
        _save(events_dir, data)
        audit(events_dir, order["status"], order, clock, detail=order["detail"], filled_qty=order["filled_qty"],
              fill_price=fill_price)
        return order


def snapshot(events_dir: Path, limits: Limits, clock=time.time) -> dict:
    now = clock()
    with _lock:
        data = load(events_dir)
        _expire(data, now)
    return {"limits": limits.as_dict(), "spent_today": round(spent_today(data, now), 2),
            "orders_today": orders_today(data, now),
            "positions": data["positions"], "orders": data["orders"][-50:][::-1], "audit": verify_audit(events_dir)}
