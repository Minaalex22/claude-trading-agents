"""Night Shift: the Melbourne overnight desk (mina's patch, from the "night shift" reel).

While mina sleeps (23:30 - 07:00 Melbourne time, which is about 12:30 - 20:00 UTC in summer: the London/New York
overlap and the US session), the 1,400 agents still vote as normal and every other check still runs. Night Shift
sits on top of them as an outer layer:

- a budget capsule: overnight trading is ring-fenced to NIGHT_SHIFT_BUDGET (default $1,500). No position may grow
  past 15% of the capsule, and if the night's closed trades lose NIGHT_SHIFT_STOP_PCT (default 5%) of it, the desk
  goes EXIT_ONLY until 07:00 (open trades can still close; nothing new opens);
- a toxicity gate: no new overnight risk when the spread eats more than 25% of the expected move, when the book is
  dead (activity under 35% of normal), or, for crypto feeds that carry on-chain data, when the coin looks like a
  rug (developer holdings, block-0 bundles, top-10 concentration, thin liquidity, a repeat rugger);
- quiet hours: no per-trade phone alerts during the night (trading/hooks.py should_suppress_alert);
- a 07:00 briefing: one morning summary for JARVIS, the Trades page and the phone.

It counts only the desk's own chosen trades. Practice-quota trades are agents' homework and never reach it.
Night Shift only runs where the market has real time (live prices or a history replay); the simulator has no clock.
Paper only. NIGHT_SHIFT_ENABLED (default true) switches it off.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta, timezone, tzinfo


def _first_sunday(year: int, month: int) -> datetime:
    d = datetime(year, month, 1)
    return d + timedelta(days=(6 - d.weekday()) % 7)


class _Melbourne(tzinfo):
    """Australia/Melbourne without the tz database (Windows and slim images may not have it):
    AEST +10, and AEDT +11 from the first Sunday of October 02:00 to the first Sunday of April 03:00."""

    def _dst_local(self, dt: datetime) -> bool:
        naive = dt.replace(tzinfo=None)
        start = _first_sunday(naive.year, 10) + timedelta(hours=2)
        end = _first_sunday(naive.year, 4) + timedelta(hours=3)
        return naive >= start or naive < end

    def utcoffset(self, dt):
        return timedelta(hours=11 if dt is not None and self._dst_local(dt) else 10)

    def dst(self, dt):
        return timedelta(hours=1 if dt is not None and self._dst_local(dt) else 0)

    def tzname(self, dt):
        return "AEDT" if dt is not None and self._dst_local(dt) else "AEST"

    def fromutc(self, dt):
        naive = dt.replace(tzinfo=None)
        start = _first_sunday(naive.year, 10) + timedelta(hours=2 - 10)  # 02:00 AEST in UTC
        end = _first_sunday(naive.year, 4) + timedelta(hours=3 - 11)  # 03:00 AEDT in UTC
        hours = 11 if (naive >= start or naive < end) else 10
        return (naive + timedelta(hours=hours)).replace(tzinfo=self)


try:
    from zoneinfo import ZoneInfo
    MELBOURNE_TZ = ZoneInfo("Australia/Melbourne")
except Exception:  # no tz database here
    MELBOURNE_TZ = _Melbourne()


def _on(env, key: str, default: str) -> bool:
    return str(env.get(key, default)).lower() in ("true", "1", "on", "yes")


@dataclass
class NightShiftConfig:
    enabled: bool = field(default_factory=lambda: _on(os.environ, "NIGHT_SHIFT_ENABLED", "true"))
    label: str = "Melbourne Overnight Desk (23:30 - 07:00 AEDT)"
    budget_usd: float = field(default_factory=lambda: float(os.getenv("NIGHT_SHIFT_BUDGET", "1500.0")))
    # Hard session stop: 5% for the main desk (20% only for an isolated high-beta crypto sandbox).
    stop_if_down_pct: float = field(default_factory=lambda: float(os.getenv("NIGHT_SHIFT_STOP_PCT", "0.05")))
    max_per_trade_pct: float = 0.15  # no single overnight position above 15% of the capsule
    wake_hour: int = 7  # 07:00 morning briefing
    wake_minute: int = 0
    sleep_start: time = time(23, 30)
    sleep_end: time = time(7, 0)

    @classmethod
    def from_env(cls, env=os.environ) -> "NightShiftConfig":
        return cls(enabled=_on(env, "NIGHT_SHIFT_ENABLED", "true"),
                   budget_usd=float(env.get("NIGHT_SHIFT_BUDGET", "1500.0")),
                   stop_if_down_pct=float(env.get("NIGHT_SHIFT_STOP_PCT", "0.05")))


@dataclass
class TokenOrAssetAudit:
    """Toxicity audit input for any market. On-chain fields default to safe values for normal price feeds."""
    symbol: str
    asset_class: str  # "crypto" | "forex" | "gold" | "indices" | "stocks"
    spread_pct: float
    atr_pct: float
    vol_vs_20bar: float
    dev_hold_pct: float = 0.0
    block0_bundle_pct: float = 0.0
    top10_holders_pct: float = 0.0
    repeat_rugger: bool = False
    liquidity_usd: float = 1_000_000.0


def should_skip_toxic_setup(audit: TokenOrAssetAudit) -> tuple[bool, str]:
    """(True, reason) to skip the setup, or (False, "PASS")."""
    if audit.atr_pct > 0 and (audit.spread_pct / audit.atr_pct) > 0.25:
        return True, f"skip_wide_spread ({audit.spread_pct:.4%} > 25% ATR)"
    if audit.vol_vs_20bar < 0.35:
        return True, f"skip_thin_volume (vol_ratio={audit.vol_vs_20bar:.2f})"
    if audit.asset_class.lower() == "crypto":
        if audit.repeat_rugger:
            return True, "skip_repeat_rugger"
        if audit.dev_hold_pct > 0.08:
            return True, f"skip_dev_holds_{audit.dev_hold_pct:.1%}"
        if audit.block0_bundle_pct > 0.18:
            return True, f"skip_bundle_{audit.block0_bundle_pct:.1%}"
        if audit.top10_holders_pct > 0.35:
            return True, f"skip_top10_concentration_{audit.top10_holders_pct:.1%}"
        if audit.liquidity_usd < 25_000:
            return True, f"skip_low_liquidity_${audit.liquidity_usd:,.0f}"
    return False, "PASS"


CRYPTO = {"BTC", "ETH", "SOL", "PAXG"}
FOREX = {"EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "USDCHF", "NZDUSD"}
GOLD = {"XAU", "XAUUSD", "GOLD", "XAG", "SILVER"}
INDICES = {"SPY", "QQQ", "DIA", "IWM", "US500", "NAS100", "US30"}
ACTIVE_BARS = 5  # "now" = the last 5 moves
NORMAL_BARS = 20  # "normal" = the last 20 moves
MOVE_BARS = 10  # expected move over 10 bars, as the desk's cost hurdle uses


def asset_class(symbol: str) -> str:
    s = symbol.upper()
    if s in CRYPTO:
        return "crypto"
    if s in GOLD:
        return "gold"
    if s in FOREX or (len(s) == 6 and s.isalpha() and s[3:] in ("USD", "JPY", "EUR", "GBP", "AUD")):
        return "forex"
    if s in INDICES:
        return "indices"
    return "stocks"


def audit_from_prices(symbol: str, prices, spread_pct: float) -> TokenOrAssetAudit:
    """Build the audit from the desk's own price history. The desk has no volume feed, so the book's activity
    (average size of the last 5 moves against the last 20) stands in for volume, and the expected move over
    10 bars stands in for ATR. Without enough history the audit passes (no data is not a reason to skip)."""
    p = list(prices)[-(NORMAL_BARS + 1):]
    steps = [abs(b / a - 1) for a, b in zip(p, p[1:]) if a]
    if len(steps) < NORMAL_BARS // 2:
        return TokenOrAssetAudit(symbol, asset_class(symbol), spread_pct=0.0, atr_pct=0.0, vol_vs_20bar=1.0)
    normal = sum(steps) / len(steps)
    recent = steps[-ACTIVE_BARS:]
    now = sum(recent) / len(recent)
    return TokenOrAssetAudit(symbol, asset_class(symbol), spread_pct=spread_pct,
                             atr_pct=normal * MOVE_BARS ** 0.5, vol_vs_20bar=(now / normal) if normal else 0.0)


def _local(now) -> datetime:
    if now is None:
        return datetime.now(MELBOURNE_TZ)
    if isinstance(now, (int, float)):
        return datetime.fromtimestamp(now, timezone.utc).astimezone(MELBOURNE_TZ)
    return now.astimezone(MELBOURNE_TZ)


class NightShiftEngine:
    """Ring-fences overnight paper trading inside a strict budget capsule.

    `update(now)` runs every tick with the market time: it opens a fresh capsule when the night starts, closes it
    at 07:00 and makes the morning briefing. `active` is True only inside a night session; outside it Night
    Shift changes nothing.
    """

    def __init__(self, config: NightShiftConfig | None = None) -> None:
        self.cfg = config or NightShiftConfig()
        self.start_budget: float = self.cfg.budget_usd
        self.current_balance: float = self.cfg.budget_usd
        self.halted: bool = False
        self.halt_reason: str = ""
        self.briefing_delivered_date: str = ""
        self.active: bool = False  # inside tonight's session
        self.session_date: str = ""  # the Melbourne date the night started
        self.carried: set[str] = set()  # positions open before the night started: their results aren't the capsule's
        self.briefing: dict | None = None  # the last 07:00 briefing
        self._new_briefing = False
        self._now = None
        self._reset_counters()

    def _reset_counters(self) -> None:
        # Counters (chosen desk trades only, never practice-quota trades)
        self.trades_count: int = 0
        self.wins: int = 0
        self.losses: int = 0
        self.skipped_toxic: int = 0
        self.best_trade: tuple[str, float, float] | None = None  # (symbol, pnl_pct, pnl_usd)
        self.timeline: list[str] = []

    def _stamp(self) -> str:
        return _local(self._now).strftime("%H:%M")

    # ---- the clock ----
    def is_sleep_window(self, now=None) -> bool:
        t = _local(now).time()
        return t >= self.cfg.sleep_start or t < self.cfg.sleep_end  # crosses midnight: 23:30 -> 07:00

    def is_wake_alarm_due(self, now=None) -> bool:
        local_dt = _local(now)
        today_str = local_dt.strftime("%Y-%m-%d")
        if self.briefing_delivered_date == today_str:
            return False
        if local_dt.hour == self.cfg.wake_hour and local_dt.minute >= self.cfg.wake_minute:
            self.briefing_delivered_date = today_str
            return True
        return False

    def update(self, now, open_positions=()) -> None:
        """Called every tick with the market time (None in the simulator: Night Shift stays off)."""
        if not self.cfg.enabled or now is None:
            self.active = False
            return
        self._now = now
        night = self.is_sleep_window(now)
        if night and not self.active:
            self.start_session(now, open_positions)
        elif not night and self.active:
            self.active = False
            self.timeline.append(f"{self._stamp()} - night over")
        if self.is_wake_alarm_due(now):
            self.briefing = {"date": self.briefing_delivered_date, **self.morning_summary()}
            self._new_briefing = True

    def start_session(self, now=None, open_positions=()) -> None:
        self._now = now
        self.active = True
        self.session_date = _local(now).strftime("%Y-%m-%d")
        self.start_budget = self.current_balance = self.cfg.budget_usd
        self.halted, self.halt_reason = False, ""
        self.carried = set(open_positions)
        self._reset_counters()
        self.timeline.append(f"{self._stamp()} - night shift on, ${self.start_budget:,.0f} capsule")

    def take_briefing(self) -> dict | None:
        """The 07:00 briefing, once."""
        if not self._new_briefing:
            return None
        self._new_briefing = False
        return self.briefing

    # ---- the gate ----
    def position_cap(self) -> float:
        return self.current_balance * self.cfg.max_per_trade_pct

    def check_entry_allowed(self, audit: TokenOrAssetAudit, order_value: float) -> tuple[bool, str]:
        if not self.cfg.enabled:
            return True, "night_shift_off"
        if self.halted:
            return False, f"night_shift_halted: {self.halt_reason}"
        drawdown = (self.start_budget - self.current_balance) / max(1e-8, self.start_budget)
        if drawdown >= self.cfg.stop_if_down_pct:
            self.halted = True
            self.halt_reason = f"session_stop_triggered (-{drawdown:.1%})"
            return False, self.halt_reason
        max_allowed_order = self.position_cap()
        if order_value > max_allowed_order:
            return False, f"exceeds_night_shift_cap (${order_value:.2f} > ${max_allowed_order:.2f})"
        skip, reason = should_skip_toxic_setup(audit)
        if skip:
            self.skipped_toxic += 1
            self.timeline.append(f"{self._stamp()} - skipped {audit.symbol} ({reason})")
            return False, reason
        return True, "PASS"

    def record_closed_trade(self, symbol: str, pnl_usd: float, pnl_pct: float, is_practice_quota: bool = False) -> None:
        # Strictly apart from the 20-an-hour practice quota trades.
        if is_practice_quota or not self.cfg.enabled:
            return
        if symbol in self.carried:
            self.carried.discard(symbol)  # opened before the night: not the capsule's trade
            return
        self.current_balance += pnl_usd
        self.trades_count += 1
        stamp = self._stamp()
        if pnl_usd > 0:
            self.wins += 1
            if self.best_trade is None or pnl_usd > self.best_trade[2]:
                self.best_trade = (symbol, pnl_pct, pnl_usd)
            self.timeline.append(f"{stamp} - sold {symbol} {pnl_pct:+.1%} +${pnl_usd:.2f}")
        else:
            self.losses += 1
            self.timeline.append(f"{stamp} - cut {symbol} {pnl_pct:+.1%} -${abs(pnl_usd):.2f} moving on")
        drawdown = (self.start_budget - self.current_balance) / max(1e-8, self.start_budget)
        if drawdown >= self.cfg.stop_if_down_pct and not self.halted:
            self.halted = True
            self.halt_reason = f"session_stop_triggered (-{drawdown:.1%})"
            self.timeline.append(f"{stamp} - HALTED: {self.halt_reason}")

    # ---- the reports ----
    def morning_summary(self) -> dict:
        net_usd = self.current_balance - self.start_budget
        net_pct = net_usd / max(1e-8, self.start_budget)
        win_rate = (self.wins / self.trades_count) if self.trades_count > 0 else 0.0
        best_str = (f"{self.best_trade[0]} {self.best_trade[1]:+.1%} (+${self.best_trade[2]:.2f})"
                    if self.best_trade else "None")
        return {
            "label": self.cfg.label,
            "starting_balance": round(self.start_budget, 2),
            "ending_balance": round(self.current_balance, 2),
            "net_profit_usd": round(net_usd, 2),
            "net_profit_pct": round(net_pct * 100, 2),
            "total_trades": self.trades_count,
            "wins": self.wins,
            "small_cuts_losses": self.losses,
            "win_rate_pct": round(win_rate * 100, 1),
            "toxic_skipped": self.skipped_toxic,
            "best_trade": best_str,
            "halted": self.halted,
            "halt_reason": self.halt_reason,
            "timeline_tail": self.timeline[-10:],
        }

    def status(self) -> dict:
        return {"enabled": self.cfg.enabled, "active": self.active, "session_date": self.session_date,
                "window": "23:30 - 07:00 Melbourne", "budget": self.cfg.budget_usd,
                "stop_pct": self.cfg.stop_if_down_pct, "summary": self.morning_summary(), "briefing": self.briefing}


def briefing_text(b: dict) -> str:
    """The 07:00 briefing as one short phone message."""
    text = (f"Good morning. Night Shift ({b.get('date', '')}): {b['total_trades']} trades, {b['wins']} won, "
            f"{b['small_cuts_losses']} cut, win rate {b['win_rate_pct']}%. Capsule ${b['starting_balance']:,.0f} -> "
            f"${b['ending_balance']:,.2f} ({b['net_profit_usd']:+,.2f}). Best: {b['best_trade']}. "
            f"Skipped {b['toxic_skipped']} toxic setups.")
    if b.get("halted"):
        text += f" Stopped early: {b['halt_reason']}."
    return text + " Paper money."


def merge(statuses: list[dict]) -> dict | None:
    """One view of every shard's Night Shift (each shard runs its own capsule)."""
    statuses = [s for s in statuses if s]
    if not statuses:
        return None
    sums = [s["summary"] for s in statuses]
    n = sum(x["total_trades"] for x in sums)
    wins = sum(x["wins"] for x in sums)
    start = sum(x["starting_balance"] for x in sums)
    end = sum(x["ending_balance"] for x in sums)
    timeline = sorted((t for x in sums for t in x["timeline_tail"]), reverse=True)[:10]
    briefings = [s["briefing"] for s in statuses if s.get("briefing")]
    return {"enabled": any(s["enabled"] for s in statuses), "active": any(s["active"] for s in statuses),
            "window": statuses[0]["window"], "capsules": len(statuses), "budget": start,
            "stop_pct": statuses[0]["stop_pct"],
            "summary": {"starting_balance": round(start, 2), "ending_balance": round(end, 2),
                        "net_profit_usd": round(end - start, 2), "total_trades": n, "wins": wins,
                        "small_cuts_losses": sum(x["small_cuts_losses"] for x in sums),
                        "win_rate_pct": round(100 * wins / n, 1) if n else 0.0,
                        "toxic_skipped": sum(x["toxic_skipped"] for x in sums),
                        "halted": any(x["halted"] for x in sums),
                        "halt_reason": next((x["halt_reason"] for x in sums if x["halted"]), ""),
                        "timeline_tail": timeline},
            "briefing": max(briefings, key=lambda b: b.get("date", ""), default=None)}
