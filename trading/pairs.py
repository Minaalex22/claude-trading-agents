"""Pairs trading toolkit for the /pairs page (practice only, nothing is sent anywhere).

Four tools, each with its own chart on the page:
  1. Engle-Granger test   OLS hedge ratio on log prices, then an ADF test on the residuals. Compared with
                          Engle-Granger critical values (about -3.34 at 5% for two series), not plain ADF tables.
  2. Ornstein-Uhlenbeck   AR(1) slope phi of the spread; theta = -ln(phi); half-life = ln(2) / theta.
  3. Kalman hedge ratio   state [intercept, beta] follows a random walk. Q (drift) and R (spread variance)
                          are set from the training data only.
  4. Z-score entry rule   rolling mean and std of the spread: enter at |z| > 2 (fade), take profit near
                          |z| < 0.5, stop near |z| > 4. Costs are charged on every leg.

Fit on the first half, trade the second half, and report both static OLS and Kalman results honestly,
including when they lose. Standard library only.
"""

from __future__ import annotations

import math
import random

EG_CRIT = {"1%": -3.90, "5%": -3.34, "10%": -3.04}  # Engle-Granger, 2 variables, with constant
COST_PER_LEG = 0.0005    # 0.05% each leg each side: fees, spread and borrow, charged on every change
Z_IN, Z_OUT, Z_STOP = 2.0, 0.5, 4.0
Z_WINDOW = 30
MARKET_PAIRS = [("GOLD", "SILVER"), ("BTC", "ETH"), ("SPX", "NDX"), ("AUDUSD", "COPPER"), ("OIL", "NATGAS"), ("NVDA", "MSFT")]


# ---------- maths helpers ----------

def ols(x: list[float], y: list[float]) -> tuple[float, float]:
    """y = a + b x. Returns (a, b)."""
    n = len(x)
    mx, my = sum(x) / n, sum(y) / n
    sxx = sum((v - mx) ** 2 for v in x)
    b = sum((u - mx) * (v - my) for u, v in zip(x, y)) / sxx if sxx else 0.0
    return my - b * mx, b


def adf_stat(e: list[float], lags: int = 1) -> float:
    """t-stat of gamma in de_t = c + gamma e_{t-1} + sum(d_i de_{t-i}) + u."""
    de = [e[i] - e[i - 1] for i in range(1, len(e))]
    rows, ys = [], []
    for t in range(lags + 1, len(e)):
        rows.append([1.0, e[t - 1]] + [de[t - 1 - i] for i in range(1, lags + 1)])
        ys.append(de[t - 1])
    k = len(rows[0])
    # Normal equations, solved with Gauss-Jordan (k is tiny).
    xtx = [[sum(r[i] * r[j] for r in rows) for j in range(k)] for i in range(k)]
    xty = [sum(r[i] * y for r, y in zip(rows, ys)) for i in range(k)]
    inv = _inverse(xtx)
    if inv is None:
        return 0.0
    beta = [sum(inv[i][j] * xty[j] for j in range(k)) for i in range(k)]
    resid = [y - sum(b * v for b, v in zip(beta, r)) for r, y in zip(rows, ys)]
    s2 = sum(u * u for u in resid) / max(1, len(rows) - k)
    se = math.sqrt(max(1e-300, s2 * inv[1][1]))
    return beta[1] / se


def _inverse(m: list[list[float]]) -> list[list[float]] | None:
    n = len(m)
    a = [row[:] + [1.0 if i == j else 0.0 for j in range(n)] for i, row in enumerate(m)]
    for c in range(n):
        p = max(range(c, n), key=lambda r: abs(a[r][c]))
        if abs(a[p][c]) < 1e-15:
            return None
        a[c], a[p] = a[p], a[c]
        piv = a[c][c]
        a[c] = [v / piv for v in a[c]]
        for r in range(n):
            if r != c and a[r][c]:
                f = a[r][c]
                a[r] = [v - f * w for v, w in zip(a[r], a[c])]
    return [row[n:] for row in a]


def engle_granger(lx: list[float], ly: list[float]) -> dict:
    a, b = ols(lx, ly)
    e = [y - a - b * x for x, y in zip(lx, ly)]
    stat = adf_stat(e)
    level = next((k for k in ("1%", "5%", "10%") if stat < EG_CRIT[k]), None)
    return {"alpha": a, "beta": b, "adf": stat, "spread": e, "crit": EG_CRIT,
            "cointegrated": stat < EG_CRIT["5%"], "level": level}


