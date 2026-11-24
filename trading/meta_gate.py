"""Meta-label gate for the desk (mina's batch 5; Lopez de Prado's two-stage set-up).

Stage 1 is the desk: 1,400 agents and the desk's rules pick the side. Stage 2 is this gate: a small logistic
regression, pure Python, predicts only P(this entry reaches +`WIN_R` R before -1 R), from what the desk knows
at entry time (`features`). Below `VETO_BELOW` the entry is vetoed; at `SIZE_UP_AT` or more it is sized up by
`SIZE_UP` (quarter-Kelly-sized step), and the hard risk limits still cap the order.

Walk-forward only: every candidate entry (taken or vetoed) is written down with its features; its label
comes later from the real price path (+WIN_R first = 1, -1R first or `HORIZON` bars without either = 0).
The model is refitted every `REFIT_EVERY` new labels on labelled entries from the past only, and it says
nothing until it has `MIN_TRAIN` of them. Only desk entries feed it: practice-quota trades never reach the
desk, so they never reach this model. Switch: env META_GATE (on by default). Paper only.
"""

from __future__ import annotations

import math

ENABLED = True
WIN_R = 1.2
HORIZON = 120
MIN_TRAIN = 40
REFIT_EVERY = 10
VETO_BELOW = 0.56
SIZE_UP_AT, SIZE_UP = 0.65, 1.25
L2, STEPS, RATE = 0.01, 300, 0.3
NAMES = ("score", "setups", "atr_pct", "spread_atr", "smt", "parent", "hour_sin", "hour_cos", "reversion", "er")


class MetaGate:
    def __init__(self) -> None:
        self.open: list[dict] = []  # entries waiting for their label
        self.rows: list[tuple[list[float], int]] = []
        self.w: list[float] | None = None
        self.mean = self.sd = None
        self.since_fit = 0
        self.counts = {"vetoed": 0, "sized_up": 0, "passed": 0, "learning": 0, "labelled": 0}

    # ---- labels ------------------------------------------------------------------------------------------
    def note(self, symbol: str, side: float, price: float, r: float, x: list[float]) -> None:
        if r > 0:
            self.open.append({"symbol": symbol, "side": side, "entry": price, "r": r, "x": x, "bars": 0})

    def update(self, prices: dict[str, float]) -> None:
        still = []
        for e in self.open:
            p = prices.get(e["symbol"])
            if p is None:
                still.append(e)
                continue
            e["bars"] += 1
            move = e["side"] * (p - e["entry"]) / e["r"]
            if move >= WIN_R or move <= -1.0 or e["bars"] >= HORIZON:
                self.rows.append((e["x"], int(move >= WIN_R)))
                self.counts["labelled"] += 1
                self.since_fit += 1
            else:
                still.append(e)
        self.open = still
        if len(self.rows) >= MIN_TRAIN and (self.w is None or self.since_fit >= REFIT_EVERY):
            self.fit()

    # ---- model -------------------------------------------------------------------------------------------
    def fit(self) -> None:
        xs = [x for x, _ in self.rows]
        ys = [y for _, y in self.rows]
        k = len(xs[0])
        self.mean = [sum(x[i] for x in xs) / len(xs) for i in range(k)]
        self.sd = [math.sqrt(sum((x[i] - self.mean[i]) ** 2 for x in xs) / len(xs)) or 1.0 for i in range(k)]
        z = [self._z(x) for x in xs]
        w = [0.0] * (k + 1)
        n = len(z)
        for _ in range(STEPS):
            g = [0.0] * (k + 1)
            for zi, y in zip(z, ys):
                err = _sig(w[0] + sum(a * b for a, b in zip(w[1:], zi))) - y
                g[0] += err
                for i in range(k):
                    g[i + 1] += err * zi[i]
            w[0] -= RATE * g[0] / n
            for i in range(k):
                w[i + 1] -= RATE * (g[i + 1] / n + L2 * w[i + 1])
        self.w = w
        self.since_fit = 0

    def _z(self, x: list[float]) -> list[float]:
        return [(v - m) / s for v, m, s in zip(x, self.mean, self.sd)]

    def p_win(self, x: list[float]) -> float | None:
        if not ENABLED or self.w is None:
            return None
        return _sig(self.w[0] + sum(a * b for a, b in zip(self.w[1:], self._z(x))))

    def size(self, x: list[float]) -> float:
        """0 to veto, SIZE_UP to size up, else 1."""
        p = self.p_win(x)
        if p is None:
            self.counts["learning"] += 1
            return 1.0
        if p < VETO_BELOW:
            self.counts["vetoed"] += 1
            return 0.0
        if p >= SIZE_UP_AT:
            self.counts["sized_up"] += 1
            return SIZE_UP
        self.counts["passed"] += 1
        return 1.0

    def snapshot(self) -> dict:
        return {"labelled": len(self.rows), "waiting": len(self.open), "counts": dict(self.counts),
                "weights": None if self.w is None else dict(zip(("bias",) + NAMES, (round(v, 3) for v in self.w)))}


def _sig(z: float) -> float:
    return 1 / (1 + math.exp(-max(-30.0, min(30.0, z))))
