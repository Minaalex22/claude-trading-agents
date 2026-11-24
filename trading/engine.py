"""Run one engine shard. Several shards together run the full agent team.

Configured with environment variables so the same image runs as any shard:
  AGENT_COUNT   total agents across all shards, split evenly over 4 departments (default 1400)
  SHARD_ID      this shard's index, 0-based (default 0)
  SHARD_COUNT   number of shards (default 1)
  SYMBOLS       comma-separated symbols (default AAPL,MSFT,NVDA,AMZN,GOOG,TSLA,META,AMD,BTC,ETH)
  TICKS         ticks to run, 0 = forever (default 0)
  TICK_SECONDS  delay between ticks (default 0.5)
  EVENTS_DIR    directory for the shard's events-<id>.jsonl (optional). The dashboard's
                agent slider writes control.json here; shards resize to match.
  SEED          market/agent seed (default 42)
  ATS_CONFIG    risk/brain config file (default config/ats_matrix.json)
  LEARNING_INTERVAL_SECONDS  override the config's self-adaptive learning interval
  PRACTICE_HOUR_TICKS  ticks in one practice hour (default 3600 / TICK_SECONDS, or 7200 when TICK_SECONDS
                is 0). Each hour every strategy trader makes at least 20 practice trades and every
                agent takes a refresher course (the hourly upgrade).
  EVENT_GUARD   "off" turns off the earnings + big-news guard (default on, live prices only; trading/event_guard.py)
  TRADE_CHECKER "off" turns off Sentinel's written PASS/FAIL and the desk audit log (default on; trading/trade_checker.py)
  READING_ROOM  "off" turns off the shared news + chart read and the traders' confirmation filter (default on)
  READING_ROOM_CLAUDE  "off" keeps Claude out of the reading room even with ANTHROPIC_API_KEY set
  READING_ROOM_MODEL   Claude model for the reading room (default claude-opus-5-5)
  REVIEWS       "off" stops the traders' 15-minute self-reviews and the daily copy-the-best step (default on)
  MARKET        "sim" (default) or "live": live trades real prices (Coinbase, Yahoo) with paper money
  LIVE_POLL_SECONDS  how often live prices are fetched (default 15)
  LIVE_NEWS     "off" turns off the real RSS headlines on live prices (default on with MARKET=live)
  DAILY_LOSS_LIMIT  fraction of the day's starting equity that stops new risk until the next UTC day
                (default 0.02). A file named KILL in EVENTS_DIR closes every position and opens nothing.
  MEMORY        "off" stops saving and restoring what the agents learned (default on when EVENTS_DIR or
                MEMORY_DIR is set). Saved to MEMORY_DIR (default EVENTS_DIR)/memory-<shard>.json.
  EXITS         "off" turns off every trader's exit discipline (trading/exits.py: stop 1R, breakeven after +1R,
                target 2.4R, time exit), for comparison
  ENTRY_FILTERS "off", "all" (default) or a comma list of regime,mtf,vol,meta: the checks a trader runs before
                it opens a trade (trading/entry_filters.py)
  SESSION_RHYTHM "on": Asia calm (trust reversion), London breaks it (trust trend), New York finishes (default off)
  OPEN_FAKE     how to treat the first 30 minutes after London and New York open: "wait" (default), "fade" or "off"
  HA_SHARE      share of the Entry (trend) department trading Heikin Ashi colour flips (signal only; fills real), default 0 (lost money)
  RSI2_SHARE    share of the Reversion department trading Larry Connors' RSI(2), 0..1 (default 0: tested, lower win rate)
  DUAL_BRAIN    "on" (default): each trader's slow half audits its fast half's setups (trading/dual_brain.py)
  CONSENSUS     "on" (default): desk drops whisper votes and weighs setups by sqrt(group size) (trading/desk.py)
  PULLBACK      "on" (default): new desk risk waits up to 2 bars for a 0.25 x bar-move pullback, no chasing
  BUCKET_SIZING "on" (default): each symbol's last 40 desk trades scale its size 0.25x-1.4x (risk limits still cap)
  EXEC_NEXT_BAR "on" (default): orders decided on a bar fill on the NEXT bar's real price plus half the spread and
                slippage (never on the close the signal read, never on a smoothed price) / "off": same-bar close
  OPTION_FLOWS  "on" (default): 0DTE whale option prints (POST /api/option-flow) veto trades against them and
                boost agreeing calls on SPY, QQQ and Big Tech; no effect until prints are sent in
  SNIPER        "off" (default, mina 2026-11-24: it made zero trades) / "on": A+ entries only: score 0.82, 1h-4h bars in 12-17 UTC, reversion sweep + reclaim
                + SMT divergence + news not against; reversion agents bank 80% at +0.9R (trading/sniper.py)
  SCALE_OUT     "on" (default): agents bank half at +1.2R and lift the rest's stop to +0.15R (trading/exits.py)
  STALL_STOP    "on" (default): close a trade that hasn't reached +0.4R in 8 bars (12 on 15m)
  BUCKET_CAP    "on" (default): at most 2 open positions per macro bucket (USD bloc, US tech beta, crypto)
  SMT_FILTER    "on" (default): sister-pair divergence for reversion, confirmation for trend (trading/smt_filter.py)
  PARENT_LOCK   "on" (default): 15m/1h entries follow the parent timeframe (4x bars)
  META_GATE     "on" (default): walk-forward meta-label gate on desk entries (trading/meta_gate.py)
  NEWS_SPLIT    "on" (default): trend traders need agreeing news + chart; reversion is stopped only by fresh/macro news
  NEWS_TO_DESK  "on" (default, MARKET=live only): sure, two-source news pipeline reads nudge the desk; doubtful ones brake it
  DEAD_ZONE     "on" (default): no new forex/metals risk 21-23 UTC, no new US stock risk in the last 10 minutes
  REGIME_ROUTER "on": per symbol, mute Trend in chop and lean on it in breakouts (trading/desk.py; default on)
  TREND_EXITS   "trail": Trend trades have no 2.4R target and trail their stop 1.5R behind the best point (default; "fixed" = 2.4R target)
  ATLAS_DEEP    "on": Atlas may bench up to 60% of a department that won under 45% of its trades that hour (default on)
  TREND_GATE    e.g. 0.25: the desk ignores Trend on a symbol while the analysts' regime vote is below it (default 0.25, "off" to disable)
  COST_HURDLE   no new desk exposure unless the expected move beats N round-trip fees: "auto" (default: 1m bars 5x,
                5m 4x, 15m 2.5x, 1h 1.8x, 4h and slower 1.5x), a fixed N, or "off"
  TREND_WEIGHT  0..1: how much the desk listens to the Trend department (default 1)
  SIGNAL_GATE   "on": every new trade goes through triage -> gate 0.8 -> deep read (trading/signal_gate.py; default off)
  MINDS         "off" turns off every trader's two-step mind (think, then decide), for comparison
  COACH         "off" keeps the hourly Claude coach off even with ANTHROPIC_API_KEY set
  COACH_MODEL   Claude model for the coach (default claude-opus-5-5)
  SIGNAL_THRESHOLD  how strong the desk's combined call must be to hold a position (default 0.72; was 0.5)
  HORIZON_LOCK  "on" (default): fast agents (5-20 bars) need medium/slow agents agreeing (trading/desk.py)
  LEARN_ON_CLOSE "on" (default): self-trust and minds freeze during a chosen trade and learn once when it closes
  MIN_TRADE     smallest rebalance in dollars the desk will send (default 5000)
  LIVE_MAX_ORDER_USD, LIVE_MAX_DAY_USD, LIVE_SYMBOLS  real-money mirror (shard 0, MARKET=live): each desk
                order in LIVE_SYMBOLS becomes a capped proposal the owner approves (trading/live_orders.py)
  TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID  phone alerts (trading/alerts.py): real-money proposals, fills, stops
  DASHBOARD_URL the app's address, so alerts can link to the approval desk
  MASTERS_OFF   comma-separated masters whose rules are switched off (e.g. "soros,buffett"; see trading/masters.py)
  SESSIONS      "off" stops the market clock (trading/sessions.py): New York opening hour, midday, power hour,
                crypto weekends and the session average (default on; it needs real time, so live or backtest only)
  PROVEN_ONLY   "on": the paper desk trades only on what proven agents (trading/accounts.py) say (default off;
                real-money proposals always need proven agents' backing)
  FEE_BPS       paper broker cost per fill in basis points (default 1; the backtest adds slippage)
  MODE          only "paper" is implemented
"""

