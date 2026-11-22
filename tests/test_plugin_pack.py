"""The trading-pack Claude Code plugin: manifests are valid and the real-money guard blocks."""
import json
import re
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PACK = ROOT / "plugins" / "trading-pack"
GUARD = PACK / "hooks" / "real_money_guard.py"


def frontmatter(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    match = re.match(r"---\n(.*?)\n---\n", text, re.S)
    assert match, f"{path} has no frontmatter"
    return dict(line.split(":", 1) for line in match.group(1).splitlines() if ":" in line)


def run_guard(tool_name: str, tool_input: dict) -> int:
    event = json.dumps({"hook_event_name": "PreToolUse", "tool_name": tool_name, "tool_input": tool_input})
    return subprocess.run([sys.executable, str(GUARD)], input=event, capture_output=True, text=True).returncode


class PluginPackTest(unittest.TestCase):
    def test_marketplace_points_at_plugin(self):
        market = json.loads((ROOT / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8"))
        plugin = json.loads((PACK / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
        entry = next(p for p in market["plugins"] if p["name"] == "trading-pack")
        self.assertEqual((ROOT / entry["source"]).resolve(), PACK.resolve())
        self.assertEqual(entry["version"], plugin["version"])
        self.assertEqual(plugin["name"], "trading-pack")

    def test_skills_agents_commands_have_frontmatter(self):
        skills = sorted(PACK.glob("skills/*/SKILL.md"))
        self.assertEqual(len(skills), 5)
        for path in skills:
            meta = frontmatter(path)
            self.assertEqual(meta["name"].strip(), path.parent.name)
            self.assertTrue(meta["description"].strip())
        for path in list(PACK.glob("agents/*.md")) + list(PACK.glob("commands/*.md")):
            self.assertTrue(frontmatter(path)["description"].strip(), path)

    def test_hooks_json_is_valid(self):
        hooks = json.loads((PACK / "hooks" / "hooks.json").read_text(encoding="utf-8"))
        self.assertIn("PreToolUse", hooks["hooks"])

    def test_guard_blocks_real_money(self):
        self.assertEqual(run_guard("Edit", {"new_string": "LIVE_TRADING_ENABLED = True"}), 2)
        self.assertEqual(run_guard("Bash", {"command": "export BRIDGE_REAL_MONEY=I-ACCEPT-THE-RISK"}), 2)
        self.assertEqual(run_guard("Write", {"content": "ETORO_MODE=live\n"}), 2)
        self.assertEqual(run_guard("MultiEdit", {"edits": [{"new_string": "LIVE_TRADING_ENABLED=True"}]}), 2)

    def test_guard_allows_paper_work(self):
        self.assertEqual(run_guard("Edit", {"new_string": "LIVE_TRADING_ENABLED = False"}), 0)
        self.assertEqual(run_guard("Bash", {"command": "python -m unittest"}), 0)
        self.assertEqual(run_guard("Write", {"content": "ETORO_MODE=demo\n"}), 0)


if __name__ == "__main__":
    unittest.main()
