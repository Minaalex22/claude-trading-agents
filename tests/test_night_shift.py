from datetime import datetime
from zoneinfo import ZoneInfo

from trading import hooks, jarvis
from trading.alerts import Alerts
from trading.broker import PaperBroker
from trading.night_shift import (NightShiftConfig, NightShiftEngine, TokenOrAssetAudit, audit_from_prices,
                                 briefing_text, merge)
from trading.risk import RiskLimits, RiskManager

MELB = ZoneInfo("Australia/Melbourne")
NIGHT = datetime(2026, 11, 24, 1, 15, tzinfo=MELB).timestamp()
DAY = datetime(2026, 11, 24, 12, 0, tzinfo=MELB).timestamp()
WAKE = datetime(2026, 11, 24, 7, 1, tzinfo=MELB).timestamp()


# ---- mina's two tests, as given ----

def test_night_shift_budget_cap_and_session_kill_switch():
    cfg = NightShiftConfig(enabled=True, budget_usd=1500.0, stop_if_down_pct=0.20)
    ns = NightShiftEngine(cfg)

    clean_tok = TokenOrAssetAudit("PAXG", "gold", spread_pct=0.0004, atr_pct=0.005, vol_vs_20bar=1.2)
    # Exceeds 15% per-trade capsule cap ($225)
    ok, reason = ns.check_entry_allowed(clean_tok, order_value=400.0)
    assert not ok and "exceeds_night_shift_cap" in reason

    # Lose $300 (-20% of $1,500) -> trips hard session stop
    ns.record_closed_trade("BTC", pnl_usd=-300.0, pnl_pct=-0.20)
    ok2, reason2 = ns.check_entry_allowed(clean_tok, order_value=150.0)
    assert not ok2 and "session_stop_triggered" in reason2


def test_night_shift_skips_bundle_and_wide_spread_and_tracks_0700_alarm():
    ns = NightShiftEngine(NightShiftConfig(enabled=True))
    bundled = TokenOrAssetAudit(
        "SBUNLK", "crypto", spread_pct=0.01, atr_pct=0.10, vol_vs_20bar=2.0, block0_bundle_pct=0.38
    )
    ok, reason = ns.check_entry_allowed(bundled, order_value=100.0)
    assert not ok and "skip_bundle" in reason
    assert ns.skipped_toxic == 1

    sleep_dt = datetime(2026, 11, 24, 1, 15, tzinfo=MELB)
    wake_dt = datetime(2026, 11, 24, 7, 1, tzinfo=MELB)
    assert ns.is_sleep_window(sleep_dt) is True
    assert ns.is_wake_alarm_due(sleep_dt) is False
    assert ns.is_wake_alarm_due(wake_dt) is True
    assert ns.is_wake_alarm_due(wake_dt) is False  # Fires once per morning


# ---- the wiring ----

def _risk(stop=0.05):
    broker = PaperBroker()
    ns = NightShiftEngine(NightShiftConfig(enabled=True, budget_usd=1500.0, stop_if_down_pct=stop))
    return RiskManager(RiskLimits(), broker, night_shift=ns), broker, ns


def test_on_by_default_with_env_switch():
    assert NightShiftConfig.from_env({}).enabled is True
    assert NightShiftConfig.from_env({"NIGHT_SHIFT_ENABLED": "false"}).enabled is False


def test_daytime_and_simulator_change_nothing():
    risk, broker, ns = _risk()
    risk.update({"BTC": 100.0}, now=DAY)
    assert not ns.active and risk.approve("BTC", 40.0, {"BTC": 100.0}) == (40.0, None)
    risk.update({"BTC": 100.0}, now=None)  # the simulator has no clock
    assert not ns.active