def ou_halflife(s: list[float]) -> dict:
    _, phi = ols(s[:-1], s[1:])
    if not 0 < phi < 1:
        return {"phi": phi, "theta": None, "half_life": None}
    theta = -math.log(phi)
    return {"phi": phi, "theta": theta, "half_life": math.log(2) / theta}


def kalman_beta(lx: list[float], ly: list[float], q: float, r: float, th0=(0.0, 0.0), p0: float = 1.0) -> dict:
    """State th = [intercept, beta] is a random walk with variance q; observation y = th0 + th1 x + noise(r)."""
    th = list(th0)
    P = [[p0, 0.0], [0.0, p0]]
    betas, alphas, innov, s_var = [], [], [], []
    for x, y in zip(lx, ly):
        P = [[P[0][0] + q, P[0][1]], [P[1][0], P[1][1] + q]]   # state drifts
        h = (1.0, x)
        e = y - (th[0] + th[1] * x)                               # innovation = spread
        Ph = (P[0][0] * h[0] + P[0][1] * h[1], P[1][0] * h[0] + P[1][1] * h[1])
        S = h[0] * Ph[0] + h[1] * Ph[1] + r
        K = (Ph[0] / S, Ph[1] / S)                                # Kalman gain
        th = [th[0] + K[0] * e, th[1] + K[1] * e]
        P = [[P[0][0] - K[0] * Ph[0], P[0][1] - K[0] * Ph[1]],
             [P[1][0] - K[1] * Ph[0], P[1][1] - K[1] * Ph[1]]]
        betas.append(th[1])
        alphas.append(th[0])
        innov.append(e)
        s_var.append(S)
    return {"beta": betas, "alpha": alphas, "innovation": innov, "S": s_var}


def rolling_z(s: list[float], n: int = Z_WINDOW) -> list[float | None]:
    out: list[float | None] = []
    for i in range(len(s)):
        if i < n - 1:
            out.append(None)
            continue
        w = s[i - n + 1:i + 1]
        m = sum(w) / n
        sd = math.sqrt(sum((v - m) ** 2 for v in w) / n)
        out.append((s[i] - m) / sd if sd > 0 else 0.0)
    return out


def pair_position(z: float | None, pos: int, z_in=Z_IN, z_out=Z_OUT, z_stop=Z_STOP) -> tuple[int, str | None]:
    """+1 = long the spread (buy Y, sell beta X), -1 = short it. Returns (new position, why it changed)."""
    if z is None:
        return pos, None
    if pos == 0:
        if z > z_in and z < z_stop:
            return -1, "enter short spread"
        if z < -z_in and z > -z_stop:
            return 1, "enter long spread"
        return 0, None
    if abs(z) >= z_stop:
        return 0, "stop"
    if (pos == 1 and z >= -z_out) or (pos == -1 and z <= z_out):
        return 0, "take profit"
    return pos, None


def backtest(lx: list[float], ly: list[float], betas: list[float], z: list[float | None]) -> dict:
    """Daily P&L of a dollar-neutral-ish spread: 1 unit of Y against beta units of X, in log-return terms."""
    pos, eq, peak, mdd = 0, 1.0, 1.0, 0.0
    rets, curve, trades, marks = [], [], [], []
    entry_i, entry_eq = None, 1.0
    for i in range(1, len(lx)):
        b = betas[i - 1]
        gross = 1 + abs(b)
        r = pos * ((ly[i] - ly[i - 1]) - b * (lx[i] - lx[i - 1])) / gross
        new, why = pair_position(z[i], pos)
        cost = 0.0
        if new != pos:
            cost = COST_PER_LEG * 2 * abs(new - pos)   # two legs, each side that changes
            if pos != 0:
                trades.append({"bars": i - entry_i, "ret": round((eq * (1 + r - cost)) / entry_eq - 1, 5), "why": why,
                               "side": "long" if pos > 0 else "short"})
            if new != 0:
                entry_i, entry_eq = i, eq * (1 + r - cost)
            marks.append({"i": i, "z": z[i], "why": why, "pos": new})
        pos = new
        day = r - cost
        rets.append(day)
        eq *= 1 + day
        peak = max(peak, eq)
        mdd = max(mdd, 1 - eq / peak)
        curve.append(eq)
    n = len(rets)
    m = sum(rets) / n if n else 0.0
    sd = math.sqrt(sum((v - m) ** 2 for v in rets) / n) if n else 0.0
    wins = [t for t in trades if t["ret"] > 0]
    return {"sharpe": round(m / sd * math.sqrt(252), 2) if sd > 0 else 0.0, "total": round(eq - 1, 4),
            "max_dd": round(mdd, 4), "trades": len(trades), "win_rate": round(len(wins) / len(trades) * 100, 1) if trades else None,
            "curve": curve, "marks": marks, "list": trades[-20:]}


