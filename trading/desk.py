"""The coordinator: turns hundreds of agent votes into one risk-checked order per symbol."""

from collections import defaultdict, deque

from . import academy, accounts, coach as coaching, event_guard, hooks, review, sessions
from . import mind as mindmod
from . import night_shift

from .agents import REVERSION, Agent, Trader
from .brains import BrainAlpha, BrainBeta
from .broker import PaperBroker
from .events import EventLog
from .managers import Managers
from .org import manager_reports, summarize
from .risk import RiskManager

SIGNAL_THRESHOLD = 0.5  # tuned on real 2024-25 hourly prices; weaker calls cost more than they earned
MIN_TRADE = 5000.0
PROVEN_MIN = 3  # proven agents needed on a symbol before their view counts
LEADERBOARD_EVERY = 20
ORG_EVERY = 5  # how often the managers report their departments
MACRO_WEIGHT = 0.3  # how far the analysts' read of the news tilts the desk's call
LATEST_NEWS = 8
# Practice-quota trades (20 an hour) are forced coin flips that win about 47%. Letting them steer the desk dragged
# its own closed trades down to a 25-45% win rate on real BTC/ETH/SOL/gold bars; leaving them out lifted it to
# 52-70% (hourly 2 months, 15m 2 weeks) with more profit. This is a hard wall: no setting turns it off.
# How much the desk listens to Trend (env TREND_WEIGHT, 0 = Reversion only). Trend's chosen trades lost on years
# of real data in most markets (forex 28-41%, gold 32-41%, indices 42-45%, shares 43-46%), but turning it down
# cut profit in some periods, so it stays at 1 until tested further.
TREND_WEIGHT = 1.0
# Regime router (env REGIME_ROUTER, on by default): per symbol, the desk measures trend strength (efficiency ratio over
# ROUTER_FAST bars, an ADX stand-in) and volatility expansion (average move over ROUTER_FAST bars vs ROUTER_SLOW).
# Breakout (strong trend and expanding volatility): Trend x1.25, Reversion x0.4. Chop (weak trend, quiet): Trend
# muted, Reversion x1.2. In between: Trend x0.5.
REGIME_ROUTER = True
ROUTER_FAST, ROUTER_SLOW = 14, 50
ROUTER_TREND, ROUTER_CHOP, ROUTER_EXPAND = 0.35, 0.2, 1.15
ROUTER_WEIGHTS = {"expansion": (1.25, 0.4), "chop": (0.0, 1.2), "transition": (0.5, 1.0)}
# Cost hurdle (env COST_HURDLE: "auto" by default, a fixed multiple, or 0 = off): no new desk exposure unless the
# expected move over HURDLE_BARS bars (average one-bar move x sqrt(bars)) is at least N x the round-trip fee.
# "auto" picks N from the bar length: faster bars need a bigger cushion over costs.
COST_HURDLE: float | None = None  # None = auto
HURDLE_BARS = 10
FRICTION_HURDLES = ((60, 5.0), (300, 4.0), (900, 2.5), (3600, 1.8), (4 * 3600, 1.5))  # (bar seconds up to, N)
FRICTION_SLOW = 1.5  # daily and slower bars
FRICTION_SIM = 2.0  # the simulator has no clock
# Analysts' trend gate (env TREND_GATE, default 0.25; "off" disables): when the analysts' regime vote is below it,
# the desk doesn't listen to Trend at all on that symbol and Reversion drives.
TREND_GATE: float | None = 0.25
MIN_PRACTICE_TRADES = 20  # every strategy trader makes at least this many practice trades per hour


