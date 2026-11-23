"""Entry filters: checks every trader runs before it opens a new trade, to raise the share of trades that win.

Taken from proven win-rate methods found on the web, keeping only what suits our desk (each one can be
switched on or off with ENTRY_FILTERS, and each was tested on the simulator before it was kept):

- **regime**: trade the style that fits the market. Trend traders only open when the market is trending
  (efficiency ratio over the agent's own window at least `ER_TREND`); reversion traders only fade moves when
  it is choppy (at most `ER_RANGE`). Fading a strong trend is the classic way mean-reversion loses.
- **mtf** (multi-timeframe confirmation): the higher timeframe (`HTF_MULT` x the agent's own window) must not
  point against the trade. Trend trades need the higher timeframe with them; reversion trades must not fade
  a strong higher-timeframe move.
- **vol** (volatility filter, built but off by default: it almost never fired): no new trade during a volatility spike (recent volatility more than `VOL_MAX`
  x its longer average), when stops get hit by noise and moves are news-driven.
- **meta** (meta-labeling, Lopez de Prado): a second model per department learns, from the department's own
  closed trades, how likely a trade with these entry features is to win, and skips trades it rates below
  `META_MIN`. It only starts judging after `META_WARMUP` trades. Off by default: it helped the simulator by
  under a point and cut the win rate by 2 to 5 points on real BTC, ETH and SOL prices. Its calibration (Brier
  score) is still reported so it can be judged again later.

Each trade's entry features are also written down (`features`), so the loss audit (trading/loss_audit.py)
can say what the first wrong step of every losing trade was.

Paper only.
"""

import math
import zlib
import statistics

ALL = ("regime", "mtf", "vol", "meta")
DEFAULT = ("regime", "mtf")  # what runs unless ENTRY_FILTERS says otherwise (vol and meta: tested, not kept)
ON: set[str] = set(DEFAULT)  # ENTRY_FILTERS (engine) picks which ones run; "off" turns them all off

ER_TREND = 0.25
PERSONAL = 0.0  # >0: each agent gets its own trend bar (ER_TREND x 1 +/- PERSONAL). Tested at 0.5: no change
FILTER_REVERSION = False  # regime/mtf checks on reversion traders: tested, they cut reversion's win rate
ER_RANGE = 0.45
HTF_MULT = 4
HTF_AGAINST = 0.25  # reversion: a higher-timeframe move this strong (in its own noise units) is not faded
VOL_MAX = 2.0
META_MIN = 0.5
META_WARMUP = 300
META_RATE = 0.02

COUNTS = {f: 0 for f in ALL}  # trades each filter stopped, this process


def efficiency(prices) -> float:
    p = list(prices)
    path = sum(abs(b - a) for a, b in zip(p, p[1:]))
    return abs(p[-1] - p[0]) / path if path else 0.0


def _vol(prices) -> float:
    p = list(prices)
    rets = [b / a - 1 for a, b in zip(p, p[1:])]
    return statistics.pstdev(rets) if len(rets) > 1 else 0.0


