"""Loss audit: why does a losing trade lose? Each loser is put in one bucket: the FIRST step where it went wrong.

From the agent-evaluation guide mina shared (audit failed traces, label the first step that went off the
rails, split failures into data / rules / logic / reasoning buckets), mapped onto how a trader opens a trade:

1. `data`         the trade opened on missing or flat price history (pipeline problem, not the agent's fault)
2. `forced`       a practice-quota trade: opened only to make the hour's 20 practice trades (a rule problem)
3. `wrong_regime` a trend trade in a choppy market, or a reversion trade fading a trending one
4. `against_trend` the higher timeframe pointed the other way
5. `vol_spike`    opened during a volatility spike
6. `weak_signal`  the agent's own signal and sureness were both weak
7. `against_view` the reading room (news + chart) leaned against the trade
8. `noise`        every check was fine and the market still went the other way: the part of losing no
                  filter can remove (in the simulator the price is mostly random, so this bucket never empties)

The order matters: a loser is blamed on the earliest step, the way the guide says most errors start two steps
before the final answer. `report` gives each bucket's share of all losses and of every trade, and keeps the
last `KEEP` losing trades so they can be read one by one. Paper only.
"""

from collections import deque

BUCKETS = ("data", "forced", "wrong_regime", "against_trend", "vol_spike", "weak_signal", "against_view", "noise")
WEAK = 0.2
KEEP = 50

COUNTS = {b: 0 for b in BUCKETS}
TOTAL = {"trades": 0, "losses": 0, "wins": 0}
WIN_BUCKETS = {b: 0 for b in BUCKETS}  # the same buckets for winners, so a bucket's win rate can be read
RECENT: deque = deque(maxlen=KEEP)


def bucket(agent_style: str, t: dict) -> str:
    """The first step where this trade went wrong (applied to winners too, to compare)."""
    from . import entry_filters as f

    if "er" not in t or not t.get("vol_ratio"):
        return "data"
    if t.get("forced"):
        return "forced"
    reversion = agent_style == "reversion"
    if (reversion and t["er"] > f.ER_RANGE) or (not reversion and t["er"] < f.ER_TREND):
        return "wrong_regime"
    if (reversion and t["htf"] < -f.HTF_AGAINST) or (not reversion and t["htf"] < 0):
        return "against_trend"
    if t["vol_ratio"] > f.VOL_MAX:
        return "vol_spike"
    if t.get("edge", 0) < WEAK and t.get("sure", 0) < WEAK:
        return "weak_signal"
    if t.get("agree", 0) < 0:
        return "against_view"
    return "noise"


def record(agent, trade: dict) -> None:
    b = bucket(agent.style, trade)
    TOTAL["trades"] += 1
    ws = agent.workspace.setdefault("loss_causes", {})
    if trade["won"]:
        TOTAL["wins"] += 1
        WIN_BUCKETS[b] += 1
        return
    TOTAL["losses"] += 1
    COUNTS[b] += 1
    ws[b] = ws.get(b, 0) + 1
    RECENT.append({"agent": agent.agent_id, "dept": agent.department, "cause": b, "exit": trade.get("exit"),
                   **{k: round(v, 4) if isinstance(v, float) else v for k, v in trade.items()
                      if k in ("move", "edge", "agree", "sure", "er", "htf", "vol_ratio", "forced")}})


def reset() -> None:
    for b in BUCKETS:
        COUNTS[b] = WIN_BUCKETS[b] = 0
    for k in TOTAL:
        TOTAL[k] = 0
    RECENT.clear()


def summarize(causes: dict, wins: int, losses: int) -> dict:
    """Shares for a {bucket: losses} count (a department's, or the whole desk's)."""
    lost = sum(causes.values()) or 1
    trades = (wins + losses) or 1
    rows = sorted(((b, n) for b, n in causes.items() if n), key=lambda r: -r[1])
    return {"top": [{"cause": b, "losses": n, "share_of_losses": round(n / lost, 4),
                     "share_of_trades": round(n / trades, 4)} for b, n in rows]}


def report() -> dict:
    """The desk-wide audit in this process: each cause's share of losses and the win rate inside each bucket."""
    out = summarize(COUNTS, TOTAL["wins"], TOTAL["losses"])
    for row in out["top"]:
        n = row["losses"] + WIN_BUCKETS[row["cause"]]
        row["bucket_win_rate"] = round(WIN_BUCKETS[row["cause"]] / n, 4) if n else None
    out.update(trades=TOTAL["trades"], losses=TOTAL["losses"], recent=list(RECENT)[-30:], mode="paper")
    return out
