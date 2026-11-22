"""Chart indicators: moving averages, Bollinger bands, RSI, MACD, ATR.

All functions take a list of floats (or candles for ATR) oldest-first and return
a list of the same length, with None where there is not enough data yet.
"""
from __future__ import annotations
import math


def sma(xs, n):
    out, s = [], 0.0
    for i, x in enumerate(xs):
        s += x
        if i >= n:
            s -= xs[i - n]
        out.append(s / n if i >= n - 1 else None)
    return out


def ema(xs, n):
    out, k, e = [], 2 / (n + 1), None
    for i, x in enumerate(xs):
        if i < n - 1:
            out.append(None)
            continue
        e = sum(xs[i - n + 1:i + 1]) / n if e is None else x * k + e * (1 - k)
        out.append(e)
    return out


def bollinger(xs, n=20, k=2.0):
    mid = sma(xs, n)
    up, lo = [], []
    for i, m in enumerate(mid):
        if m is None:
            up.append(None); lo.append(None); continue
        w = xs[i - n + 1:i + 1]
        sd = math.sqrt(sum((x - m) ** 2 for x in w) / n)
        up.append(m + k * sd); lo.append(m - k * sd)
    return {"mid": mid, "upper": up, "lower": lo}


def rsi(xs, n=14):
    out = [None] * len(xs)
    if len(xs) <= n:
        return out
    gains = losses = 0.0
    for i in range(1, n + 1):
        d = xs[i] - xs[i - 1]
        gains += max(d, 0); losses += max(-d, 0)
    ag, al = gains / n, losses / n
    out[n] = 100 if al == 0 else 100 - 100 / (1 + ag / al)
    for i in range(n + 1, len(xs)):
        d = xs[i] - xs[i - 1]
        ag = (ag * (n - 1) + max(d, 0)) / n
        al = (al * (n - 1) + max(-d, 0)) / n
        out[i] = 100 if al == 0 else 100 - 100 / (1 + ag / al)
    return out


def macd(xs, fast=12, slow=26, signal=9):
    f, s = ema(xs, fast), ema(xs, slow)
    line = [a - b if a is not None and b is not None else None for a, b in zip(f, s)]
    start = next((i for i, v in enumerate(line) if v is not None), len(line))
    sig_part = ema(line[start:], signal) if len(line) - start >= signal else [None] * (len(line) - start)
    sig = [None] * start + sig_part
    hist = [a - b if a is not None and b is not None else None for a, b in zip(line, sig)]
    return {"macd": line, "signal": sig, "hist": hist}


def atr(candles, n=14):
    trs = []
    for i, c in enumerate(candles):
        if i == 0:
            trs.append(c["h"] - c["l"])
        else:
            pc = candles[i - 1]["c"]
            trs.append(max(c["h"] - c["l"], abs(c["h"] - pc), abs(c["l"] - pc)))
    return sma(trs, n)


def last(xs):
    for x in reversed(xs):
        if x is not None:
            return x
    return None


def all_for(candles):
    closes = [c["c"] for c in candles]
    return {
        "ma20": sma(closes, 20), "ma50": sma(closes, 50), "ma200": sma(closes, 200),
        "bollinger": bollinger(closes), "rsi": rsi(closes), "macd": macd(closes),
    }
