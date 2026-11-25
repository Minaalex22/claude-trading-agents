"""Master pipeline: every guard, in one fixed order, between the agents' score and the paper order.

mina's orchestrator (24 Nov 2026), matched to the real modules. The order:

  1. feed      stale or stuck prices and the NY-to-Asia rollover / Friday close     watchdog.py, ops_guard.py
  2. orbit     the symbol's Orbit arm and its 1h/4h timeframe floor                  orbit_portfolio.py
  3. events    earnings and high-impact macro windows (blocks, or half size)        event_guard.py
  4. parent    the 4h parent-trend lock (Scalpers may buy dips in a flat parent)    smt_filter.parent
  5. room      no new risk against a confident news + chart view                    hooks.against_the_room
  6. whale     never against a $3M+ 0DTE print at a key level; a boost with one      option_flows.FEED
  7. sizing    constant-R notional x arm heat x event size, under the $5k/$20k caps  ops_guard.py, risk.py
  8. night     Night Shift's overnight capsule and toxic-setup check                 night_shift.py
  9. checker   Sentinel's written PASS/FAIL, logged to desk_audit.jsonl             trade_checker.py

**The live desk already runs every one of these** (trading/desk.py, hooks.py, risk.py), in this order, once per
order. Running them a second time through a separate object would double-block and double-log, so the desk does
not call `evaluate_trade`; it uses this module to write one `pipeline` line per desk order with what each stage
said (arm, heat, constant-R cap, event size, checker verdict), so every order can be traced through all nine.

`evaluate_trade()` is the same nine stages as one call, for a single candidate order outside the desk loop (the
`/api/pipeline` check, tests, what-if questions). Paper only: nothing here can place an order.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

STAGES = ("feed", "orbit", "events", "parent", "room", "whale", "sizing", "night", "checker")
RISK_PCT = 0.0075  # same 1R as the desk (RiskLimits.risk_per_trade)


@dataclass
class CandidateOrderContext:
    symbol: str
    side: str  # "BUY" | "SELL"
    desk_score: float  # -1 .. 1, the agents' combined call
    entry_price: float
    stop_price: float
    timeframe: str = "1h"
    style: str = "reversion"  # which department leads: "reversion" (Scalper) or "trend" (Entry)
    prices: list = field(default_factory=list)  # recent closes on the child timeframe, oldest first
    bar_seconds: float | None = 3600.0
    last_price_at: float | None = None  # unix time of the last real price
    room_direction: float | None = None
    room_confident: bool = False
    threshold: float = 0.5
    qty: float = 0.0  # optional; derived from the notional when 0


class MasterDeskPipeline:
    def __init__(self, account_equity: float = 100_000.0, orbit=None, night=None, checker=None,
                 risk_pct: float = RISK_PCT, max_position: float = 20_000.0):
        from .night_shift import NightShiftEngine
        from .orbit_portfolio import OrbitPortfolioCoordinator
        self.orbit = orbit or OrbitPortfolioCoordinator(account_equity=account_equity)
        self.night = night or NightShiftEngine()
        self.checker = checker  # trade_checker.TradeChecker, or None (no written audit)
        self.equity = account_equity
        self.risk_pct = risk_pct
        self.max_position = max_position

    def evaluate_trade(self, ctx: CandidateOrderContext, events=None, now_utc: datetime | None = None) -> dict:
        from . import event_guard, hooks, ops_guard, option_flows, smt_filter, watchdog
        from .night_shift import TokenOrAssetAudit, asset_class

        dt = now_utc or datetime.now(timezone.utc)
        now = dt.timestamp()
        side = 1.0 if ctx.side.upper() == "BUY" else -1.0
        trace: list[dict] = []

        def stop(stage: str, reason: str) -> dict:
            trace.append({"stage": stage, "ok": False, "why": reason})
            return {"approved": False, "notional": 0.0, "stage": stage, "reason": reason, "trace": trace}

        def ok(stage: str, why: str = "PASS", **extra) -> None:
            trace.append({"stage": stage, "ok": True, "why": why, **extra})

        # 1. feed: an old price, then the rollover / Friday-close shield
        if ctx.last_price_at is not None and now - ctx.last_price_at > watchdog.max_age(ctx.bar_seconds):
            return stop("feed", f"stale price: last real price {(now - ctx.last_price_at) / 60:.0f} min old")
        why = ops_guard.rollover_blocked(ctx.symbol, dt)
        if why:
            return stop("feed", why)
        ok("feed")
        # 2. orbit arm + timeframe floor
        arm = self.orbit.route_arm_for_trade(ctx.symbol, ctx.timeframe)
        if arm is None:
            return stop("orbit", f"{ctx.symbol} {ctx.timeframe} is not in an Orbit arm (core symbols, 1h/4h only)")
        heat = arm.dynamic_heat_multiplier() if self.orbit.enabled else 1.0
        ok("orbit", arm.label, heat=round(heat, 3))
        # 3. events
        event_ok, size, event_reason = event_guard.check(ctx.symbol, events or [], dt)
        if not event_ok:
            return stop("events", event_reason)
        ok("events", event_reason, size=size)
        # 4. parent-timeframe lock
        if smt_filter.PARENT_LOCK and ctx.prices:
            why = smt_filter.parent(ctx.symbol, ctx.style, side, list(ctx.prices), ctx.bar_seconds)
            if why:
                return stop("parent", why)
        ok("parent")
        # 5. reading room
        view = None
        if ctx.room_direction is not None:
            view = type("View", (), {"direction": ctx.room_direction, "confident": ctx.room_confident})()
        scale, why = hooks.against_the_room(hooks.OrderCheck(ctx.symbol, side, 0.0, ctx.entry_price, view=view))
        if scale == 0.0:
            return stop("room", why)
        ok("room")
        # 6. whale flow: veto against, boost with
        why = option_flows.FEED.against(ctx.symbol, side, now)
        if why:
            return stop("whale", why)
        score = option_flows.FEED.boost(ctx.symbol, ctx.desk_score, now)
        ok("whale", "boosted" if score != ctx.desk_score else "PASS", score=round(score, 4))
        # 7. sizing: constant R x heat x event size, under the flat cap
        cap = ops_guard.compute_volatility_parity_notional(self.equity, ctx.entry_price, ctx.stop_price,
                                                           self.risk_pct, 1.0, self.max_position)
        notional = round(min(self.max_position, abs(score) * cap * heat * size), 2)
        if notional <= 0:
            return stop("sizing", "zero notional (no stop distance or no score)")
        ok("sizing", f"${notional:,.0f}", cap=cap)
        # 8. night shift
        if self.night.active:
            spread_pct = 0.0
            audit = TokenOrAssetAudit(ctx.symbol, asset_class(ctx.symbol), spread_pct,
                                      abs(ctx.entry_price - ctx.stop_price) / ctx.entry_price, 1.0)
            allowed, why = self.night.check_entry_allowed(audit, min(notional, self.night.position_cap()))
            if not allowed:
                return stop("night", why)
            notional = min(notional, self.night.position_cap())
        ok("night", "overnight capsule" if self.night.active else "day session")
        # 9. Sentinel's written check
        if self.checker is not None:
            rec = self.checker.review(symbol=ctx.symbol, qty=ctx.qty or side * notional / ctx.entry_price,
                                      price=ctx.entry_price, desk_score=score, threshold=ctx.threshold,
                                      event_ok=event_ok, event_reason=event_reason,
                                      room_direction=ctx.room_direction, room_confident=ctx.room_confident, now=now)
            if not rec.passed:
                return stop("checker", rec.verdict)
            ok("checker", rec.verdict)
        elif abs(score) < ctx.threshold:
            return stop("checker", f"desk score {abs(score):.2f} below the threshold {ctx.threshold:.2f}")
        else:
            ok("checker")
        return {"approved": True, "notional": notional, "arm": arm.label, "effective_score": round(score, 4),
                "stage": "done", "reason": "PASS: all nine stages cleared", "trace": trace}


def desk_trace(desk, symbol: str, qty: float, price: float, score: float, notes) -> dict:
    """What each stage said for one desk order (the desk ran the stages itself; this writes them down)."""
    arm = desk.orbit.route_arm_for_trade(symbol, "1h")
    from . import exits
    cap = (desk.risk.position_cap(price, exits.risk_unit(desk.price_hist[symbol]), desk.broker.equity(desk.last_prices))
           if desk.now is not None else desk.risk.limits.max_position_value)
    shrunk = [n for n in (notes or []) if isinstance(n, str)]
    return {"symbol": symbol, "qty": round(qty, 4), "price": round(price, 4), "score": round(score, 3),
            "stages": {"feed": "fresh" if symbol not in desk.risk.frozen else "stale (exit only)",
                       "orbit": arm.label if arm else "outside the orbit",
                       "heat": round(desk.orbit.heat(symbol), 3),
                       "events": next((n for n in shrunk if n.startswith("event_guard")), "PASS"),
                       "parent": "PASS", "room": next((n for n in shrunk if n.startswith("against_the_room")), "PASS"),
                       "whale": next((n for n in shrunk if n.startswith("against_whale")), "PASS"),
                       "sizing_cap": round(cap, 2),
                       "night": "capsule" if desk.risk.night_active else "day",
                       "checker": "PASS" if desk.checker else "off"},
            "board": desk.boards[symbol].snapshot() if symbol in getattr(desk, "boards", {}) else None}


def trail_text(ev: dict) -> str:
    """One desk order's nine-stage trail in plain words, for JARVIS."""
    st = ev.get("stages") or {}
    side = "bought" if (ev.get("qty") or 0) > 0 else "sold"
    return (f"{ev.get('symbol')}: {side} {abs(ev.get('qty') or 0):g} at {ev.get('price')} (score {ev.get('score')}). "
            f"1 feed {st.get('feed')}; 2 orbit {st.get('orbit')} at {st.get('heat')}x heat; 3 events {st.get('events')}; "
            f"4 parent {st.get('parent')}; 5 room {st.get('room')}; 6 whale {st.get('whale')}; "
            f"7 size cap ${st.get('sizing_cap') or 0:,.0f}; 8 night {st.get('night')}; 9 checker {st.get('checker')}. "
            "Every order's trail is on /audit.")
