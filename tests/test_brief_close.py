"""Session brief (top 5 ideas, Melbourne sessions) and daily close (ledger, report, weekly audit)."""
import json
import unittest
from datetime import datetime, timezone

from trading import daily_close as dc
from trading import session_brief as sb
from trading.jarvis import BuiltinBrain as LocalBrain

# 2026-11-24 is AEDT (UTC+11)
def mel(h, m=0, day=24):
    return datetime(2026, 11, day, h, m, tzinfo=timezone.utc).timestamp() - 11 * 3600


def decision(sym, direction, conf, verdict="ACT"):
    return {"symbol": sym, "direction": direction, "confidence": conf, "verdict": verdict, "agree": 4, "voting": 6,
            "heads": [{"head": "trend", "vote": "up" if direction == "buy" else "down", "score": 0.6, "weight": 1, "why": "Entry dept"},
                      {"head": "news", "vote": "down" if direction == "buy" else "up", "score": 0.2, "weight": 1, "why": ""}],
            "reasons": []}


class Brief(unittest.TestCase):
    def test_sessions_in_melbourne_time(self):
        self.assertEqual(sb.active_session(mel(11)), "asia")
        self.assertEqual(sb.active_session(mel(19)), "london")
        self.assertEqual(sb.active_session(mel(2)), "ny")
        self.assertEqual(sb.next_open(mel(9, 50))[0], "asia")
        self.assertEqual(sb.next_open(mel(23, 0))[0], "ny")
        self.assertEqual(sb.due(mel(9, 50), None), "asia:2026-11-24")
        self.assertIsNone(sb.due(mel(9, 50), "asia:2026-11-24"))
        self.assertIsNone(sb.due(mel(9, 30), None))

    def test_top_ideas_skip_blackouts_and_weak_calls(self):
        ds = [decision("AAPL", "buy", 0.6), decision("NVDA", "sell", 0.7), decision("TSLA", "buy", 0.05),
              decision("BTC", "none", 0.9), decision("MSFT", "buy", 0.4)]
        ideas = sb.top_ideas(ds, {"NVDA": "earnings block"})
        self.assertEqual([i["symbol"] for i in ideas], ["AAPL", "MSFT"])
        self.assertIn("trend (Entry dept)", ideas[0]["why"])

    def test_build_and_text(self):
        d = {"decisions": [decision("AAPL", "buy", 0.6), decision("NVDA", "sell", 0.7)], "totals": {"trades": 3}}
        guard = lambda s: (False, 0.0, "earnings block: NVDA") if s == "NVDA" else (True, 1.0, "PASS")  # noqa: E731
        b = sb.build(d, [{"name": "USD CPI", "at": "x", "impact": "HIGH"}], guard,
                     [{"manager": "Atlas", "title": "Head of Trading", "call": "favour trend", "agents": 10, "active": 8}],
                     "demo", False, now=mel(9, 46))
        self.assertEqual((b["session"], b["account"]["mode"], len(b["top_ideas"])), ("asia", "demo", 1))
        self.assertEqual(b["blocked"][0]["symbol"], "NVDA")
        t = sb.text(b)
        self.assertIn("BUY AAPL", t)
        self.assertIn("USD CPI", t)
        live = sb.build(d, [], guard, [], "live", True, now=mel(9, 46))
        self.assertIn("LIVE", live["account"]["label"])          # shows the One account switch, never forces it


class Close(unittest.TestCase):
    def trades(self):
        return [{"ts": mel(12), "symbol": "AAPL", "qty": 2, "entry": 100, "exit": 101, "move": 0.01, "won": True, "pnl": 2.0},
                {"ts": mel(13), "symbol": "AAPL", "qty": 2, "entry": 101, "exit": 99, "move": -0.0198, "won": False, "pnl": -4.0},
                {"ts": mel(14), "symbol": "AAPL", "qty": 1, "entry": 150, "exit": 151, "move": 0.02, "won": True, "pnl": 1.0}]

    def test_ledger_flags_prices_outside_the_day_and_bad_pnl(self):
        hist = [{"ts": mel(12), "prices": {"AAPL": 99}}, {"ts": mel(13), "prices": {"AAPL": 102}}]
        r = dc.ledger_check(self.trades(), hist)
        self.assertFalse(r["ok"])
        self.assertTrue(any("150" in p for p in r["problems"]))
        self.assertTrue(any("doesn't add up" in p for p in r["problems"]))     # 1 x 150 x 0.0067 != 1.0
        ok = dc.ledger_check(self.trades()[:2], hist)
        self.assertTrue(ok["ok"], ok)

    def test_daily_report_and_due_time(self):
        r = dc.report(self.trades(), [], audit={"rows": [{"at": "2026-11-24 01:00", "passed": True}]}, now=mel(8, 20, day=25))
        self.assertEqual((r["day"], r["trades"], r["wins"], r["win_rate"], r["pnl"], r["worst_drop"]),
                         ("2026-11-24", 3, 2, 66.7, -1.0, 4.0))
        self.assertEqual(dc.close_due(mel(8, 20, day=25), None), "2026-11-24")
        self.assertIsNone(dc.close_due(mel(8, 0, day=25), None))
        self.assertIn("3 desk trades", dc.text(r))

    def test_weekly_audit_flags_overfitting(self):
        many = [{"ts": mel(12), "won": i % 2 == 0, "pnl": 1.0} for i in range(40)]
        w = dc.weekly_audit(many, now=mel(13))
        self.assertEqual((w["trades"], w["real_win_rate"], w["flag"]), (40, 50.0, True))
        self.assertFalse(dc.weekly_audit(many[:10], now=mel(13))["flag"])


class Jarvis(unittest.TestCase):
    def test_jarvis_reads_the_brief(self):
        brain = LocalBrain()
        self.assertIn("BUY AAPL", brain.answer("what's the session brief?", {"session_brief": "Top ideas: 1. BUY AAPL"}))
        self.assertIn("08:15", brain.answer("daily close please", {}))


if __name__ == "__main__":
    unittest.main()


from tests.test_timemachine import Page as _TmPage  # noqa: E402


class BriefPage(_TmPage):
    test_page_api_and_ticket = None

    def test_brief_page_and_api(self):
        r, html = self.req("GET", "/brief", cookie=self.cookie)
        self.assertEqual(r.status, 200)
        self.assertIn(b"Session brief", html)
        r, body = self.req("GET", "/api/brief", cookie=self.cookie)
        b = json.loads(body)
        self.assertEqual(r.status, 200, b)
        for k in ("now", "now_text", "close", "weekly", "next_open", "sessions"):
            self.assertIn(k, b)
        self.assertEqual(b["now"]["account"]["mode"], "demo")
