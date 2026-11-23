"""One look on every page: the same menu, the menu script, and a page title."""
import re
import unittest
from pathlib import Path

WEB = Path(__file__).resolve().parent.parent / "trading" / "web"
PAGES = sorted(p for p in WEB.glob("*.html") if p.stem not in ("login", "offline"))


class SameDesignEverywhere(unittest.TestCase):
    def test_every_page_has_the_same_menu(self):
        navs = {p.name: re.search(r'<nav class="ats-nav".*?</nav>', p.read_text(), re.S).group(0) for p in PAGES}
        self.assertEqual(len(set(navs.values())), 1, sorted(navs))

    def test_menu_script_and_shared_theme_on_every_page(self):
        for p in PAGES:
            html = p.read_text()
            self.assertIn('<script src="/nav.js"></script>', html, p.name)
            self.assertIn('href="/theme.css"', html, p.name)

    def test_every_page_has_a_title_except_market_detail(self):
        for p in PAGES:
            if p.stem == "market":  # the product name is its title
                continue
            self.assertIn('class="pg-head', p.read_text(), p.name)


if __name__ == "__main__":
    unittest.main()
