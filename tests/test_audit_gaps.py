"""25 Nov audit: restart-safe SQLite state, brain distill export, and the wiring map."""

import json

from trading import brain_distill, integration_wiring, memory, state_store


def test_state_store_wal_and_history(tmp_path):
    s = state_store.DeskStateStore(tmp_path / "desk_state.sqlite")
    assert s.journal_mode().lower() == "wal"
    for i in range(state_store.KEEP + 3):
        s.save_namespace("memory-0", {"i": i}, now=float(i))
    assert s.load_namespace("memory-0") == {"i": state_store.KEEP + 2}
    assert s.load_namespace("memory-0", back=1) == {"i": state_store.KEEP + 1}
    assert len(s.history("memory-0")) == state_store.KEEP
    assert s.load_namespace("nothing") is None


def test_memory_falls_back_to_sqlite(tmp_path):
    path = tmp_path / "memory-0.json"
    data = {"version": memory.VERSION, "agents": {}}
    path.write_text(json.dumps(data))
    memory._mirror(path, data)
    path.write_text("{broken")  # a crash halfway through writing the JSON file
    assert memory._load(path) == data
    path.unlink()
    assert memory._load(path) == data


def _trail(sym, qty, style, regime, score=0.9, checker="PASS"):
    return {"type": "pipeline", "shard": 0, "symbol": sym, "qty": qty, "price": 100.0, "score": score,
            "stages": {"feed": "fresh", "events": "PASS", "room": "PASS", "whale": "PASS", "checker": checker,
                       "orbit": "crypto", "heat": 1.0, "night": "day"},
            "board": {"active_style": style, "regime_label": regime, "suggested_stop_r": 1.15}}


def test_brain_distill_splits_by_department(tmp_path):
    ev = [_trail("BTC", 1, "TREND", "BREAKOUT"), {"type": "closed", "shard": 0, "symbol": "BTC", "won": True, "pnl": 5},
          _trail("ETH", -1, "TREND", "CHOP"), {"type": "closed", "shard": 0, "symbol": "ETH", "won": False, "pnl": -3},
          _trail("SOL", 1, "REVERSION", "CHOP"), {"type": "closed", "shard": 0, "symbol": "SOL", "won": True, "pnl": 2},
          _trail("XRP", 1, "REVERSION", "CHOP", checker="off"), {"type": "closed", "shard": 0, "symbol": "XRP", "won": True}]
    f = tmp_path / "events-0.jsonl"
    f.write_text("".join(json.dumps(e) + "\n" for e in ev))
    summary = brain_distill.write(brain_distill.build(brain_distill.read_events([f])), tmp_path / "brains")
    assert summary["entry"]["verified_trades"] == 2 and summary["entry"]["tags"] == {"win": 1, "chop_entry": 1}
    assert summary["scalper"]["verified_trades"] == 1  # the unchecked XRP trade is left out
    dpo = [json.loads(x) for x in (tmp_path / "brains/entry/dpo.jsonl").read_text().splitlines()]
    assert any(r["chosen"].startswith("STAND ASIDE") and r["tag"] == "chop_entry" for r in dpo)
    assert (tmp_path / "brains/scalper/Modelfile").read_text().startswith("FROM ")


def test_every_wiring_step_is_plugged_in():
    assert integration_wiring.check() == {}
