"""Trading calculator: position size, pip value, profit or loss, risk:reward and margin. PRACTICE ONLY.

Covers the 25 products in the Markets section (forex, gold and commodities, indices, crypto, shares) and checks
every answer against the owner's money rules (trading/money_rules.py): small risk per trade, a stop and a target
first, targets about 2.4x the stop, a 2% daily loss stop. Half-Kelly is shown only as a ceiling, never a size.
Contract sizes and leverage are typical CFD broker values (AU retail leverage caps); a broker's own may differ.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .markets import catalog

DEFAULT_RISK_PCT = 0.5        # % of balance risked per trade
RULE_MAX_PCT = 1.0            # money rules: lower of 1% of balance ...
RULE_MAX_USD = 10.0           # ... and $10 until the owner raises it
DEFAULT_RR = 2.4              # money rules: target 2.4x the stop
DAILY_STOP_PCT = 2.0          # money rules: 2% daily loss, stop for the day
MARGIN_WARN = 0.5             # warn when one trade ties up half the balance


@dataclass(frozen=True)
class Spec:
    contract: float     # units in 1 lot
    pip: float          # one pip / point, in price
    lot_step: float     # smallest size step, in lots
    leverage: int       # default max leverage (AU retail caps)
    quote: str          # currency the price is quoted in
    unit: str           # what one unit is


FOREX = dict(contract=100_000, lot_step=0.01, leverage=30, unit="currency units")
SPECS: dict[str, Spec] = {
    "EURUSD": Spec(pip=0.0001, quote="USD", **FOREX),
    "GBPUSD": Spec(pip=0.0001, quote="USD", **FOREX),
    "AUDUSD": Spec(pip=0.0001, quote="USD", **FOREX),
    "USDJPY": Spec(pip=0.01, quote="JPY", **FOREX),
    "USDCAD": Spec(pip=0.0001, quote="CAD", **FOREX),
    "GOLD": Spec(100, 0.1, 0.01, 20, "USD", "ounces"),
    "SILVER": Spec(5_000, 0.01, 0.01, 10, "USD", "ounces"),
    "OIL": Spec(1_000, 0.01, 0.01, 10, "USD", "barrels"),
    "NATGAS": Spec(10_000, 0.001, 0.01, 10, "USD", "MMBtu"),
    "COPPER": Spec(25_000, 0.0005, 0.01, 10, "USD", "pounds"),
    "SPX": Spec(1, 1, 0.1, 20, "USD", "index points"),
    "NDX": Spec(1, 1, 0.1, 20, "USD", "index points"),
    "DJI": Spec(1, 1, 0.1, 20, "USD", "index points"),
    "ASX200": Spec(1, 1, 0.1, 20, "AUD", "index points"),
    "DAX": Spec(1, 1, 0.1, 20, "EUR", "index points"),
    "BTC": Spec(1, 1, 0.0001, 2, "USD", "coins"),
    "ETH": Spec(1, 0.1, 0.001, 2, "USD", "coins"),
    "SOL": Spec(1, 0.01, 0.01, 2, "USD", "coins"),
    "XRP": Spec(1, 0.0001, 1, 2, "USD", "coins"),
    "DOGE": Spec(1, 0.0001, 1, 2, "USD", "coins"),
}
for _p in catalog.PRODUCTS:
    if _p.kind == "share":
        SPECS[_p.symbol] = Spec(1, 0.01, 1, 5, "USD", "shares")


def usd_per_quote(quote: str, price: float, fx: dict | None = None) -> float:
    """How many USD one unit of the quote currency is worth."""
    fx = fx or {}
    if quote == "USD":
        return 1.0
    if quote == "JPY":
        return 1.0 / (fx.get("USDJPY") or (price if price > 20 else catalog.get("USDJPY").base_price))
    if quote == "CAD":
        return 1.0 / (fx.get("USDCAD") or (price if price < 5 else catalog.get("USDCAD").base_price))
    if quote == "AUD":
        return fx.get("AUDUSD") or catalog.get("AUDUSD").base_price
    if quote == "EUR":
        return fx.get("EURUSD") or catalog.get("EURUSD").base_price
    raise ValueError(f"no rate for {quote}")


def specs() -> list[dict]:
    out = []
    for p in catalog.PRODUCTS:
        s = SPECS[p.symbol]
        out.append({"symbol": p.symbol, "name": p.name, "kind": p.kind, "contract": s.contract, "pip": s.pip,
                    "lot_step": s.lot_step, "leverage": s.leverage, "quote": s.quote, "unit": s.unit,
                    "price": p.base_price})
    return out


def _num(v, name: str, lo: float | None = None, hi: float | None = None, required: bool = True) -> float | None:
    if v is None or v == "":
        if required:
            raise ValueError(f"{name} is needed")
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be a number") from None
    if not math.isfinite(x) or (lo is not None and x < lo) or (hi is not None and x > hi):
        raise ValueError(f"{name} is out of range")
    return x


def _floor_step(x: float, step: float) -> float:
    return math.floor(x / step + 1e-9) * step


def calculate(symbol: str, balance, entry, stop, target=None, risk_pct=DEFAULT_RISK_PCT, leverage=None,
              win_rate=None, fx: dict | None = None) -> dict:
    """Everything a trader needs before an order, from balance, risk %, entry, stop and (optional) target."""
    symbol = (symbol or "").upper()
    p = catalog.get(symbol)
    s = SPECS[p.symbol]
    balance = _num(balance, "balance", 1, 1e9)
    entry = _num(entry, "entry price", 0)
    stop = _num(stop, "stop loss", 0)
    risk_pct = _num(risk_pct, "risk %", 0.01, 10)
    leverage = _num(leverage, "leverage", 1, 500, required=False) or s.leverage
    target = _num(target, "target", 0, required=False)
    win_rate = _num(win_rate, "win rate", 1, 99, required=False)
    if entry <= 0 or stop <= 0:
        raise ValueError("prices must be above zero")
    if stop == entry:
        raise ValueError("stop loss can't equal the entry")
    side = "buy" if stop < entry else "sell"
    sign = 1 if side == "buy" else -1
    stop_dist = abs(entry - stop)
    target_given = target is not None
    if not target_given:
        target = entry + sign * stop_dist * DEFAULT_RR
    if target <= 0 or sign * (target - entry) <= 0:
        raise ValueError(f"target must be {'above' if side == 'buy' else 'below'} the entry for a {side}")
    target_dist = abs(target - entry)

    conv = usd_per_quote(s.quote, entry, fx)
    usd_per_unit_move = conv                       # USD made per unit held per 1.0 of price
    risk_usd = balance * risk_pct / 100
    units_exact = risk_usd / (stop_dist * usd_per_unit_move)
    lots = _floor_step(units_exact / s.contract, s.lot_step)
    too_small = lots <= 0
    units = lots * s.contract
    loss_usd = units * stop_dist * usd_per_unit_move
    win_usd = units * target_dist * usd_per_unit_move
    notional_usd = units * entry * conv
    margin_usd = notional_usd / leverage
    pip_value_lot = s.pip * s.contract * usd_per_unit_move
    rr = target_dist / stop_dist
    breakeven = 1 / (1 + rr)

    rule_cap = min(balance * RULE_MAX_PCT / 100, RULE_MAX_USD)
    daily_stop = balance * DAILY_STOP_PCT / 100
    checks = [
        {"rule": "risk per trade", "ok": loss_usd <= rule_cap + 1e-9,
         "detail": f"risking ${loss_usd:,.2f}; money rules allow the lower of 1% and $10 = ${rule_cap:,.2f}"},
        {"rule": "target 2.4x the stop", "ok": rr >= DEFAULT_RR - 1e-9, "detail": f"this trade is 1:{rr:.2f}"},
        {"rule": "2% daily loss", "ok": loss_usd <= daily_stop,
         "detail": (f"{int(daily_stop // loss_usd)} full stops before the day ends" if loss_usd > 0
                    else "no size yet")},
        {"rule": "margin", "ok": margin_usd <= balance * MARGIN_WARN,
         "detail": f"ties up {margin_usd / balance:.0%} of the balance"},
    ]

    kelly = None
    if win_rate is not None:
        w = win_rate / 100
        full = w - (1 - w) / rr
        half = max(0.0, full / 2)
        kelly = {"win_rate": win_rate, "full_pct": round(full * 100, 2), "half_pct": round(half * 100, 2),
                 "edge": full > 0,
                 "note": ("no edge at this win rate and reward: skip the trade" if full <= 0 else
                          "half-Kelly is a ceiling only; the money rules cap is lower" if half * 100 > risk_pct
                          else "your risk is inside half-Kelly")}

    def r(x, n=2):
        return round(x, n)

    return {
        "symbol": p.symbol, "name": p.name, "kind": p.kind, "side": side, "quote": s.quote,
        "inputs": {"balance": balance, "risk_pct": risk_pct, "entry": entry, "stop": stop, "target": target,
                   "target_given": target_given, "leverage": leverage},
        "size": {"lots": r(lots, 4), "units": r(units, 4), "unit": s.unit, "contract": s.contract,
                 "lot_step": s.lot_step, "too_small": too_small,
                 "exact_lots": r(units_exact / s.contract, 6)},
        "risk_usd": r(risk_usd), "loss_usd": r(loss_usd), "win_usd": r(win_usd),
        "stop_pips": r(stop_dist / s.pip, 1), "target_pips": r(target_dist / s.pip, 1), "pip": s.pip,
        "pip_value_lot_usd": r(pip_value_lot, 4), "pip_value_usd": r(pip_value_lot * lots, 4),
        "rr": r(rr), "breakeven_win_rate": r(breakeven * 100, 1),
        "notional_usd": r(notional_usd), "margin_usd": r(margin_usd), "leverage": leverage,
        "effective_leverage": r(notional_usd / balance, 2),
        "rule_cap_usd": r(rule_cap), "rule_size_lots": r(_floor_step(rule_cap / (stop_dist * usd_per_unit_move)
                                                                   / s.contract, s.lot_step), 4),
        "checks": checks, "kelly": kelly, "real_money": "locked",
    }
