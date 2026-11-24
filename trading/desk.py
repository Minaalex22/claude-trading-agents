"""The coordinator: turns hundreds of agent votes into one risk-checked order per symbol."""

import math
from collections import defaultdict, deque

from . import agents as agents_mod

from . import academy, accounts, coach as coaching, exits, hooks, review, sessions, smt_filter, sniper
from .meta_gate import MetaGate
from . import mind as mindmod

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
# Diverse consensus (env CONSENSUS): whisper votes (under WHISPER) are dropped, the rest are grouped by setup
# (strategy kind + market type) and each group counts sqrt(its size), so 200 clones of one idea can't outvote
# independent strategies agreeing; a single-setup call is cut to SINGLE_SETUP.
CONSENSUS = True
WHISPER = 0.35
SINGLE_SETUP = 0.72
# Pullback entries (env PULLBACK): new desk risk waits for a pullback of PULLBACK_ATR x the average bar move
# (0.15 on 15-minute bars) for PULLBACK_BARS bars; if the price runs away, the desk doesn't chase.
PULLBACK = True
PULLBACK_ATR, PULLBACK_ATR_15M, PULLBACK_BARS = 0.25, 0.15, 2
# Bucket sizing (env BUCKET_SIZING): each symbol's last 40 closed desk trades set its size: under 8 trades x0.75,
# expectancy at or under 0.05R x0.25, at least 0.35R with a 58%+ win rate x1.4, else x1. Risk limits still cap it.
BUCKET_SIZING = True
# News pipeline into the desk (env NEWS_TO_DESK, live feeds only): sure (>= 0.8) and two-source confirmed reads add
# PIPE_WEIGHT x their lean to the desk's score; an open human-queue read under 0.6 halves exposure in that symbol.
PIPE_CONF, PIPE_DOUBT, PIPE_WEIGHT, PIPE_BRAKE = 0.8, 0.6, 0.3, 0.5
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
        self.long_hist: dict[str, deque] = defaultdict(lambda: deque(maxlen=smt_filter.PARENT_X * 60 + 1))
        self.meta = MetaGate()  # walk-forward meta-label gate on desk entries (trading/meta_gate.py)
        self.regime: dict[str, str] = {}
        self.pipeline = None  # live feeds: a NewsPipeline whose sure, confirmed reads nudge the desk (engine)
        self.lead_style: dict[str, str] = {}
        self.pending: dict[str, dict] = {}  # pullback entries waiting for their price
        self.pending_cool: dict[str, int] = {}
        self.bucket: dict[str, deque] = defaultdict(lambda: deque(maxlen=40))  # closed desk trade moves per symbol
        self.tagged: dict[str, dict[str, list]] = {}
        self.bar_seconds: float | None = None  # time between market ticks, for the cost hurdle

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
            self.long_hist[symbol].append(price)
        exits.BAR_SECONDS = self.bar_seconds
        self.meta.update(prices)
        if self.beta:
            self.beta.begin()
        views = self.room.update(prices, headlines, now) if self.room else {}
        if self.pipeline is not None and headlines:
            self.pipeline.feed(headlines, {k: {"news": v.news, "chart": v.chart} for k, v in views.items()}, now)
        votes = defaultdict(lambda: {"trend": [], "reversion": [], "analyst": [], "risk": [], "macro": []})
        agent_votes: dict[str, float] = {}
        tagged = defaultdict(lambda: {"trend": [], "reversion": []})
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
                style = "reversion" if agent.style == REVERSION else "trend"
                votes[agent.symbol][style].append(vote.value)
                tag = f"{getattr(agent, 'kind', type(agent).__name__)}:{agent.workspace.get('dual_tag', '').split(':')[-1]}"
                tagged[agent.symbol][style].append((tag, vote.value))
                if accounts.proven(agent.account):
                    proven[agent.symbol].append(vote.value)
            else:
                votes[agent.symbol][vote.role].append(vote.value)

        self.tagged = tagged
        self.news_view = {s: sum(v["macro"]) / len(v["macro"]) for s, v in votes.items() if v["macro"]}
        self.proven_view = {s: (sum(v) / len(v), len(v)) for s, v in proven.items()}
        risk_votes = [v for sym in votes.values() for v in sym["risk"]]
        exposure = sum(risk_votes) / len(risk_votes) if risk_votes else 1.0
        self.risk.update(prices)

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
            if sniper.active(self.bar_seconds) and self.broker.position(symbol) * score <= 0 and abs(score) < sniper.BAR:
                score = 0.0  # sniper: opening (or flipping) needs 0.82; holding keeps the 0.5 bar
            if self.risk.killed:
                score = 0.0  # kill switch: close everything
            orders += self._work_pending(symbol, prices, score)
            if self.pipeline is not None and not self.risk.killed:
                score = self._pipeline_nudge(symbol, score)
            target_value = score * self.risk.limits.max_position_value * self._bucket_mult(symbol)
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
                                     style=self.lead_style.get(symbol, "") if agents_mod.NEWS_SPLIT else "")
            desired, notes = hooks.run_hooks(check)
            if notes:
                self.checks["blocked" if abs(desired * prices[symbol]) < self.min_trade else "shrunk"] += 1
                self.log.emit("check", symbol=symbol, wanted=round(check.qty, 4), allowed=round(desired, 4),
                              notes=notes)
            if abs(desired * prices[symbol]) < self.min_trade and not (self.risk.killed and desired):
                continue
            if not self.risk.killed and self._adds_risk(symbol, desired):
                why = self._entry_gate(symbol, desired, prices, score)
                if why:
                    self.checks["blocked"] += 1
                    self.log.emit("check", symbol=symbol, wanted=round(desired, 4), allowed=0.0, notes=[why])
                    continue
                desired *= self._meta_size
            qty, reason = self.risk.approve(symbol, desired, prices)
            if reason:
                self.log.emit("risk", symbol=symbol, wanted=round(desired, 4), allowed=round(qty, 4), reason=reason)
            if qty and PULLBACK and not self.risk.killed and self._adds_risk(symbol, qty):
                # Wait for a pullback instead of chasing the bar's close. One order at a time; after one expires
                # the desk waits as many bars again before it stages another (no chasing a runaway price).
                if symbol not in self.pending and self.ticks >= self.pending_cool.get(symbol, 0):
                    self._stage(symbol, qty, prices[symbol], hooks.why(votes[symbol], views.get(symbol), exposure))
                continue
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
                    self.bucket[symbol].append(closed["move"])
                if closed:
                    # One closed round trip, for the Trades page: what it was, and did it make or lose money.
                    self.log.emit("closed", symbol=symbol, side=closed["side"], qty=round(closed["qty"], 4),
                                  entry=round(closed["entry"], 4), exit=round(closed["exit"], 4),
                                  move=round(closed["move"], 5), won=closed["won"],
                                  pnl=round(closed["qty"] * closed["entry"] * closed["move"], 2))
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

    def _entry_gate(self, symbol: str, qty: float, prices: dict, score: float) -> str | None:
        """New desk risk: sister-pair SMT, the parent-timeframe lock, then the meta-label gate.
        Returns why it is blocked; sets self._meta_size (1, or SIZE_UP) when it passes."""
        self._meta_size = 1.0
        side = 1.0 if self.broker.position(symbol) + qty > 0 else -1.0
        style = self.lead_style.get(symbol, "trend")
        hist = {s: list(d) for s, d in self.long_hist.items()}
        mine = hist.get(symbol, [])
        if sniper.active(self.bar_seconds):
            from datetime import datetime, timezone
            hour = datetime.fromtimestamp(self.now, timezone.utc).hour if self.now is not None else None
            view = self.room.views.get(symbol) if self.room else None
            why = sniper.gate(symbol, style, side, score, hist, view, self.bar_seconds, hour)
            if why:
                return why
        smt_why = smt_filter.smt(symbol, style, side, hist)
        parent_why = smt_filter.parent(symbol, style, side, mine, self.bar_seconds)
        if not self.meta_on:
            return smt_why or parent_why
        x = self._meta_features(symbol, side, style, score, mine, smt_why, parent_why)
        if not any(e["symbol"] == symbol for e in self.meta.open):
            self.meta.note(symbol, side, prices[symbol], exits.risk_unit(mine[-(ROUTER_SLOW + 1):]), x)
        if smt_why or parent_why:
            return smt_why or parent_why
        size = self.meta.size(x)
        if not size:
            return f"blocked: meta-label gate, P(+{self.meta_win_r}R first) under {self.meta_veto}"
        self._meta_size = size
        return None

    meta_on = True
    meta_win_r, meta_veto = 1.2, 0.56

    def _meta_features(self, symbol, side, style, score, mine, smt_why, parent_why) -> list[float]:
        from datetime import datetime, timezone

        moves = [abs(b - a) for a, b in zip(mine, mine[1:])] or [0.0]
        recent = sum(moves[-14:]) / len(moves[-14:])
        pct = sum(1 for m in moves if m <= recent) / len(moves)
        price = mine[-1] if mine else 1.0
        spread = price * 2 * accounts.COST_BPS / 10_000
        tv = (self.tagged.get(symbol) or {}).get(style, [])
        setups = len({t for t, v in tv if abs(v) >= WHISPER})
        hour = datetime.fromtimestamp(self.now, timezone.utc).hour if self.now is not None else self.ticks % 24
        seg = mine[-15:]
        path = sum(abs(b - a) for a, b in zip(seg, seg[1:])) or 1e-12
        er = abs(seg[-1] - seg[0]) / path if len(seg) > 1 else 0.0
        return [abs(score), float(setups), pct, spread / recent if recent else 0.0, 0.0 if smt_why else 1.0,
                0.0 if parent_why else 1.0, math.sin(hour / 24 * 2 * math.pi), math.cos(hour / 24 * 2 * math.pi),
                1.0 if style == "reversion" else 0.0, er]

    def _pipeline_nudge(self, symbol: str, score: float) -> float:
        """Live feeds only: the news pipeline's lean counts when it is sure (>= PIPE_CONF) and two sources
        confirmed the story; while a doubtful read (< PIPE_DOUBT) waits for a human, exposure is halved."""
        lean = self.pipeline.desk_lean(symbol, self.now, PIPE_CONF)
        if lean is not None:
            score = max(-1.0, min(1.0, score + PIPE_WEIGHT * lean))
        if self.pipeline.doubtful(symbol, self.now, PIPE_DOUBT):
            score *= PIPE_BRAKE
        return score

    def _adds_risk(self, symbol: str, qty: float) -> bool:
        before = self.broker.position(symbol)
        after = before + qty
        return abs(after) > abs(before) or bool(after and before and (after > 0) != (before > 0))

    def _bar_move(self, symbol: str) -> float:
        p = list(self.price_hist.get(symbol, ()))[-(ROUTER_FAST + 1):]
        return sum(abs(b - a) for a, b in zip(p, p[1:])) / (len(p) - 1) if len(p) > 1 else 0.0

    def _stage(self, symbol: str, qty: float, price: float, why: str = "") -> None:
        mult = PULLBACK_ATR_15M if self.bar_seconds == 900 else PULLBACK_ATR
        offset = mult * self._bar_move(symbol)
        limit = price - offset if qty > 0 else price + offset
        self.pending[symbol] = {"qty": qty, "limit": limit, "left": PULLBACK_BARS, "why": why}
        self.log.emit("staged", symbol=symbol, qty=round(qty, 4), limit=round(limit, 4), bars=PULLBACK_BARS)

    def _work_pending(self, symbol: str, prices: dict, score: float) -> int:
        """Fill a staged pullback entry if the price came to it; drop it when it expires or the call flips."""
        order = self.pending.get(symbol)
        if not order:
            return 0
        price = prices[symbol]
        if self.risk.killed or score * order["qty"] <= 0:
            self.pending.pop(symbol)
            return 0
        if (order["qty"] > 0 and price <= order["limit"]) or (order["qty"] < 0 and price >= order["limit"]):
            self.pending.pop(symbol)
            fill_px = order["limit"]
            qty, _ = self.risk.approve(symbol, order["qty"], {**prices, symbol: fill_px})
            if qty and self.beta and not self.beta.jev.validate(self._proposal(symbol, qty, fill_px)).approved:
                return 0
            if not qty:
                return 0
            before = self.broker.position(symbol)
            self.broker.fill(symbol, qty, fill_px)
            closed = self.journal.fill(symbol, before, qty, fill_px, self.ticks, self.now)
            if closed:
                self.bucket[symbol].append(closed["move"])
                self.log.emit("closed", symbol=symbol, side=closed["side"], qty=round(closed["qty"], 4),
                              entry=round(closed["entry"], 4), exit=round(closed["exit"], 4),
                              move=round(closed["move"], 5), won=closed["won"],
                              pnl=round(closed["qty"] * closed["entry"] * closed["move"], 2))
            why = f"{order['why']} (pullback entry)"
            self.log.emit("order", symbol=symbol, qty=round(qty, 4), price=round(fill_px, 4),
                          score=round(score, 3), why=why, checks=[])
            if self.on_order:
                self.on_order(symbol, qty, fill_px, why)
            return 1
        order["left"] -= 1
        if order["left"] <= 0:
            self.pending.pop(symbol)  # the price ran away: no chasing
            self.pending_cool[symbol] = self.ticks + PULLBACK_BARS
        return 0

    def _bucket_mult(self, symbol: str) -> float:
        if not BUCKET_SIZING:
            return 1.0
        moves = list(self.bucket.get(symbol, ()))
        if len(moves) < 8:
            return 0.75
        wins = [m for m in moves if m > 0]
        losses = [-m for m in moves if m <= 0]
        p = len(wins) / len(moves)
        avg_win = sum(wins) / len(wins) if wins else 0.0
        avg_loss = sum(losses) / len(losses) if losses and sum(losses) else (avg_win or 1e-9)
        exp_r = (p * avg_win - (1 - p) * avg_loss) / avg_loss
        if exp_r <= 0.05:
            return 0.25
        if exp_r >= 0.35 and p >= 0.58:
            return 1.4
        return 1.0

    @staticmethod
    def _cluster(pairs: list) -> float:
        groups: dict[str, list[float]] = defaultdict(list)
        for tag, v in pairs:
            if abs(v) >= WHISPER:
                groups[tag].append(v)
        if not groups:
            return 0.0
        total = sum(len(g) ** 0.5 for g in groups.values())
        value = sum(sum(g) / len(g) * len(g) ** 0.5 for g in groups.values()) / total
        return value * (1.0 if len(groups) >= 2 else SINGLE_SETUP)

    def _score(self, v: dict, symbol: str | None = None) -> float:
        tv = self.tagged.get(symbol) if CONSENSUS and symbol is not None and hasattr(self, "tagged") else None
        if tv is not None:
            trend, reversion = self._cluster(tv["trend"]), self._cluster(tv["reversion"])
        else:
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
        if symbol is not None and hasattr(self, "lead_style"):
            t_part, r_part = trend_weight * t_mult * trend, (1 - trend_weight) * r_mult * reversion
            self.lead_style[symbol] = "reversion" if abs(r_part) > abs(t_part) else "trend"
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
