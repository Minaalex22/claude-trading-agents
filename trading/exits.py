"""Exit discipline: every trader manages its own open trade like the Exit agent on the Agents floor.

Taken from the "6 AI agents, 1 conductor" breakdown mina shared (Exit agent + Risk agent) and her own
money rules (trading/money_rules.py: 2.4x target). Only the parts that fit our desk:

- **R** is the trade's risk unit: 10x the agent's average price step over its own window (an ATR stand-in),
  fixed when the trade opens.
- **Stop loss** at 1R against the trade.
- **Breakeven**: once the trade is +1R in profit, the stop moves to the entry price, so a winner can't
  turn into a loss.
- **Take profit** at 2.4R (mina's money-rule target).
- **Time exit**: a trade still not in profit after `MAX_HOLD_WINDOWS` of the agent's own windows is closed.
- After a stop the agent waits `COOLDOWN` ticks before it opens the same side again (no revenge trades).

Left out on purpose: the hedge overlay and news pause (the desk already nets every trader's vote and the
simulated wire has no scheduled events), and the 7/10 confluence score (the confirmation filter in
trading/agents.py already needs signal + news + chart to agree).

Measured on the simulator (200 agents, 2,400 ticks, seeds 1-2): a tight stop (1.5x the average step) cut the
traders' win rate from 57% to 52% and halved profit, because noise kept stopping them out. At 10x the win
rate rose about 2 points (58.6% -> 60.5%, 56.0% -> 58.7%) for about 6% less profit, mostly from banking
winners at the 2.4R target. Profit and win rate trade off; mina asked for win rate first.

`ENABLED = False` (env EXITS=off) turns it off for comparison. Paper only.
"""

ENABLED = True
R_MULT = 10.0
TARGET_R = 2.4
BREAKEVEN_R = 1.0
MAX_HOLD_WINDOWS = 3
COOLDOWN = 5
# Trend trades can run (env TREND_EXITS, "trail" by default): no 2.4R target for the Trend department; once a trend trade is
# +1.5R its stop trails TRAIL_R behind its best point (Seykota: cut losses, let winners run).
TREND_TRAIL = True
TRAIL_R = 1.0  # trend: trail 1R behind the best point ...
TREND_STOP_R = 1.2  # ... with a slightly wider first stop (fewer wick-outs) ...
TREND_BREAKEVEN_R = 1.5  # ... and breakeven, then the trail, only from +1.5R
COOLDOWNS = {"trend": 3, "reversion": 2}  # bars before reopening the same side after a stop
MIN_R = 1e-4  # as a fraction of price, so a flat window still gives a usable stop

# Bank half at +1.2R (env SCALE_OUT): close 50% and lift the stop on the rest to +0.15R (fees + slippage), so a
# trade that reached +1.2R ends a winner. Stall stop (env STALL_STOP): a trade that hasn't reached +0.4R after
# STALL_BARS bars (12 on 15-minute bars) is closed. BAR_SECONDS is set by the desk from the feed's bar length.
SCALE_OUT = True
SCALE_AT_R, SCALE_SHARE, SCALE_STOP_R = 1.2, 0.5, 0.15
STALL_STOP = True
STALL_R, STALL_BARS, STALL_BARS_15M = 0.4, 8, 12
BAR_SECONDS: float | None = None

REASONS = ("stop", "breakeven", "target", "time", "stall")


def risk_unit(prices) -> float:
    """1R in price terms: R_MULT x the average absolute step over the window."""
    p = list(prices)
    if len(p) < 2:
        return 0.0
    step = sum(abs(b - a) for a, b in zip(p, p[1:])) / (len(p) - 1)
    return max(step * R_MULT, p[-1] * MIN_R)


def open_trade(agent) -> None:
    """Remember the trade's R, stop and target when it opens."""
    w = agent.workspace
    r = risk_unit(agent.prices)
    trail = TREND_TRAIL and agent.style != "reversion"
    w["exit_plan"] = {"r": r, "stop_r": -TREND_STOP_R if trail else -1.0, "best_r": 0.0, "held": 0}


def check(agent) -> str | None:
    """Should the open trade be closed now? Returns the reason, or None to keep it.

    Moves the stop to breakeven once the trade has gone +1R.
    """
    if not ENABLED or not agent.position:
        return None
    w = agent.workspace
    plan, entry = w.get("exit_plan"), w.get("entry")
    if not plan or not entry or not plan["r"]:
        return None
    side = 1.0 if agent.position > 0 else -1.0
    now_r = side * (agent.prices[-1] - entry) / plan["r"]
    plan["held"] += 1
    plan["best_r"] = max(plan["best_r"], now_r)
    trail = TREND_TRAIL and agent.style != "reversion"
    if plan["best_r"] >= (TREND_BREAKEVEN_R if trail else BREAKEVEN_R) and plan["stop_r"] < 0:
        plan["stop_r"] = 0.0  # breakeven: this trade can no longer lose
    if trail and plan["best_r"] >= TREND_BREAKEVEN_R:
        plan["stop_r"] = max(plan["stop_r"], plan["best_r"] - TRAIL_R)
    if SCALE_OUT and not agent.must_practice and not plan.get("scaled") and now_r >= SCALE_AT_R:
        plan["scaled"] = True
        plan["stop_r"] = max(plan["stop_r"], SCALE_STOP_R)
        return "scale"  # bank half (agents.py keeps the other half)
    if not trail and now_r >= TARGET_R:
        return "target"
    if now_r <= plan["stop_r"]:
        return "breakeven" if plan["stop_r"] >= 0 else "stop"
    if plan["held"] >= MAX_HOLD_WINDOWS * agent.window and now_r <= 0:
        return "time"
    if STALL_STOP and plan["held"] >= (STALL_BARS_15M if BAR_SECONDS == 900 else STALL_BARS) \
            and plan["best_r"] < STALL_R:
        return "stall"
    return None


def record(agent, reason: str) -> None:
    w = agent.workspace
    counts = w.setdefault("exits", {})
    counts[reason] = counts.get(reason, 0) + 1
    if reason == "stop":
        left = COOLDOWNS.get("reversion" if agent.style == "reversion" else "trend", COOLDOWN) if TREND_TRAIL else COOLDOWN
        w["cooldown"] = {"side": 1.0 if agent.position > 0 else -1.0, "left": left}
    w.pop("exit_plan", None)


def blocked(agent, value: float) -> bool:
    """True while the agent is cooling down after a stop and `value` would reopen the same side."""
    cd = agent.workspace.get("cooldown")
    if not cd or not value:
        return False
    return (value > 0) == (cd["side"] > 0)


def tick_cooldown(agent) -> None:
    cd = agent.workspace.get("cooldown")
    if cd:
        cd["left"] -= 1
        if cd["left"] <= 0:
            agent.workspace.pop("cooldown", None)