# ---------- data ----------

def simulated_pair(seed: int = 11, n: int = 1500, beta: float = 1.5, half_life: float = 10.0, drift_after: int = 750) -> dict:
    """Same example as the reel: true beta 1.5, half-life 10 days, beta starts drifting after day 750."""
    rng = random.Random(seed)
    phi = math.exp(-math.log(2) / half_life)
    lx, ly, true_b = [], [], []
    x, s, b = math.log(100), 0.0, beta
    for t in range(n):
        x += rng.gauss(0.0002, 0.012)
        s = phi * s + rng.gauss(0, 0.01)
        if t >= drift_after:
            b += rng.gauss(0.0004, 0.002)
        lx.append(x)
        ly.append(0.3 + b * x + s)
        true_b.append(b)
    return {"x": lx, "y": ly, "true_beta": true_b, "t": list(range(n)), "label": "Simulated pair (seed 11)",
            "x_name": "X", "y_name": "Y", "source": "simulated", "true_half_life": half_life}


def market_pair(x_sym: str, y_sym: str) -> dict:
    from .markets import feeds
    cx, sx = feeds.candles(x_sym, "1d")
    cy, sy = feeds.candles(y_sym, "1d")
    by_t = {c["t"]: c["c"] for c in cx}
    rows = [(c["t"], by_t[c["t"]], c["c"]) for c in cy if c["t"] in by_t and by_t[c["t"]] > 0 and c["c"] > 0]
    if len(rows) < 120:  # different calendars (crypto trades weekends): line up by order instead
        n = min(len(cx), len(cy))
        rows = [(cy[-n + i]["t"], cx[-n + i]["c"], cy[-n + i]["c"]) for i in range(n)]
    return {"x": [math.log(r[1]) for r in rows], "y": [math.log(r[2]) for r in rows], "t": [r[0] for r in rows],
            "true_beta": None, "label": f"{y_sym} vs {x_sym}", "x_name": x_sym, "y_name": y_sym,
            "source": sy if sx == sy else f"{sy}/{sx}", "true_half_life": None}


# ---------- the whole study ----------

