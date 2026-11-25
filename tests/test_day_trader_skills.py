"""The day-trader skill pack is installed as project skills, complete and paper-only."""
import pathlib
import unittest

SKILLS = pathlib.Path(__file__).resolve().parent.parent / ".claude" / "skills"
NAMES = ("alerts", "analyst", "broker", "budget", "entry", "exit", "news", "pelosi", "risk", "scanner", "timemachine")


class DayTraderSkills(unittest.TestCase):
    def test_all_eleven_installed_with_frontmatter(self):
        for n in NAMES:
            md = (SKILLS / f"trader-{n}" / "SKILL.md").read_text(encoding="utf-8")
            self.assertTrue(md.startswith("---\nname: trader-" + n + "\n"), n)
            self.assertIn("description:", md.split("---")[1], n)

    def test_license_and_readme_kept(self):
        self.assertIn("MIT", (SKILLS / "DAY-TRADER-LICENSE").read_text(encoding="utf-8"))
        readme = (SKILLS / "README.md").read_text(encoding="utf-8")
        self.assertIn("paper / demo only", readme)

    def test_broker_never_orders_without_yes(self):
        md = (SKILLS / "trader-broker" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("No yes, no order", md)
        self.assertIn('"ALPACA_PAPER_TRADE": "true"', md)


if __name__ == "__main__":
    unittest.main()