import json
import os
import signal
import sys
import time
from pathlib import Path

from . import academy, live_orders, masters, memory
from .coach import make_coach
from . import config as ats_config
from . import control
from . import desk as desk_rules
from . import managers as managers_rules
from .agents import build_team
from .alerts import Alerts
from .org import DEPARTMENTS, MIN_DEPARTMENT_SIZE
from .brains import BrainAlpha, BrainBeta
from .broker import PaperBroker
from .desk import Desk
from .events import EventLog
from .market import SimulatedMarket
from .news import NewsWire
from .reading_room import ReadingRoom, make_reader
from .risk import RiskLimits, RiskManager
from .night_shift import NightShiftConfig, NightShiftEngine, briefing_text
from .hooks import should_suppress_alert

DEFAULT_SYMBOLS = "AAPL,MSFT,NVDA,AMZN,GOOG,TSLA,META,AMD,BTC,ETH"
DEFAULT_AGENT_COUNT = MIN_DEPARTMENT_SIZE * len(DEPARTMENTS)  # 350 per department
CONTROL_EVERY = 10
PROVEN_AGREE = 0.3  # how strongly proven agents must back a trade before it becomes a real-money proposal  # ticks between checks of the agent slider


def shard_agents(agent_count: int, symbols: list[str], shard_id: int, shard_count: int, seed: int):
    if not 0 <= shard_id < shard_count:
        raise SystemExit(f"SHARD_ID must be in 0..{shard_count - 1}")
    agents = build_team(agent_count, symbols, seed)[shard_id::shard_count]
    for agent in agents:
        academy.enroll(agent)  # every agent is trained for its department's job before it starts
    return agents


