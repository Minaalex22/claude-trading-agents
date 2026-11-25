"""Net-USD exposure guard: five Orbit arms must not become one big dollar bet.

mina's safeguard (25 Nov 2026). On CPI, FOMC or RBA days, EUR/USD, AUD/USD, gold, US shares and crypto all move
against the US dollar together, so five "separate" 1% trades are really one 5% short-dollar bet. Each symbol gets a
USD beta (+1 = gains when the dollar falls, -1 = gains when it rises). Before new desk risk, the desk adds up
beta x side x risk (as a share of equity, risk = position size x stop distance) over every open position plus the
candidate. If that net dollar risk would pass MAX_NET_USD_RISK (2.5%) the entry is blocked. Trims, exits and
trades that reduce the net dollar risk always go. Env CORRELATION_GUARD=off turns it off. Paper only.
"""

from __future__ import annotations

ENABLED = True
MAX_NET_USD_RISK = 0.025  # 2.5% of equity on the dollar factor, either way

USD_BETA: dict[str, float] = {
    "EURUSD": 1.0, "GBPUSD": 1.0, "AUDUSD": 1.0, "NZDUSD": 1.0,
    "USDJPY": -1.0, "USDCAD": -1.0, "USDCHF": -1.0,
    "GOLD": 0.85, "XAUUSD": 0.85, "PAXG": 0.85, "SILVER": 0.8, "XAGUSD": 0.8,
    "SPX": 0.6, "SPY": 0.6, "QQQ": 0.65, "NDX": 0.65, "NVDA": 0.65, "TSLA": 0.65, "AMD": 0.65,
    "AAPL": 0.55, "MSFT": 0.55, "AMZN": 0.55, "META": 0.55, "GOOG": 0.55, "GOOGL": 0.55,
    "BTC": 0.7, "ETH": 0.7, "SOL": 0.7,
}


def beta(symbol: str) -> float:
    s = str(symbol).upper().replace("-USD", "").replace("-USDT", "").replace("/", "").replace("=X", "")
    return USD_BETA.get(s, 0.0)


def net_usd_risk(positions: list[dict]) -> float:
    """positions: [{"symbol", "side" ("BUY"/"SELL") or signed "qty", "risk_pct"}] -> signed net dollar-factor risk."""
    total = 0.0
    for p in positions:
        side = p.get("side")
        d = (1.0 if str(side).upper() == "BUY" else -1.0) if side else (1.0 if float(p.get("qty", 0)) > 0 else -1.0)
        total += beta(p.get("symbol", "")) * d * float(p.get("risk_pct", 0.0))
    return total


def check(candidate: dict, open_positions: list[dict], cap: float | None = None) -> tuple[bool, str]:
    """(allowed, reason). A candidate that shrinks the net dollar risk is always allowed."""
    cap = MAX_NET_USD_RISK if cap is None else cap
    before = net_usd_risk(open_positions)
    after = before + net_usd_risk([candidate])
    if abs(after) > cap and abs(after) > abs(before):
        return False, (f"blocked: dollar cluster risk {after:+.2%} would pass +/-{cap:.1%} "
                       f"({'short' if after > 0 else 'long'} USD across forex, gold, shares and crypto)")
    return True, "PASS"