def study(data: dict) -> dict:
    lx, ly = data["x"], data["y"]
    n = len(lx)
    split = n // 2
    tx, ty = lx[:split], ly[:split]
    eg = engle_granger(tx, ty)
    ou = ou_halflife(eg["spread"])
    # Q and R from training data only. R = training residual variance. Q = the drift that makes the
    # training innovations most likely (a small grid search on the training half, never the test half).
    r_var = sum(v * v for v in eg["spread"]) / len(eg["spread"])
    q_var, best = 1e-7, -float("inf")
    for qe in range(-9, -2):
        for m in (1.0, 3.0):
            q = m * 10.0 ** qe
            f = kalman_beta(tx, ty, q=q, r=r_var, th0=(eg["alpha"], eg["beta"]), p0=1e-3)
            ll = -0.5 * sum(math.log(S) + e * e / S for e, S in zip(f["innovation"][20:], f["S"][20:]))
            if ll > best:
                q_var, best = q, ll
    kf = kalman_beta(lx, ly, q=q_var, r=r_var, th0=(eg["alpha"], eg["beta"]), p0=1e-3)

    # Spreads on the full sample: static uses the training beta; Kalman uses the beta known the day before.
    s_ols = [y - eg["alpha"] - eg["beta"] * x for x, y in zip(lx, ly)]
    s_kf = [ly[i] - (kf["alpha"][i - 1] + kf["beta"][i - 1] * lx[i]) if i else 0.0 for i in range(n)]
    z_ols, z_kf = rolling_z(s_ols), rolling_z(s_kf)
    test = slice(split, n)
    bt_ols = backtest(lx[test], ly[test], [eg["beta"]] * (n - split), z_ols[test])
    kb = kf["beta"][split - 1:n - 1]
    bt_kf = backtest(lx[test], ly[test], kb, z_kf[test])
    tb = data.get("true_beta")
    rmse = None
    if tb:
        rmse = {"kalman": round(math.sqrt(sum((kf["beta"][i] - tb[i]) ** 2 for i in range(split, n)) / (n - split)), 3),
                "ols": round(math.sqrt(sum((eg["beta"] - tb[i]) ** 2 for i in range(split, n)) / (n - split)), 3)}
    ou_test = ou_halflife(s_ols[split:])

    def thin(xs, k=2):
        return [None if v is None else round(v, 5) for v in xs[::k]] if n > 600 else [None if v is None else round(v, 5) for v in xs]
    k = 2 if n > 600 else 1
    verdict = []
    if not eg["cointegrated"]:
        verdict.append(f"Not cointegrated in training (ADF {eg['adf']:.2f} is above the 5% line {EG_CRIT['5%']}). Do not trade it.")
    if ou["half_life"] is None or ou["half_life"] > 60:
        verdict.append("Half-life too long or no mean reversion: positions would sit for months.")
    if bt_kf["sharpe"] <= 0 and bt_ols["sharpe"] <= 0:
        verdict.append("Both versions lost money after costs on the test half.")
    if not verdict:
        best = "Kalman" if bt_kf["sharpe"] > bt_ols["sharpe"] else "static OLS"
        verdict.append(f"Passes the screen. {best} did better on the unseen half. Still practice only.")
    return {
        "label": data["label"], "source": data["source"], "x_name": data["x_name"], "y_name": data["y_name"],
        "n": n, "split": split, "step": k, "t": data["t"][::k],
        "lx": thin([v - lx[0] for v in lx], k), "ly": thin([v - ly[0] for v in ly], k),
        "fit": thin([eg["alpha"] + eg["beta"] * x - ly[0] for x in lx], k),
        "eg": {"alpha": round(eg["alpha"], 4), "beta": round(eg["beta"], 4), "adf": round(eg["adf"], 2), "crit": EG_CRIT,
               "cointegrated": eg["cointegrated"], "level": eg["level"]},
        "spread": thin(s_ols, k),
        "ou": {"phi": round(ou["phi"], 4), "half_life": round(ou["half_life"], 1) if ou["half_life"] else None,
               "theta": round(ou["theta"], 4) if ou["theta"] else None, "true": data.get("true_half_life"),
               "test_half_life": round(ou_test["half_life"], 1) if ou_test["half_life"] else None},
        "kalman": {"beta": thin(kf["beta"], k), "true_beta": thin(tb, k) if tb else None, "q": q_var, "r": r_var,
                   "rmse": rmse, "innovation_sd": round(math.sqrt(sum(v * v for v in kf["innovation"][split:]) / (n - split)), 5)},
        "z": {"ols": thin(z_ols, k), "kalman": thin(z_kf, k), "in": Z_IN, "out": Z_OUT, "stop": Z_STOP, "window": Z_WINDOW},
        "backtest": {
            "ols": {kk: v for kk, v in bt_ols.items() if kk not in ("curve",)} | {"curve": [round(v, 5) for v in bt_ols["curve"][::k]]},
            "kalman": {kk: v for kk, v in bt_kf.items() if kk not in ("curve",)} | {"curve": [round(v, 5) for v in bt_kf["curve"][::k]]},
            "cost_per_leg_pct": COST_PER_LEG * 100,
        },
        "verdict": verdict,
    }


_cache: dict[str, tuple[float, dict]] = {}


def pair_study(key: str = "sim") -> dict:
    import time
    now = time.time()
    if key in _cache and now - _cache[key][0] < 600:
        return _cache[key][1]
    if key == "sim":
        out = study(simulated_pair())
    else:
        x_sym, y_sym = key.upper().split("-")
        if (x_sym, y_sym) not in MARKET_PAIRS:
            raise KeyError(f"unknown pair {key}")
        out = study(market_pair(x_sym, y_sym))
    out["pairs"] = [{"key": "sim", "label": "Simulated pair (teaching example)"}] + \
                   [{"key": f"{x}-{y}".lower(), "label": f"{y} vs {x}"} for x, y in MARKET_PAIRS]
    out["key"] = key
    _cache[key] = (now, out)
    return out
