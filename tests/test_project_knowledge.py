import io
import json
import unittest
from types import SimpleNamespace

from trading import jarvis as J
from trading import jarvis_mini as M
from trading import project_knowledge as K

DOCS = ("00-latest-status", "whole-system-blueprint", "agents-behaviour-blueprint", "how-the-brains-work",
        "how-the-agents-work", "how-news-is-read", "how-market-data-is-read", "claude-11-functions-blueprint",
        "day-trader-11-skills-blueprint", "every-request-checklist-2026-11-25")


class KnowledgeTests(unittest.TestCase):
    def test_every_project_document_is_loaded(self):
        names = [n for n, _ in K.files()]
        for doc in DOCS:
            self.assertIn(doc, names)
        text = K.text()
        for fact in ("1,400 agents", "/orbit", "MetaTrader 5", "Atlas", "Sentinel", "1.72", "USDJPY", "GO LIVE"):
            self.assertIn(fact, text)
        self.assertNotIn("![", text)  # images stay in project files

    def test_no_secrets_in_the_knowledge(self):
        text = K.text().lower()
        for bad in ("sk-ant-", "api_key=", "password=", "bearer "):
            self.assertNotIn(bad, text)

    def test_search_finds_the_right_section(self):
        titles = " ".join(t for t, _ in K.search("how does the news pipeline gate work"))
        self.assertIn("News", titles)
        self.assertLessEqual(len(K.search_text("agents", max_chars=2000)), 2200)


class JarvisAnswersProjectQuestions(unittest.TestCase):
    """The built-in brain (no key, no network) must answer the questions mina asks about the project."""

    def ask(self, q):
        return J.BuiltinBrain().answer(q, {})

    def test_unlock_rules(self):
        reply = self.ask("Are we ready to go live with real money?")
        for fact in ("50", "60 percent", "1.35", "8 percent", "sign-off"):
            self.assertIn(fact, reply)

    def test_latest_results(self):
        reply = self.ask("What are the latest results on real prices?")
        self.assertIn("2.56", reply)
        self.assertIn("USDJPY", reply)

    def test_parked_items(self):
        reply = self.ask("What's parked, what do I need to do?")
        self.assertIn("ATAS", reply)
        self.assertIn("MetaApi", reply)

    def test_bridges_pages_and_sleeves(self):
        self.assertIn("Polymarket", self.ask("what's the bridge status?"))
        self.assertIn("Orbit", self.ask("what pages are there?"))
        self.assertIn("37.5 percent", self.ask("how are the orbit sleeves split?"))

    def test_how_parts_of_the_system_work(self):
        self.assertIn("Sentinel", self.ask("What does Sentinel do every hour?"))
        self.assertIn("triage", self.ask("How does the news pipeline gate work?").lower())

    def test_status_still_comes_from_the_live_snapshot(self):
        self.assertIn("haven't reported", self.ask("how is the desk doing?"))


class BrainsCarryTheKnowledge(unittest.TestCase):
    def test_claude_jarvis_sends_the_knowledge_cached(self):
        calls = []
        resp = SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text="ok")])
        client = SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(create=lambda **kw: calls.append(kw) or resp)))
        J.ClaudeBrain(client).answer("what is parked?", {})
        block = calls[0]["system"][-1]
        self.assertEqual(block["cache_control"], {"type": "ephemeral"})
        self.assertIn("PROJECT KNOWLEDGE", block["text"])
        self.assertIn("whole-system-blueprint", block["text"])

    def test_mini_jarvis_haiku_sends_the_knowledge_cached(self):
        calls = []
        resp = SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text="ok")])
        client = SimpleNamespace(messages=SimpleNamespace(create=lambda **kw: calls.append(kw) or resp))
        M.ClaudeMini(client).answer("results?", {})
        block = calls[0]["system"][-1]
        self.assertEqual(block["cache_control"], {"type": "ephemeral"})
        self.assertIn("00-latest-status", block["text"])

    def test_local_model_gets_only_the_matching_sections(self):
        sent = {}

        def opener(req, timeout):
            sent["body"] = json.loads(req.data)
            return io.BytesIO(json.dumps({"choices": [{"message": {"content": "ok"}}]}).encode())
        M.OpenAICompatMini("http://localhost:11434/v1", "qwen2.5:14b", opener=opener).answer("news pipeline gate", {})
        system = sent["body"]["messages"][0]["content"]
        self.assertIn("PROJECT KNOWLEDGE", system)
        self.assertLess(len(system), len(K.text()) // 4)

    def test_builtin_mini_answers_project_questions(self):
        self.assertIn("2.56", M.BuiltinMini().answer("what are the latest results?", {}))


if __name__ == "__main__":
    unittest.main()