def test_overnight_positions_capped_at_15_percent_of_capsule():
    risk, broker, ns = _risk()
    risk.update({"BTC": 100.0}, now=NIGHT)
    assert ns.active
    qty, reason = risk.approve("BTC", 40.0, {"BTC": 100.0})  # wants $4,000
    assert abs(qty * 100.0 - 225.0) < 1e-6 and reason == "night shift cap"
    broker.fill("BTC", qty, 100.0)
    assert risk.approve("BTC", 5.0, {"BTC": 100.0})[0] == 0.0  # already at the cap
    assert risk.approve("BTC", -1.0, {"BTC": 100.0})[0] == -1.0  # exits always pass


def test_night_stop_goes_exit_only_until_0700():
    risk, broker, ns = _risk()
    broker.fill("ETH", 1.0, 100.0)  # carried from the day: its result isn't the capsule's
    risk.update({"BTC": 100.0, "ETH": 100.0}, now=NIGHT)
    ns.record_closed_trade("ETH", -500.0, -0.5)
    assert not ns.halted and ns.trades_count == 0
    ns.record_closed_trade("BTC", -80.0, -0.05)  # -5.3% of $1,500
    assert ns.halted and risk.mode == "EXIT_ONLY" and risk.stop_reason.startswith("night shift stop")
    broker.fill("BTC", 1.0, 100.0)
    assert risk.approve("BTC", 1.0, {"BTC": 100.0, "ETH": 100.0})[0] == 0.0
    assert risk.approve("BTC", -1.0, {"BTC": 100.0, "ETH": 100.0})[0] == -1.0
    risk.update({"BTC": 100.0, "ETH": 100.0}, now=WAKE)
    assert not ns.active and risk.mode == "NORMAL"
    brief = ns.take_briefing()
    assert brief and brief["halted"] and brief["total_trades"] == 1 and ns.take_briefing() is None
    assert "Night Shift" in briefing_text(brief)


def test_practice_quota_trades_never_count():
    ns = NightShiftEngine(NightShiftConfig(enabled=True))
    ns.record_closed_trade("BTC", -1000.0, -0.5, is_practice_quota=True)
    assert ns.trades_count == 0 and not ns.halted


def test_toxicity_gate_overnight_blocks_dead_book():
    risk, broker, ns = _risk()
    risk.update({"BTC": 100.0}, now=NIGHT)
    flat = [100.0 + (i % 2) * 0.5 for i in range(16)] + [100.0] * 5  # moving, then dead
    audit = audit_from_prices("BTC", flat, 0.0001)
    qty, reason = risk.approve("BTC", 1.0, {"BTC": 100.0}, audit=audit)
    assert qty == 0.0 and "skip_thin_volume" in reason and ns.skipped_toxic == 1
    assert audit_from_prices("BTC", [100.0, 101.0], 0.0001).vol_vs_20bar == 1.0  # too little history passes


def test_quiet_hours_hold_single_trade_alerts():
    ns = NightShiftEngine(NightShiftConfig(enabled=True))
    assert hooks.should_suppress_alert(ns, NIGHT) and not hooks.should_suppress_alert(ns, DAY)
    sent = []
    a = Alerts("t", "c", post=lambda url, data: sent.append(data["text"]), start_thread=False)
    quiet = [True]
    a.quiet = lambda: quiet[0]
    order = {"status": "filled", "side": "buy", "qty": 1, "symbol": "BTC"}
    assert a.result(order) is False and a.held
    quiet[0] = False
    assert a.result({**order, "side": "sell"}) is True and not a.held
    a.flush()
    assert sent and sent[0].startswith("While you slept")


def test_jarvis_answers_how_night_shift_went():
    ns = NightShiftEngine(NightShiftConfig(enabled=True))
    ns.update(NIGHT)
    ns.record_closed_trade("BTC", 30.0, 0.02)
    ns.update(WAKE)
    merged = merge([ns.status()])
    snap = jarvis.snapshot({}, {"night_shift": merged})
    assert snap["night_shift"]["briefing"]["wins"] == 1
    reply = jarvis.BuiltinBrain().answer("how did night shift go?", snap)
    assert "1 trades, 1 won" in reply
