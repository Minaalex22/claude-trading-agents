"""Outlook for 1 minute, 1 hour, 4 hours and 24 hours ahead.

Each outlook gives a direction, a confidence, plain-language reasons, a stop loss,
a take profit, and a "not worth the risk" warning when the edge is too small.
This is analysis for practice trading, not financial advice.
"""
from __future__ import annotations

from . import feeds, indicators as ind
from .volume_profile import profile

# horizon -> candle timeframe used to judge it
HORIZONS = {"1m": "1m", "1h": "15m", "4h": "1h", "24h": "4h"}
MIN_RR = 1.5
MIN_CONF = 0.25


def _fmt(x):
    if x is None:
        return "?"
    return f"{x:,.2f}" if abs(x) >= 1 else f"{x:.5f}"


def analyse(candles, flow_delta_share: float | None = None) -> dict:
    closes = [c["c"] for c in candles]
    price = closes[-1]
    ma20, ma50 = ind.last(ind.sma(closes, 20)), ind.last(ind.sma(closes, 50))
    r = ind.last(ind.rsi(closes))
    m = ind.macd(closes)
    hist = [h for h in m["hist"] if h is not None]
    bb = ind.bollinger(closes)
    up, lo = ind.last(bb["upper"]), ind.last(bb["lower"])
    a = ind.last(ind.atr(candles)) or price * 0.002
    vp = profile(candles[-120:])

    score, reasons = 0.0, []

    if ma20 and ma50:
        if price > ma20 > ma50:
            score += 0.30; reasons.append("Uptrend: price is above the 20 and 50 moving averages.")
        elif price < ma20 < ma50:
            score -= 0.30; reasons.append("Downtrend: price is below the 20 and 50 moving averages.")
        else:
            reasons.append("No clear trend: moving averages are mixed.")

    if len(hist) >= 2:
        if hist[-1] > 0 and hist[-1] > hist[-2]:
            score += 0.20; reasons.append("MACD momentum is rising.")
        elif hist[-1] < 0 and hist[-1] < hist[-2]:
            score -= 0.20; reasons.append("MACD momentum is falling.")

    if r is not None:
        if r > 70:
            score -= 0.15; reasons.append(f"RSI {r:.0f}: overbought, a pullback is likely.")
        elif r < 30:
            score += 0.15; reasons.append(f"RSI {r:.0f}: oversold, a bounce is likely.")
        elif r > 55:
            score += 0.10; reasons.append(f"RSI {r:.0f}: buyers in control.")
        elif r < 45:
            score -= 0.10; reasons.append(f"RSI {r:.0f}: sellers in control.")

    if up and lo:
        if price > up:
            score -= 0.10; reasons.append("Price is above the upper Bollinger band (stretched).")
        elif price < lo:
            score += 0.10; reasons.append("Price is below the lower Bollinger band (stretched).")

    if vp["poc"]:
        if price > vp["vah"]:
            score += 0.10; reasons.append(f"Trading above the value area (above {_fmt(vp['vah'])}).")
        elif price < vp["val"]:
            score -= 0.10; reasons.append(f"Trading below the value area (below {_fmt(vp['val'])}).")

    if flow_delta_share is not None:
        if flow_delta_share > 0.58:
            score += 0.15; reasons.append(f"Order flow: {flow_delta_share:.0%} of recent volume was buying.")
        elif flow_delta_share < 0.42:
            score -= 0.15; reasons.append(f"Order flow: {1 - flow_delta_share:.0%} of recent volume was selling.")

    score = max(-1.0, min(1.0, score))
    direction = "up" if score > 0.1 else "down" if score < -0.1 else "flat"
    conf = abs(score)
    sign = 1 if direction == "up" else -1
    stop = price - sign * 1.5 * a
    target = price + sign * (1.5 * a) * (1.2 + 2.0 * conf)
    risk = abs(price - stop)
    rr = abs(target - price) / risk if risk else 0
    not_worth = direction == "flat" or conf < MIN_CONF or rr < MIN_RR
    warn = None
    if not_worth:
        why = "no clear direction" if direction == "flat" else \
              "signals are weak" if conf < MIN_CONF else f"reward is only {rr:.1f}x the risk"
        warn = f"Not worth the risk right now: {why}."
    return {
        "direction": direction, "confidence": round(conf, 2), "price": price,
        "stop_loss": None if direction == "flat" else stop,
        "take_profit": None if direction == "flat" else target,
        "reward_risk": round(rr, 2) if direction != "flat" else None,
        "not_worth_the_risk": not_worth, "warning": warn, "reasons": reasons,
    }


def outlook(symbol: str, flow_delta_share: float | None = None) -> dict:
    out = {}
    for h, tf in HORIZONS.items():
        cs, src = feeds.candles(symbol, tf)
        res = analyse(cs, flow_delta_share if h in ("1m", "1h") else None)
        res["timeframe"] = tf
        res["source"] = src
        out[h] = res
    return out


def why_moving(symbol: str, flow_delta_share: float | None = None) -> list[str]:
    """Plain reasons for today's move, from price, volume and flow."""
    h1, _ = feeds.candles(symbol, "1h")
    d1, _ = feeds.candles(symbol, "1d")
    out = []
    if len(h1) >= 25:
        ch = (h1[-1]["c"] - h1[-25]["c"]) / h1[-25]["c"] * 100
        out.append(f"{'Up' if ch >= 0 else 'Down'} {abs(ch):.2f}% over the last 24 hours.")
        vol_recent = sum(c["v"] for c in h1[-6:]) / 6
        vol_avg = sum(c["v"] for c in h1[-100:-6]) / max(len(h1[-100:-6]), 1)
        if vol_avg and vol_recent > 1.5 * vol_avg:
            out.append(f"Volume is {vol_recent / vol_avg:.1f}x normal: big players are active.")
        elif vol_avg and vol_recent < 0.6 * vol_avg:
            out.append("Volume is light, so the move has little conviction.")
    if len(d1) >= 21:
        hi20 = max(c["h"] for c in d1[-21:-1]); lo20 = min(c["l"] for c in d1[-21:-1])
        px = d1[-1]["c"]
        if px > hi20:
            out.append(f"Broke above its 20-day high ({_fmt(hi20)}).")
        elif px < lo20:
            out.append(f"Broke below its 20-day low ({_fmt(lo20)}).")
    if flow_delta_share is not None:
        out.append(f"Recent trades: {flow_delta_share:.0%} buying, {1 - flow_delta_share:.0%} selling.")
    if not out:
        out.append("Quiet: no strong driver in the price or volume data.")
    return out