class Desk:
    def __init__(self, agents: list[Agent], broker: PaperBroker, risk: RiskManager, log: EventLog,
                 beta: BrainBeta | None = None, alpha: BrainAlpha | None = None, hour_ticks: int | None = None,
                 room=None, reviews: bool = True, coach=None):
        self.agents = agents
        self.broker = broker
        self.risk = risk
        self.log = log
        self.beta = beta
        # Brain Alpha learns how far to trust each trader style ("trend" / "reversion").
        self.alpha = alpha
        self.signal_threshold = SIGNAL_THRESHOLD
        self.min_trade = MIN_TRADE  # dollars: smaller rebalances aren't worth their cost
        # Proven agents (trading/accounts.py): their own view per symbol, and whether only they steer the desk.
        self.use_clock = True  # read the market clock (trading/sessions.py) when the market has real time
        self.clock = None
        self.now: float | None = None
        self.session_avg = sessions.SessionAverages()
        self.proven_only = False
        self.proven_min = PROVEN_MIN
        self.proven_view: dict[str, tuple[float, int]] = {}
        self.on_order = None  # called with (symbol, qty, price, why) after each paper fill (real-money mirror)
        self.ticks = 0
        self.news_view: dict[str, float] = {}  # per symbol, last tick's analysts' news read
        self.last_prices: dict[str, float] = {}
        self.symbols = sorted({a.symbol for a in agents})
        self.news: deque[dict] = deque(maxlen=LATEST_NEWS)
        # A practice hour, in ticks: the quota of practice trades and the hourly upgrade run on it.
        self.hour_ticks = hour_ticks
        self.hours = 0
        self.last_hour: dict | None = None
        # The reading room (trading/reading_room.py): one news + chart view per symbol that traders confirm with.
        self.room = room
        # Every trader reviews its own trades four times a practice hour (every 15 minutes) ...
        self.review_ticks = max(1, hour_ticks // 4) if hour_ticks and reviews else None
        self.reviews = 0
        self.last_review: dict | None = None
        # ... and once a practice day the weakest copy the best.
        self.days = 0
        self.last_share: dict | None = None
        # What Claude's agent program adds (see README "Built like Claude's agent program"):
        self.journal = hooks.DeskJournal()  # the desk's own closed trades per symbol
        self.managers = Managers()  # Atlas, Sage and Sentinel act once an hour
        self.coach = coach  # the Claude coach (trading/coach.py), or None
        self.last_coach: dict | None = None
        self.coach_trial: dict | None = None  # the coach's last changes, checked at the end of the next hour
        self.last_report: dict | None = None
        self.memory: dict | None = None  # set by the engine: {"remembered": n, "saved_tick": t}
        self.checks = {"blocked": 0, "shrunk": 0}  # pre-order checks (trading/hooks.py)
        self.hour_start = 0
        self.hour_stats: dict[str, dict] = {}
        self.price_hist: dict[str, deque] = defaultdict(lambda: deque(maxlen=ROUTER_SLOW + 1))
        self.regime: dict[str, str] = {}
        self.bar_seconds: float | None = None  # time between market ticks, for the cost hurdle
        self.calendar = None  # event_guard.Calendar (live markets): earnings + big economic news
        self.checker = None  # trade_checker.TradeChecker: Sentinel's written PASS/FAIL + audit log

    def step(self, prices: dict[str, float], headlines: list[dict] | None = None, now: float | None = None) -> None:
        """One tick. `now` is the market time (unix seconds) for live prices and backtests; None in the simulator."""
        self.ticks += 1
        if now is not None and self.now is not None and now > self.now:
            self.bar_seconds = now - self.now
        self.now = now
        self.clock = sessions.clock(now) if self.use_clock else None
        if self.clock is not None:
            self.session_avg.update(self.clock, prices)
        for headline in headlines or ():
            self.news.appendleft(headline)
        self.last_prices = dict(prices)
        for symbol, price in prices.items():
            self.price_hist[symbol].append(price)
        if self.beta:
            self.beta.begin()
        views = self.room.update(prices, headlines) if self.room else {}
        votes = defaultdict(lambda: {"trend": [], "reversion": [], "analyst": [], "risk": [], "macro": []})
        agent_votes: dict[str, float] = {}
        proven = defaultdict(list)
        self._set_practice_pace()
        by_symbol = defaultdict(list)
        for headline in headlines or ():
            by_symbol[headline["symbol"]].append(headline)
        for agent in self.agents:
            if agent.symbol not in prices:
                continue
            if agent.role == "analyst":
                agent.forget_news()
                for headline in by_symbol.get(agent.symbol, ()):
                    agent.hear(headline)
                if agent.macro_view is not None:
                    votes[agent.symbol]["macro"].append(agent.macro_view)
            agent.clock = self.clock
            if agent.role == "trader":
                agent.news_view = self.news_view.get(agent.symbol)
                agent.view = views.get(agent.symbol)
            vote = agent.observe(prices[agent.symbol])
            if isinstance(agent, Trader) and (agent.must_practice or (vote is not None and vote.practice)):
                continue  # hard wall: a practice-quota trade is the agent's homework, never a desk order
            if vote is None or agent.agent_id in self.managers.benched:
                continue  # benched by its manager: it keeps practising, but its vote doesn't count this hour
            agent_votes[agent.agent_id] = vote.value
            if isinstance(agent, Trader):
                votes[agent.symbol]["reversion" if agent.style == REVERSION else "trend"].append(vote.value)
                if accounts.proven(agent.account):
                    proven[agent.symbol].append(vote.value)
            else:
                votes[agent.symbol][vote.role].append(vote.value)

        self.news_view = {s: sum(v["macro"]) / len(v["macro"]) for s, v in votes.items() if v["macro"]}
        self.proven_view = {s: (sum(v) / len(v), len(v)) for s, v in proven.items()}
        risk_votes = [v for sym in votes.values() for v in sym["risk"]]
        exposure = sum(risk_votes) / len(risk_votes) if risk_votes else 1.0
        self.risk.update(prices, now=self.now)

        orders = 0
        for symbol in self.symbols:
            if symbol not in votes:
                continue
            score = self._score(votes[symbol], symbol) * exposure
            if self.proven_only:
                # Only agents with a proven record steer: no trade on a symbol without enough of them.
                view, n = self.proven_view.get(symbol, (0.0, 0))
                score = view * exposure if n >= self.proven_min else 0.0
            if abs(score) < self.signal_threshold:
                score = 0.0
            if self.risk.killed:
                score = 0.0  # kill switch: close everything
            target_value = score * self.risk.limits.max_position_value
            desired = target_value / prices[symbol] - self.broker.position(symbol)
            if self._hurdle() and abs(target_value) > abs(self.broker.position(symbol) * prices[symbol]) \
                    and not self.risk.killed and not self._clears_costs(symbol):
                self.checks["blocked"] += 1
                continue  # not enough movement to pay the round trip: no new exposure (trims and exits still go)
            if abs(desired * prices[symbol]) < self.min_trade and not (self.risk.killed and self.broker.position(symbol)):
                continue  # ignore tiny rebalances
            check = hooks.OrderCheck(symbol, desired, self.broker.position(symbol), prices[symbol],
                                     view=views.get(symbol), paused=symbol in self.managers.paused,
                                     streak=self.journal.streak(symbol), clock=self.clock,
                                     cooling=self.journal.cooling(symbol, self.ticks, self.now, max(1, (self.hour_ticks or 7200) // 12)),
                                     session_avg=self.session_avg.get(symbol) if self.clock else None,
                                     events=self.calendar.snapshot() if self.calendar and self.now else None,
                                     now=self.now)
            desired, notes = hooks.run_hooks(check)
            if notes:
                self.checks["blocked" if abs(desired * prices[symbol]) < self.min_trade else "shrunk"] += 1
                self.log.emit("check", symbol=symbol, wanted=round(check.qty, 4), allowed=round(desired, 4),
                              notes=notes)
            if abs(desired * prices[symbol]) < self.min_trade and not (self.risk.killed and desired):
                continue
            if self.checker and not self.risk.killed:
                after = check.position + desired
                if abs(after) > abs(check.position) or (after and check.position and (after > 0) != (check.position > 0)):
                    ev_ok, ev_reason = True, "PASS"
                    if check.events and self.now is not None:
                        from datetime import datetime, timezone
                        ev_ok, _, ev_reason = event_guard.check(symbol, check.events,
                                                                datetime.fromtimestamp(self.now, timezone.utc))
                    view = views.get(symbol)
                    rec = self.checker.review(
                        symbol=symbol, qty=desired, price=prices[symbol], desk_score=score,
                        threshold=self.signal_threshold, event_ok=ev_ok, event_reason=ev_reason,
                        costs_ok=self._clears_costs(symbol) if self._hurdle() else None,
                        room_direction=getattr(view, "direction", None),
                        room_confident=bool(getattr(view, "confident", False)), tick=self.ticks, now=self.now)
                    if not rec.passed:
                        self.checks["blocked"] += 1
                        self.log.emit("checker", symbol=symbol, wanted=round(desired, 4), verdict=rec.verdict)
                        continue
            qty, reason = self.risk.approve(symbol, desired, prices, audit=night_shift.audit_from_prices(
                symbol, self.price_hist[symbol], self.broker.fee_bps / 10_000) if self.risk.night_active else None)
            if reason:
                self.log.emit("risk", symbol=symbol, wanted=round(desired, 4), allowed=round(qty, 4), reason=reason)
            if qty and self.beta:
                decision = self.beta.jev.validate(self._proposal(symbol, qty, prices[symbol]))
                if not decision.approved:
                    self.log.emit("jev", symbol=symbol, decision="rejected", codes=[decision.code], legs=1)
                    continue
            if qty:
                before = self.broker.position(symbol)
                self.broker.fill(symbol, qty, prices[symbol])
                closed = self.journal.fill(symbol, before, qty, prices[symbol], self.ticks, self.now)
                if closed:
                    # One closed round trip, for the Trades page: what it was, and did it make or lose money.
                    self.log.emit("closed", symbol=symbol, side=closed["side"], qty=round(closed["qty"], 4),
                                  entry=round(closed["entry"], 4), exit=round(closed["exit"], 4),
                                  move=round(closed["move"], 5), won=closed["won"],
                                  pnl=round(closed["qty"] * closed["entry"] * closed["move"], 2))
                    if self.risk.night_active:  # the overnight capsule (trading/night_shift.py)
                        self.risk.night_shift.record_closed_trade(
                            symbol, closed["qty"] * closed["entry"] * closed["move"], closed["move"])
                orders += 1
                why = hooks.why(votes[symbol], views.get(symbol), exposure)
                self.log.emit("order", symbol=symbol, qty=round(qty, 4), price=round(prices[symbol], 4),
                              score=round(score, 3), why=why, checks=notes)
                if self.on_order:
                    self.on_order(symbol, qty, prices[symbol], why)

        advice = self.coach.take_advice() if self.coach else None
        if advice:
            changed = coaching.apply(self.agents, advice)
            self.last_coach = {"hour": self.hours, "summary": str(advice.get("summary", ""))[:300],
                               "changed": changed, "looked_at": advice.get("looked_at", [])[:10]}
            # On trial: remember each department's win rate the advice is meant to beat.
            self.coach_trial = {d: {"rate": (self.last_report or {}).get("departments", {}).get(d, {}).get(
                "win_rate"), "changes": c["changes"]} for d, c in changed.items()}
            self.log.emit("coach", tick=self.ticks, **self.last_coach)

        if self.alpha and self.alpha.due():
            weights = self.alpha.learn(self.style_pnl())
            self.log.emit("brain", brain="alpha", update=self.alpha.updates,
                          weights={k: round(v, 3) for k, v in weights.items()})

        equity = self.broker.equity(prices)
        extra = {}
        if self.beta:
            loop = self.beta.end()
            extra = {"loop_ms": round(loop * 1000, 2), "loop_overruns": self.beta.overruns,
                     "jev": {"approved": self.beta.jev.counts["approved"], "rejected": self.beta.jev.counts["rejected"]}}
        self.log.emit(
            "tick", tick=self.ticks, agents=len(self.agents), orders=orders, exposure_vote=round(exposure, 3),
            equity=round(equity, 2), pnl=round(equity - self.broker.starting_cash, 2), halted=self.risk.halted,
            proven=self.proven_count(), clock=self.clock.snapshot() if self.clock else None,
            stopped=self.risk.stop_reason, stale=sorted(self.risk.frozen), killed=self.risk.killed,
            risk=self._risk_gauges(prices, equity),
            prices={s: round(p, 4) for s, p in prices.items()}, **extra,
        )
        if self.review_ticks and self.ticks % self.review_ticks == 0:
            self._review()
        if self.hour_ticks and self.ticks % self.hour_ticks == 0:
            self._end_hour()
            if self.review_ticks and self.hours % 24 == 0:
                self.days += 1
                self.last_share = {"day": self.days, "copied": review.share_best(self.agents)}
                self.log.emit("share", tick=self.ticks, **self.last_share)
        if self.ticks % ORG_EVERY == 0 or self.ticks == 1:
            departments = summarize(self.agents, agent_votes)
            self.log.emit("org", tick=self.ticks, departments=departments, managers=manager_reports(departments),
                          news=list(self.news), practice=self.practice_status(),
                          reading_room=self.room.snapshot() if self.room else None)
        if self.ticks % LEADERBOARD_EVERY == 0:
            self.log.emit("leaderboard", tick=self.ticks, top=self.leaderboard(5))

    def _set_practice_pace(self) -> None:
        """Flag every trader that is behind the pace for MIN_PRACTICE_TRADES this hour."""
        if not self.hour_ticks:
            return
        into = (self.ticks - 1) % self.hour_ticks + 1
        need = MIN_PRACTICE_TRADES * into / self.hour_ticks
        for agent in self.agents:
            if agent.role == "trader":
                agent.must_practice = agent.workspace["trades_this_hour"] < need

    def _end_hour(self) -> None:
        """Close the practice hour: report trades per department, then upgrade every agent."""
        self.hours += 1
        trades: dict[str, list[int]] = defaultdict(list)
        scores: dict[str, list[tuple[float, float]]] = defaultdict(list)
        back_in = 0
        for agent in self.agents:
            if agent.role == "trader":
                trades[agent.department].append(agent.workspace["trades_this_hour"])
                agent.workspace["trades_this_hour"] = 0
            benched = agent.brain.sitting_out
            result = academy.upgrade(agent, self.hours)
            if result:
                scores[agent.department].append(result)
                back_in += benched and not agent.brain.sitting_out
        self.last_hour = {
            "hour": self.hours,
            "trades": {k: {"min": min(v), "avg": round(sum(v) / len(v), 1),
                           "under_quota": sum(t < MIN_PRACTICE_TRADES for t in v)} for k, v in trades.items()},
            "upgrade": {k: {"exam_before": round(sum(b for b, _ in v) / len(v), 4),
                            "exam_after": round(sum(a for _, a in v) / len(v), 4)} for k, v in scores.items()},
            "back_in": back_in,
        }
        self.log.emit("hour", tick=self.ticks, min_trades=MIN_PRACTICE_TRADES, **self.last_hour)
        acted = self.managers.hour(self.agents, self.journal, self.hour_start, self.hours)
        self.log.emit("managers", tick=self.ticks, **acted)
        report = self.coach_report()
        trial = self.check_coach_trial(report)
        if trial:
            report["your_last_advice_result"] = trial
            self.log.emit("coach_trial", tick=self.ticks, results=trial)
        self.last_report = report
        if self.coach:
            self.coach.request(report, coaching.details(self))
        self.hour_start = self.ticks
        self.hour_stats = {}
        self.checks = {"blocked": 0, "shrunk": 0}

    def check_coach_trial(self, report: dict) -> dict:
        """Keep or undo the coach's last changes, department by department, by this hour's win rate."""
        results = {}
        for dept, t in (self.coach_trial or {}).items():
            before, now = t["rate"], report["departments"].get(dept, {}).get("win_rate")
            if before is None or now is None:
                continue
            if now < before - coaching.TRIAL_MARGIN:
                for a in self.agents:
                    if a.role == "trader" and a.department == dept:
                        for rule, x in t["changes"].items():
                            lo, hi = coaching.LIMITS[rule]
                            a.rules[rule] = round(max(lo, min(hi, a.rules[rule] - x)), 3)
                results[dept] = {"kept": False, "win_rate_before": before, "win_rate_after": now}
            else:
                results[dept] = {"kept": True, "win_rate_before": before, "win_rate_after": now}
        self.coach_trial = None
        if results and self.last_coach is not None:
            self.last_coach["trial"] = results
        return results

    def coach_report(self) -> dict:
        """What the Claude coach reads once an hour: how each trading department's trades went, and why."""
        departments = {}
        for dept, st in self.hour_stats.items():
            traders = [a for a in self.agents if a.role == "trader" and a.department == dept]
            losers = st["trades"] - st["won"]
            departments[dept] = {
                "traders": len(traders), "closed_trades": st["trades"],
                "win_rate": round(st["won"] / st["trades"], 3) if st["trades"] else None,
                "losers_against_view": st["against_lost"], "losers_on_weak_signal": st["weak_lost"],
                "avg_win_move": round(st["win_move"] / st["won"], 5) if st["won"] else None,
                "avg_loss_move": round(st["loss_move"] / losers, 5) if losers else None,
                "avg_rules": {k: round(sum(a.rules[k] for a in traders) / len(traders), 3)
                              for k in coaching.FIELDS} if traders else {}}
        return {"hour": self.hours, "departments": departments, "desk_trades": self.journal.summary(self.hour_start),
                "order_checks": dict(self.checks), "managers": self.managers.last,
                "your_last_advice": self.last_coach}

    def _review(self) -> None:
        """Every trader reviews the trades it closed since its last review and learns from its losers."""
        self.reviews += 1
        changed: dict[str, int] = defaultdict(int)
        examples: list[dict] = []
        kept = undone = 0
        for agent in self.agents:
            if agent.role != "trader":
                continue
            st = self.hour_stats.setdefault(agent.department, {"trades": 0, "won": 0, "against_lost": 0,
                                                                "weak_lost": 0, "win_move": 0.0, "loss_move": 0.0})
            for t in agent.journal:
                if t.get("forced"):
                    continue
                st["trades"] += 1
                if t["won"]:
                    st["won"] += 1
                    st["win_move"] += t["move"]
                else:
                    st["loss_move"] += t["move"]
                    st["against_lost"] += t.get("agree", 0.0) <= 0
                    st["weak_lost"] += t.get("edge", 0.0) < 0.3
            k0, u0 = agent.workspace.get("kept", 0), agent.workspace.get("undone", 0)
            lessons = review.review(agent, self.reviews)
            kept += agent.workspace.get("kept", 0) - k0
            undone += agent.workspace.get("undone", 0) - u0
            if lessons:
                changed[agent.department] += 1
                if len(examples) < 3:
                    examples.append({"agent": agent.agent_id, "lesson": lessons[0]})
        self.last_review = {"review": self.reviews, "learned": dict(changed), "examples": examples,
                            "changes_kept": kept, "changes_undone": undone}
        self.log.emit("review", tick=self.ticks, **self.last_review)

    def practice_status(self) -> dict | None:
        if not self.hour_ticks:
            return None
        return {"hour": self.hours + 1, "ticks_into_hour": self.ticks % self.hour_ticks, "hour_ticks": self.hour_ticks,
                "min_trades": MIN_PRACTICE_TRADES, "last_hour": self.last_hour,
                "review_ticks": self.review_ticks, "last_review": self.last_review, "last_share": self.last_share,
                "managers": self.managers.last, "coach": self.last_coach,
                "coach_status": self.coach.status() if self.coach else {"claude": False},
                "checks": dict(self.checks), "desk_trades": self.journal.closed, "memory": self.memory,
                "minds": self.minds()}

    def _clears_costs(self, symbol: str) -> bool:
        p = list(self.price_hist[symbol])[-(ROUTER_FAST + 1):]
        if len(p) < 3:
            return False
        step = sum(abs(b / a - 1) for a, b in zip(p, p[1:])) / (len(p) - 1)
        return step * HURDLE_BARS ** 0.5 >= self._hurdle() * 2 * self.broker.fee_bps / 10_000

    def _hurdle(self) -> float:
        """How many round-trip fees the expected move must cover: COST_HURDLE, or by bar length when auto."""
        if COST_HURDLE is not None:
            return COST_HURDLE
        if self.bar_seconds is None:
            return FRICTION_SIM
        return next((n for top, n in FRICTION_HURDLES if self.bar_seconds <= top), FRICTION_SLOW)

    def regime_of(self, symbol: str) -> str:
        """expansion, chop or transition, from the symbol's own recent bars (the desk's ADX/ATR stand-in)."""
        p = list(self.price_hist.get(symbol, ()))
        if len(p) < ROUTER_SLOW + 1:
            return "transition"
        fast = p[-(ROUTER_FAST + 1):]
        path = sum(abs(b - a) for a, b in zip(fast, fast[1:]))
        er = abs(fast[-1] - fast[0]) / path if path else 0.0
        steps = [abs(b / a - 1) for a, b in zip(p, p[1:])]
        slow = sum(steps) / len(steps)
        ratio = (sum(steps[-ROUTER_FAST:]) / ROUTER_FAST) / slow if slow else 1.0
        if er >= ROUTER_TREND and ratio >= ROUTER_EXPAND:
            return "expansion"
        if er < ROUTER_CHOP and ratio < 1.0:
            return "chop"
        return "transition"

    def _score(self, v: dict, symbol: str | None = None) -> float:
        trend = sum(v["trend"]) / len(v["trend"]) if v["trend"] else 0.0
        reversion = sum(v["reversion"]) / len(v["reversion"]) if v["reversion"] else 0.0
        # Analysts decide whose advice to trust: trending regime favours
        # trend-followers, choppy regime favours mean-reversion traders.
        regime = sum(v["analyst"]) / len(v["analyst"]) if v["analyst"] else 0.0
        trend_weight = (1 + regime) / 2 * TREND_WEIGHT
        if TREND_GATE is not None and regime < TREND_GATE:
            trend_weight = 0.0
        if self.alpha:
            # Brain Alpha's learned style weights tilt the analysts' call.
            t = trend_weight * self.alpha.weights["trend"]
            r = (1 - trend_weight) * self.alpha.weights["reversion"]
            trend_weight = t / (t + r) if t + r else 0.5
        t_mult, r_mult = 1.0, 1.0
        if REGIME_ROUTER and symbol is not None:
            self.regime[symbol] = self.regime_of(symbol)
            t_mult, r_mult = ROUTER_WEIGHTS[self.regime[symbol]]
        score = trend_weight * t_mult * trend + (1 - trend_weight) * r_mult * reversion
        # Analysts trained in politics and economics add what the news says.
        macro = sum(v["macro"]) / len(v["macro"]) if v["macro"] else 0.0
        return max(-1.0, min(1.0, score + MACRO_WEIGHT * macro))

    def style_pnl(self) -> dict[str, float]:
        pnl = {"trend": 0.0, "reversion": 0.0}
        for a in self.agents:
            if isinstance(a, Trader):
                pnl["reversion" if a.style == REVERSION else "trend"] += a.pnl
        return pnl

    def _risk_gauges(self, prices: dict[str, float], equity: float) -> dict:
        """How close each hard limit is, for the dashboard's gauges."""
        r, lim = self.risk, self.risk.limits
        return {"day_pnl": round(equity - r.day_start_equity, 2), "day_start": round(r.day_start_equity, 2),
                "daily_limit": lim.max_daily_loss, "drawdown": round(1 - equity / r.peak_equity, 4) if r.peak_equity else 0,
                "max_drawdown": lim.max_drawdown, "gross": round(self.broker.gross_exposure(prices), 2),
                "gross_limit": lim.max_gross_exposure, "positions": sum(1 for q in self.broker.positions.values() if q)}

    @staticmethod
    def _proposal(symbol: str, qty: float, price: float) -> dict:
        notional = abs(qty) * price
        return {
            "bot": "analyst", "venue": "spot", "instrument": symbol, "side": "buy" if qty > 0 else "sell",
            "qty": abs(qty), "price": price, "strategy": "agent_consensus",
            # Liquid large-cap books: about 0.01% slippage per $2k traded.
            "expected_slippage_pct": notional / 200_000, "depth_usdc": 10_000_000.0,
        }

    def leaderboard(self, n: int) -> list[dict]:
        """The best practice accounts: proven agents first, then by money made after costs."""
        traders = sorted((a for a in self.agents if isinstance(a, Trader)),
                         key=lambda a: (accounts.proven(a.account), a.account.equity()), reverse=True)
        return [{"agent": a.agent_id, "symbol": a.symbol, "pnl": round(a.pnl, 4), "brain": a.brain.snapshot(),
                 "account": a.account.snapshot(), "mind": a.mind.snapshot() if a.mind else None}
                for a in traders[:n]]

    def minds(self) -> dict:
        """How the minds think and decide: department averages and the best trader's last thought and call."""
        out = {}
        for dept in ("trend", "reversion"):
            members = [a for a in self.agents if a.department == dept and getattr(a, "mind", None)]
            if not members:
                continue
            acc = [a.mind.accuracy for a in members if a.mind.accuracy is not None]
            trust: dict[str, float] = {}
            for a in members:
                for f, w in a.mind.weights.items():
                    trust[f] = trust.get(f, 0.0) + w / len(members)
            best = max(members, key=lambda a: (accounts.proven(a.account), a.account.equity()))
            out[dept] = {"agents": len(members), "guessed_right": round(sum(acc) / len(acc), 3) if acc else None,
                         "patience": round(sum(a.mind.patience for a in members) / len(members), 3),
                         "appetite": round(sum(a.mind.appetite for a in members) / len(members), 3),
                         "trusts": {mindmod.NAMES[f]: round(w, 2)
                                    for f, w in sorted(trust.items(), key=lambda kv: -abs(kv[1]))[:3]},
                         "example": {"agent": best.agent_id, "symbol": best.symbol, **best.mind.snapshot()}}
        return out

    def proven_count(self) -> int:
        return sum(1 for a in self.agents if isinstance(a, Trader) and accounts.proven(a.account))