def features(agent, direction: float) -> dict:
    """What the market looked like for a trade in `direction`: regime, higher timeframe, volatility."""
    own = list(agent.prices)
    hist = list(agent.history) if agent.history else own
    d = 1.0 if direction > 0 else -1.0
    er = efficiency(own) if len(own) > 2 else 0.0
    # Higher timeframe: the move over the long window, in units of its own noise (move / (vol * sqrt(n))).
    long_vol = _vol(hist)
    n = max(1, len(hist) - 1)
    htf = (hist[-1] / hist[0] - 1) / (long_vol * math.sqrt(n)) if long_vol and len(hist) > 2 else 0.0
    recent = _vol(own[-max(3, len(own) // 3):])
    vol_ratio = recent / long_vol if long_vol else 1.0
    return {"er": round(er, 4), "htf": round(d * htf, 4), "vol_ratio": round(vol_ratio, 4)}


class MetaLabeler:
    """Online logistic regression: P(win | entry features), learned from closed trades."""

    KEYS = ("edge", "agree", "sure", "er", "htf", "vol_ratio")

    def __init__(self):
        self.w = {k: 0.0 for k in self.KEYS}
        self.bias = 0.0
        self.seen = 0
        # Calibration, scored on each trade BEFORE the model learns from it (so it is always out of sample).
        self.brier_sum = 0.0
        self.wins = 0
        self.buckets: dict[str, list[int]] = {}  # "0.6" -> [wins, trades] for predictions 0.6..0.7

    def _x(self, note: dict) -> dict:
        x = {k: float(note.get(k) or 0.0) for k in self.KEYS}
        x["htf"] = max(-3.0, min(3.0, x["htf"]))
        x["vol_ratio"] = max(0.0, min(4.0, x["vol_ratio"])) - 1.0
        return x

    def prob(self, note: dict) -> float:
        x = self._x(note)
        z = self.bias + sum(self.w[k] * x[k] for k in self.KEYS)
        return 1 / (1 + math.exp(-max(-30.0, min(30.0, z))))

    def learn(self, note: dict, won: bool) -> None:
        x = self._x(note)
        p = self.prob(note)
        y = 1.0 if won else 0.0
        self.brier_sum += (p - y) ** 2
        self.wins += won
        row = self.buckets.setdefault(f"{min(0.9, int(p * 10) / 10):.1f}", [0, 0])
        row[0] += won
        row[1] += 1
        err = y - p
        self.bias += META_RATE * err
        for k in self.KEYS:
            self.w[k] += META_RATE * err * x[k]
        self.seen += 1

    def ready(self) -> bool:
        return self.seen >= META_WARMUP

    def calibration(self) -> dict:
        """Brier score (lower is better), Brier skill vs always guessing the base rate, and the calibration
        table: for trades the model rated 0.6-0.7, how many really won."""
        n = self.seen
        if not n:
            return {"trades": 0}
        base = self.wins / n
        brier = self.brier_sum / n
        ref = base * (1 - base)
        return {"trades": n, "base_rate": round(base, 4), "brier": round(brier, 4),
                "brier_skill": round(1 - brier / ref, 4) if ref else None,
                "buckets": {b: {"predicted": float(b) + 0.05, "won": round(w / t, 4), "trades": t}
                            for b, (w, t) in sorted(self.buckets.items())}}


def calibration() -> dict:
    return {k: m.calibration() for k, m in META.items()}


META: dict[str, MetaLabeler] = {}


def meta_for(agent) -> MetaLabeler:
    return META.setdefault(agent.department or agent.style, MetaLabeler())


def reset() -> None:
    META.clear()
    for f in ALL:
        COUNTS[f] = 0


def trend_bar(agent) -> float:
    if not PERSONAL:
        return ER_TREND
    h = (zlib.crc32(agent.agent_id.encode()) % 1000) / 999  # the agent's own, fixed for life
    return ER_TREND * (1 + PERSONAL * (2 * h - 1))


def blocked_by(agent, direction: float, note: dict) -> str | None:
    """The first filter that says no to opening a trade in `direction`, or None if it may open."""
    if not ON or not direction:
        return None
    reversion = agent.style == "reversion"
    if reversion and not FILTER_REVERSION:
        pass
    elif "regime" in ON:
        if (reversion and note["er"] > ER_RANGE) or (not reversion and note["er"] < trend_bar(agent)):
            return "regime"
    if "mtf" in ON and (FILTER_REVERSION or not reversion):
        if (reversion and note["htf"] < -HTF_AGAINST) or (not reversion and note["htf"] < 0):
            return "mtf"
    if "vol" in ON and note["vol_ratio"] > VOL_MAX:
        return "vol"
    if "meta" in ON:
        m = meta_for(agent)
        if m.ready() and m.prob(note) < META_MIN:
            return "meta"
    return None


def check(agent, value: float, signal: float) -> float:
    """Apply the filters to the vote `value`: block new exposure the filters reject, never block an exit."""
    if not ON or not value:
        return value
    held = 1.0 if agent.position > 0 else -1.0 if agent.position < 0 else 0.0
    d = 1.0 if value > 0 else -1.0
    if d == held and abs(value) <= abs(agent.position):
        return value  # trimming or holding: no new exposure
    note = features(agent, d)
    note.update(edge=abs(signal), agree=agent._agreement(d), sure=agent.workspace.get("sureness", abs(signal)))
    why = blocked_by(agent, d, note)
    if why is None:
        return value
    COUNTS[why] += 1
    ws = agent.workspace.setdefault("filtered", {})
    ws[why] = ws.get(why, 0) + 1
    return agent.position if d == held else 0.0


def configure(spec: str | None) -> None:
    """ENTRY_FILTERS: "off", "all"/unset, or a comma list such as "regime,mtf"."""
    global ON
    s = (spec or "default").strip().lower()
    if s in ("off", "0", "false", "no", "none", ""):
        ON = set()
    elif s in ("default", "on"):
        ON = set(DEFAULT)
    elif s == "all":
        ON = set(ALL)
    else:
        ON = {f.strip() for f in s.split(",") if f.strip() in ALL}