def resize(desk: Desk, agent_count: int, symbols: list[str], shard_id: int, shard_count: int, seed: int) -> int:
    """Grow or shrink the shard's team to match `agent_count`. Agents that stay keep their brains."""
    current = {a.agent_id: a for a in desk.agents}
    wanted = shard_agents(agent_count, symbols, shard_id, shard_count, seed)
    desk.agents = [current.get(a.agent_id, a) for a in wanted]
    return len(desk.agents)


def _save(desk: Desk, path: Path, log: EventLog) -> None:
    try:
        memory.save(desk, path)
        if desk.memory is not None:
            desk.memory["saved_tick"] = desk.ticks
    except OSError as e:  # a full or read-only disk must not stop the desk
        log.emit("memory", saved=False, error=str(e)[:200])


def run(env=os.environ, stream=None, market=None) -> Desk:
    """Run a shard. `market` replaces the price feed (the backtest passes its historical bars)."""
    from .ring import require_paper

    require_paper(env.get("MODE", "paper"))
    cfg = ats_config.load(env.get("ATS_CONFIG"))
    agent_count = int(env.get("AGENT_COUNT", DEFAULT_AGENT_COUNT))
    shard_id = int(env.get("SHARD_ID", 0))
    shard_count = int(env.get("SHARD_COUNT", 1))
    symbols = [s.strip() for s in env.get("SYMBOLS", DEFAULT_SYMBOLS).split(",") if s.strip()]
    ticks = int(env.get("TICKS", 0))
    live_market = env.get("MARKET", "sim").lower() == "live"
    # Live prices change every poll, not every half second: tick once per poll by default.
    tick_seconds = float(env.get("TICK_SECONDS", env.get("LIVE_POLL_SECONDS", 15) if live_market else 0.5))
    seed = int(env.get("SEED", 42))
    events_dir = env.get("EVENTS_DIR")

    from . import mind
    mind.ENABLED = env.get("MINDS", "on").lower() not in ("off", "0", "false", "no")
    from . import exits
    exits.ENABLED = env.get("EXITS", "on").lower() not in ("off", "0", "false", "no")
    from . import entry_filters, loss_audit
    entry_filters.configure(env.get("ENTRY_FILTERS"))
    from . import sessions
    from . import agents as agent_types, signal_gate
    desk_rules.REGIME_ROUTER = env.get("REGIME_ROUTER", "on").lower() in ("on", "1", "true", "yes")
    from . import dual_brain
    dual_brain.ENABLED = env.get("DUAL_BRAIN", "on").lower() in ("on", "1", "true", "yes")
    dual_brain.reset()
    exits.TREND_TRAIL = env.get("TREND_EXITS", "trail").lower() == "trail"
    managers_rules.ATLAS_DEEP = env.get("ATLAS_DEEP", "on").lower() in ("on", "1", "true", "yes")
    on = lambda k: env.get(k, "on").lower() in ("on", "1", "true", "yes")
    desk_rules.CONSENSUS, desk_rules.PULLBACK, desk_rules.BUCKET_SIZING = on("CONSENSUS"), on("PULLBACK"), on("BUCKET_SIZING")
    from . import exits as exit_rules, meta_gate, risk as risk_rules, smt_filter
    exit_rules.SCALE_OUT, exit_rules.STALL_STOP = on("SCALE_OUT"), on("STALL_STOP")
    risk_rules.BUCKET_CAP = on("BUCKET_CAP")
    smt_filter.SMT_FILTER, smt_filter.PARENT_LOCK = on("SMT_FILTER"), on("PARENT_LOCK")
    meta_gate.ENABLED = on("META_GATE")
    from . import sniper
    sniper.SNIPER = env.get("SNIPER", "off").lower() in ("on", "1", "true", "yes")
    from . import execution_integrity
    execution_integrity.EXEC_NEXT_BAR = on("EXEC_NEXT_BAR")
    from . import option_flows
    option_flows.OPTION_FLOWS = on("OPTION_FLOWS")
    desk_rules.Desk.meta_on = meta_gate.ENABLED
    from . import agents as agent_rules
    agent_rules.NEWS_SPLIT = on("NEWS_SPLIT")
    agent_rules.LEARN_ON_CLOSE = on("LEARN_ON_CLOSE")
    desk_rules.HORIZON_LOCK = on("HORIZON_LOCK")
    desk_rules.SIGNAL_THRESHOLD = float(env.get("SIGNAL_THRESHOLD", 0.72))
    from . import hooks as hook_rules
    hook_rules.DEAD_ZONE = on("DEAD_ZONE")
    desk_rules.TREND_GATE = (float(env["TREND_GATE"]) if env.get("TREND_GATE", "off") not in ("off", "") else None) if "TREND_GATE" in env else 0.25
    hurdle = env.get("COST_HURDLE", "auto").lower()
    desk_rules.COST_HURDLE = None if hurdle == "auto" else 0.0 if hurdle in ("off", "") else max(0.0, float(hurdle))
    desk_rules.TREND_WEIGHT = max(0.0, min(1.0, float(env.get("TREND_WEIGHT", 1.0))))
    signal_gate.ENABLED = env.get("SIGNAL_GATE", "off").lower() in ("on", "1", "true", "yes")
    signal_gate.reset()
    agent_types.RSI2_SHARE = float(env.get("RSI2_SHARE", agent_types.RSI2_SHARE))
    agent_types.HA_SHARE = float(env.get("HA_SHARE", "0"))
    sessions.RHYTHM_ON = env.get("SESSION_RHYTHM", "off").lower() in ("on", "1", "true", "yes")
    sessions.OPEN_FAKE = env.get("OPEN_FAKE", "wait").lower()
    entry_filters.reset()
    loss_audit.reset()
    agents = shard_agents(agent_count, symbols, shard_id, shard_count, seed)
    log = EventLog(shard_id, Path(events_dir) / f"events-{shard_id}.jsonl" if events_dir else None, stream)
    broker = PaperBroker(fee_bps=float(env.get("FEE_BPS", 1.0)))
    interval = env.get("LEARNING_INTERVAL_SECONDS")
    alpha = BrainAlpha(["trend", "reversion"], float(interval) if interval else cfg.beta.learning_interval_seconds,
                       cfg.beta.learning_enabled)
    limits = RiskLimits(max_drawdown=cfg.risk.max_drawdown,
                        max_daily_loss=float(env.get("DAILY_LOSS_LIMIT", RiskLimits.max_daily_loss)))
    hour_ticks = int(env.get("PRACTICE_HOUR_TICKS") or (round(3600 / tick_seconds) if tick_seconds else 7200))
    room = ReadingRoom(symbols, claude=make_reader(env)) if env.get("READING_ROOM", "on") != "off" else None
    # Night Shift (trading/night_shift.py): the overnight capsule; on by default, NIGHT_SHIFT_ENABLED=false turns it off.
    night = NightShiftEngine(NightShiftConfig.from_env(env))
    risk = RiskManager(limits, broker, kill_path=Path(events_dir) / "KILL" if events_dir else None, night_shift=night)
    desk = Desk(agents, broker, risk, log, beta=BrainBeta(cfg), alpha=alpha,
                hour_ticks=hour_ticks, room=room, reviews=env.get("REVIEWS", "on") != "off", coach=make_coach(env))
    desk.signal_threshold = float(env.get("SIGNAL_THRESHOLD", desk.signal_threshold))
    masters.OFF = {m.strip() for m in env.get("MASTERS_OFF", "").split(",") if m.strip()}
    desk.use_clock = env.get("SESSIONS", "on").lower() not in ("off", "0", "false", "no")
    desk.proven_only = env.get("PROVEN_ONLY", "off").lower() in ("on", "1", "true", "yes")
    desk.min_trade = float(env.get("MIN_TRADE", desk.min_trade))
    # Sentinel's written PASS/FAIL + the audit log (trading/trade_checker.py), on by default.
    if env.get("TRADE_CHECKER", "on").lower() not in ("off", "0", "false", "no"):
        from . import trade_checker
        desk.checker = trade_checker.TradeChecker(events_dir, shard_id)
    # Earnings + big-news guard (trading/event_guard.py): live prices only (the calendar is about real dates).
    if live_market and market is None and env.get("EVENT_GUARD", "on").lower() not in ("off", "0", "false", "no"):
        from . import event_guard
        desk.calendar = event_guard.Calendar(symbols)
        desk.calendar.start()
    if live_market and env.get("NEWS_TO_DESK", "on").lower() in ("on", "1", "true", "yes"):
        from .news_pipeline import NewsPipeline
        desk.pipeline = NewsPipeline()
    alerts = Alerts.from_env(env)  # Telegram phone alerts (trading/alerts.py); off without the two settings
    limits_live = live_orders.Limits(env)
    if live_market and events_dir and shard_id == 0 and limits_live.on and market is None:
        # Real-money mirror: shard 0's orders become small proposals the owner approves on the dashboard.
        def mirror(symbol, qty, price, why):
            # Real money follows proven agents only: enough of them on this symbol, agreeing with the trade.
            view, n = desk.proven_view.get(symbol, (0.0, 0))
            if n < desk.proven_min or view * qty <= 0 or abs(view) < PROVEN_AGREE:
                return
            order = live_orders.propose(Path(events_dir), symbol, "buy" if qty > 0 else "sell", price,
                                        json.dumps(why, default=str)[:300], limits_live)
            if order:
                log.emit("live_proposal", **{k: order[k] for k in ("id", "symbol", "side", "qty", "price")})
                alerts.proposal(order)
        desk.on_order = mirror
    memory_dir = env.get("MEMORY_DIR") or events_dir
    memory_path = (Path(memory_dir) / f"memory-{shard_id}.json"
                   if memory_dir and env.get("MEMORY", "on").lower() not in ("off", "0", "false", "no") else None)
    remembered = memory.restore(desk, memory_path) if memory_path else 0
    desk.memory = {"remembered": remembered, "saved_tick": None, "on": memory_path is not None}
    # Every shard sees the same market (same seed), but trades its own agents.
    live = live_market
    if market is not None:
        news = None  # a replay of past prices: past headlines aren't available
        if hasattr(market, "now"):
            # A replay's day is the bar's day, not today's: the daily loss stop must reset each replayed day.
            risk.clock = lambda: market.now() or time.time()
    elif live:
        from .live_market import LiveMarket  # real prices, paper money: orders still go to the paper broker
        market = LiveMarket(symbols, poll_seconds=float(env.get("LIVE_POLL_SECONDS", 15)))
        # The simulated wire hints at the simulator's hidden drift, which real prices don't have: read real news.
        news = None
        if env.get("LIVE_NEWS", "on").lower() not in ("off", "0", "false", "no"):
            from .news_feed import LiveNewsFeed
            news = LiveNewsFeed(symbols)
    else:
        market = SimulatedMarket(symbols, seed=seed)
        news = NewsWire(symbols, seed=seed)

    roles, departments = {}, {}
    for a in agents:
        roles[a.role] = roles.get(a.role, 0) + 1
        departments[a.department] = departments.get(a.department, 0) + 1
    log.emit("start", agents=len(agents), total_agents=agent_count, shard_count=shard_count,
             roles=roles, departments=departments, symbols=symbols, mode="paper",
             market="history" if env.get("MARKET") == "history" else "live" if live else "simulated", config_version=cfg.version,
             learning_interval_seconds=alpha.interval, school=academy.summarize(agents),
             remembered=remembered, coach=desk.coach is not None)

    stopping = False
    last_stop = None

    def stop(*_):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    try:
        while not stopping and (ticks == 0 or desk.ticks < ticks):
            if events_dir and desk.ticks % CONTROL_EVERY == 0:
                per_department = control.read(Path(events_dir))
                if per_department and control.total(per_department) != agent_count:
                    before = len(desk.agents)
                    agent_count = control.total(per_department)
                    after = resize(desk, agent_count, symbols, shard_id, shard_count, seed)
                    log.emit("resize", agents=after, was=before, total_agents=agent_count,
                             per_department=per_department)
            prices = market.tick()
            if live:
                risk.frozen = market.stale()
                if desk.ticks % 20 == 0:
                    log.emit("market", **feed_status(market, news))
            now = market.now() if hasattr(market, "now") else (time.time() if live else None)
            desk.step(prices, news.tick(market.drift, desk.ticks + 1) if news else [], now=now)
            if desk.risk.stop_reason != last_stop:
                last_stop = desk.risk.stop_reason
                if not (last_stop and last_stop.startswith("night shift") and should_suppress_alert(night, now)):
                    alerts.stop(shard_id, last_stop)  # the capsule's own stop waits for the 07:00 briefing
            if now is not None and night.cfg.enabled and desk.ticks % 20 == 0:
                log.emit("night_shift", **night.status())
            brief = night.take_briefing()
            if brief:
                log.emit("night_shift", briefing_now=True, **night.status())
                if shard_id == 0:
                    alerts.send(briefing_text(brief))
            if memory_path and desk.ticks % memory.SAVE_EVERY == 0:
                _save(desk, memory_path, log)
            if tick_seconds:
                time.sleep(tick_seconds)
    except KeyboardInterrupt:
        pass
    if memory_path:
        _save(desk, memory_path, log)
    log.emit("stop", ticks=desk.ticks, equity=round(broker.equity(market.prices), 2))
    log.close()
    return desk


def feed_status(market, news) -> dict:
    """The price feed's status plus the news feed's; news keys that clash (polls, last_error) get a news_ prefix."""
    status = dict(market.status())
    for key, value in (news.status() if news else {}).items():
        status[key if key not in status else f"news_{key}"] = value
    return status


if __name__ == "__main__":
    run()
    sys.exit(0)
