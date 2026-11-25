import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from trading import jarvis as J
from trading import jarvis_mini as M
from trading.state_store import DeskStateStore
from trading.trade_checker import TradeChecker


def desk(folder: Path) -> None:
    checker = TradeChecker(folder)
    checker.review(symbol="xauusd", qty=1, price=2400, desk_score=0.9, threshold=0.5, now=1_000_000)
    checker.review(symbol="eurusd", qty=-1, price=1.08, desk_score=0.2, threshold=0.5, now=1_000_100)
    checker.review(symbol="eurusd", qty=1, price=1.08, desk_score=0.1, threshold=0.5, now=1_000_200)
    DeskStateStore(folder / "desk_state.sqlite").save_namespace("memory-0", {"agents": {"a": 1}}, now=1_000_000)


class ContextTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_reads_the_real_desk_files(self):
        desk(self.dir)
        ctx = M.load_live_desk_context(self.dir, trades=[])
        audit = ctx["sentinel_audit"]
        self.assertEqual((audit["passed"], audit["failed"]), (1, 2))
        self.assertEqual(audit["recent"][0]["symbol"], "EURUSD")  # newest first
        self.assertIn("desk score", next(iter(audit["top_fail_reasons"])))
        self.assertEqual(ctx["saved_state"]["memory-0"]["snapshots"], 1)
        self.assertNotIn("payload", json.dumps(ctx["saved_state"]))  # sizes only, never the agents' memory
        gate = ctx["live_unlock_gate"]
        self.assertFalse(gate["live_unlocked"])
        self.assertEqual(gate["rules"]["max_drawdown"], 0.08)
        self.assertEqual(gate["rules"]["min_profit_factor"], 1.35)

    def test_replay_stats_from_closed_trades(self):
        trades = [{"symbol": "xauusd", "pnl": -30.0}, {"symbol": "eurusd", "pnl": 20.0}, {"symbol": "eurusd", "pnl": 25.0}]
        stats = M.load_live_desk_context(self.dir, trades=trades)["replay_stats"]["closed_trades"]
        self.assertEqual(stats["trades"], 3)
        self.assertEqual(stats["profit_factor"], 1.5)
        self.assertEqual(stats["worst_symbols"], {"XAUUSD": -30.0})

    def test_empty_folder_still_answers(self):
        ctx = M.load_live_desk_context(self.dir)
        self.assertEqual(ctx["saved_state"], {})
        self.assertIn("empty", M.BuiltinMini().answer("why did sentinel fail?", ctx))
        self.assertIn("locked", M.BuiltinMini().answer("are we ready to unlock live trading?", ctx))

    def test_snapshot_is_capped(self):
        ctx = {"sentinel_audit": {"recent": [{"verdict": "x" * 500} for _ in range(100)]}}
        self.assertLessEqual(len(M._compact(ctx)), M.MAX_CONTEXT_CHARS)


class BrainTests(unittest.TestCase):
    def test_builtin_summarises_fails(self):
        with tempfile.TemporaryDirectory() as d:
            desk(Path(d))
            reply = M.BuiltinMini().answer("Summarize my last Sentinel FAIL blocks", M.load_live_desk_context(d))
        self.assertIn("failed 2", reply)

    def test_claude_haiku_request(self):
        calls = []
        resp = SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text="Two fails.")])
        client = SimpleNamespace(messages=SimpleNamespace(create=lambda **kw: calls.append(kw) or resp))
        brain = M.ClaudeMini(client)
        self.assertEqual(brain.answer("fails?", {"a": 1}, [{"role": "user", "content": "hi"}] * 20), "Two fails.")
        kw = calls[0]
        self.assertEqual(kw["model"], "claude-haiku-5-5")
        self.assertEqual(kw["system"][0]["cache_control"], {"type": "ephemeral"})
        self.assertLessEqual(len(kw["messages"]), M.HISTORY_TURNS * 2 + 1)
        self.assertIn("LIVE DESK SNAPSHOT", kw["messages"][-1]["content"])

    def test_openai_compatible_server(self):
        sent = {}

        def opener(req, timeout):
            sent["url"], sent["body"], sent["auth"] = req.full_url, json.loads(req.data), req.get_header("Authorization")
            return io.BytesIO(json.dumps({"choices": [{"message": {"content": "Local answer."}}]}).encode())

        brain = M.OpenAICompatMini("http://localhost:11434/v1/", "qwen2.5:14b", opener=opener)
        self.assertEqual(brain.answer("hi", {}), "Local answer.")
        self.assertEqual(sent["url"], "http://localhost:11434/v1/chat/completions")
        self.assertEqual(sent["body"]["model"], "qwen2.5:14b")
        self.assertIsNone(sent["auth"])  # Ollama needs no key

    def test_make_mini_brain(self):
        self.assertIsInstance(M.make_mini_brain({}), M.BuiltinMini)
        self.assertIsInstance(M.make_mini_brain({"COPILOT_BASE_URL": "http://localhost:11434/v1"}), M.OpenAICompatMini)

    def test_falls_back_when_the_model_is_down(self):
        class Down:
            name = "mini-claude"

            def answer(self, *a):
                raise OSError("no network")
        with tempfile.TemporaryDirectory() as d:
            reply = M.MiniJarvis(d, brain=Down()).answer("ready for live?", {})
        self.assertIn("locked", reply)

    def test_jarvis_uses_the_fast_brain_and_shares_history(self):
        seen = []

        class Fast:
            name = "mini-test"

            def answer(self, q, snap, history=None):
                seen.append(history)
                return "fast"
        jarvis = J.Jarvis(None, required=False)
        jarvis.mini = Fast()
        self.assertEqual(jarvis.ask("me", "hello", {}, brain=jarvis.mini), "fast")
        self.assertNotEqual(jarvis.ask("me", "how many agents?", {}), "fast")
        jarvis.ask("me", "again", {}, brain=jarvis.mini)
        self.assertEqual(len(seen[-1]), 4)
        self.assertEqual(jarvis.status(True)["fast_brain"], "mini-test")


if __name__ == "__main__":
    unittest.main()
