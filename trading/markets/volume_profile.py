"""Volume profile: how much traded at each price. POC = busiest price,
value area (VAH/VAL) = the price range holding 70% of volume."""
from __future__ import annotations


def profile(candles, bins=40, value_area=0.70):
    if not candles:
        return {"levels": [], "poc": None, "vah": None, "val": None}
    lo = min(c["l"] for c in candles)
    hi = max(c["h"] for c in candles)
    if hi <= lo:
        hi = lo * 1.0001 + 1e-9
    size = (hi - lo) / bins
    vols = [0.0] * bins
    for c in candles:
        # spread each candle's volume evenly over the bins it touched
        a = min(int((c["l"] - lo) / size), bins - 1)
        b = min(int((c["h"] - lo) / size), bins - 1)
        share = c["v"] / (b - a + 1)
        for i in range(a, b + 1):
            vols[i] += share
    poc_i = max(range(bins), key=lambda i: vols[i])
    total = sum(vols)
    inside, a, b = vols[poc_i], poc_i, poc_i
    while total and inside / total < value_area and (a > 0 or b < bins - 1):
        up = vols[b + 1] if b < bins - 1 else -1
        dn = vols[a - 1] if a > 0 else -1
        if up >= dn:
            b += 1; inside += up
        else:
            a -= 1; inside += dn
    mid = lambda i: lo + (i + 0.5) * size
    return {
        "levels": [{"price": mid(i), "volume": vols[i]} for i in range(bins)],
        "poc": mid(poc_i), "vah": lo + (b + 1) * size, "val": lo + a * size,
    }
