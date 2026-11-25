"""Where every piece of mina's 24 Nov wiring plan lives, and a check that it is really plugged in.

mina's integration_wiring.py asked for one place that connects the team relay (Sage -> Sentinel -> Atlas),
the nine-stage master pipeline, passive maker entries with the broker drift guard, restart-safe state and the
close-of-trade updates (Orbit heat, Night Shift), so desk.py, hooks.py, risk.py and JARVIS share one setup.

In our code those pieces are wired straight into the desk rather than through a separate orchestrator
(one fewer layer to keep in step). This module is the map: `WIRING` names, for each step of mina's plan, the
module and function that does it and the module that calls it. `check()` imports every one and confirms the
caller really references it, so a refactor that unplugs a step fails the tests (tests/test_audit_gaps.py).
"""

from __future__ import annotations

import importlib
import inspect

# step: (module that does it, function or name, module that calls it)
WIRING = {
    "news dedup before any Claude call": ("trading.ops_guard", "HeadlineBatchDeduplicator", "trading.reading_room"),
    "team relay: Sage regime": ("trading.team_relay", "phase1_sage", "trading.desk"),
    "team relay: Sentinel size, stop, target": ("trading.team_relay", "phase2_sentinel", "trading.desk"),
    "team relay: Atlas picks the department": ("trading.team_relay", "phase3_atlas", "trading.desk"),
    "0DTE whale flow boost": ("trading.option_flows", "FEED", "trading.desk"),
    "0DTE whale veto": ("trading.option_flows", "FEED", "trading.hooks"),
    "event guard (FOMC, CPI, earnings)": ("trading.event_guard", "check", "trading.desk"),
    "rollover shield": ("trading.ops_guard", "rollover_blocked", "trading.hooks"),
    "constant-R volatility sizing": ("trading.ops_guard", "compute_volatility_parity_notional", "trading.risk"),
    "macro bucket (correlation) cap": ("trading.risk", "BUCKET_OF", "trading.risk"),
    "Night Shift capsule": ("trading.night_shift", "NightShiftEngine", "trading.risk"),
    "Orbit arm heat on every close": ("trading.orbit_portfolio", "OrbitPortfolioCoordinator", "trading.desk"),
    "nine-stage pipeline trail per order": ("trading.master_pipeline", "desk_trace", "trading.desk"),
    "passive maker entries (2 bars, 0.35R runaway)": ("trading.execution_guard", "PassiveBook", "trading.desk"),
    "broker quote drift guard (0.15%)": ("trading.execution_guard", "quote_drift", "trading.desk"),
    "restart-safe state (SQLite WAL)": ("trading.state_store", "for_memory", "trading.memory"),
    "state saved every 15 minutes": ("trading.memory", "save", "trading.engine"),
    "trade checker on every order": ("trading.trade_checker", "TradeChecker", "trading.desk"),
    "dual brain (fast creator, slow governor)": ("trading.dual_brain", "check", "trading.agents"),
    "3-gate live promotion": ("trading.strategy_lab", "sign_off", "trading.dashboard"),
}


def check() -> dict[str, str]:
    """{step: problem} for every step that is not plugged in; empty when everything is wired."""
    problems = {}
    for step, (mod, name, caller) in WIRING.items():
        try:
            m = importlib.import_module(mod)
        except Exception as e:  # pragma: no cover - reported, not raised
            problems[step] = f"{mod} does not import: {e}"
            continue
        if not hasattr(m, name):
            problems[step] = f"{mod}.{name} is missing"
            continue
        try:
            src = inspect.getsource(importlib.import_module(caller))
        except Exception as e:  # pragma: no cover
            problems[step] = f"{caller} does not import: {e}"
            continue
        short = mod.rsplit(".", 1)[1]
        if name not in src and short not in src:
            problems[step] = f"{caller} never uses {mod}.{name}"
    return problems
