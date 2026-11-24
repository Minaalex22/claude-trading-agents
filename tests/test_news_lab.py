"""News Lab: the lab uses the desk's real readers, its maths adds up, and it never peeks or trades for real."""
import http.client
import json
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path

from trading import news_lab as lab
from trading import news_pipeline as np_
from trading.reading_room import read_headline


class NewsLabTest(unittest.TestCase):
    def test_controls_are_clamped(self):
        p = lab.clean({"gate": "5", "half_life": "-3", "news_share": "nan", "sources": "9", "use_pipeline": "on", "junk": 1})
        self.assertEqual(p["gate"], 0.95)
        self.assertEqual(p["half_life"], 2.0)
        self.assertEqual(p["news_share"], lab.DEFAULTS["news_share"])
        self.assertEqual(p["sources"], 2)
        self.assertTrue(p["use_pipeline"])
        self.assertNotIn("junk", p)

    def test_defaults_mirror_the_live_desk(self):
        self.assertAlmostEqual(0.5 ** (1 / lab.DEFAULTS["half_life"]), 0.97, places=3)
        self.assertEqual(lab.DEFAULTS["news_share"], 0.4)
        self.assertEqual(lab.DEFAULTS["gate"], np_.START_GATE)

    def test_same_settings_same_answer(self):
        self.assertEqual(lab.run({"seed": 3}, detail=False), lab.run({"seed": 3}, detail=False))

    def test_reads_use_the_real_readers(self):
        r = lab.run({"seed": 5})
        for row in r["trace"][:50]:
            self.assertEqual(row["push"], read_headline(row["text"]))
            self.assertEqual(row["cheap_lean"], np_.cheap_read({"symbol": row["symbol"], "text": row["text"]})["lean"])

    def test_all_checks_that_must_pass_do(self):
        d = lab.lab({"seed": 11})
        must = {"News maths matches the real reading room", "P&L adds up", "Costs are paid on every trade",
                "No trade peeks at the future", "The bill adds up", "Paper only"}
        got = {c["check"]: c["ok"] for c in d["checks"]}
        for name in must:
            self.assertTrue(got[name], name)
        self.assertEqual(d["real_money"], "locked")

    def test_gate_moves_the_bill_and_costs_cut_pnl(self):
        cheap = lab.run({"gate": 0.5}, detail=False)
        strict = lab.run({"gate": 0.95}, detail=False)
        self.assertGreater(strict["pipeline"]["bill"]["hybrid"], cheap["pipeline"]["bill"]["hybrid"])
        free = lab.run({"cost_bps": 0}, detail=False)
        dear = lab.run({"cost_bps": 10}, detail=False)
        self.assertEqual(free["summary"]["trades"], dear["summary"]["trades"])
        self.assertAlmostEqual(free["summary"]["pnl"] - dear["summary"]["pnl"], free["summary"]["trades"] * 10.0, places=1)

    def test_waiting_for_two_sources_ignores_rumours(self):
        r = lab.run({"sources": 2})
        rumours = [row for row in r["trace"] if not row["confirmed"]]
        self.assertTrue(rumours)
        self.assertTrue(all(not row["used"] and row["news_after"] == row["news_before"] for row in rumours))

    def test_chart_only_ignores_news(self):
        a = lab.run({"news_share": 0, "honest": 0.5}, detail=False)
        b = lab.run({"news_share": 0, "honest": 0.9}, detail=False)
        self.assertEqual(a["summary"], b["summary"])

    def test_reroute_live_headlines(self):
        log = [{"symbol": "AAPL", "text": "x", "triage": {"confidence": 0.85}, "route": "cheap", "gate": 0.8, "overrule": None}]
        self.assertEqual(lab.reroute(log, 0.9)[0]["your_route"], "deep")
        self.assertTrue(lab.reroute(log, 0.9)[0]["changed"])
        self.assertFalse(lab.reroute(log, 0.8)[0]["changed"])


class NewsLabPageTest(unittest.TestCase):
    def test_page_and_api(self):
        from trading.dashboard import EventHub, make_handler
        from trading.webauth import WebAuth
        tmp = tempfile.TemporaryDirectory()
        Path(tmp.name, "events-0.jsonl").write_text('{"ts":1,"shard":0,"type":"start","agents":250}\n')
        hub = EventHub(Path(tmp.name))
        server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(hub, WebAuth(None, "secret", enabled=False)))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            def get(path):
                c = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=20)
                c.request("GET", path)
                r = c.getresponse()
                return r.status, r.read()
            status, body = get("/newslab")
            self.assertEqual(status, 200)
            self.assertIn(b"News <b>lab</b>", body)
            status, body = get("/api/newslab?gate=0.9&news_share=0.6&sources=2")
            self.assertEqual(status, 200)
            d = json.loads(body)
            self.assertEqual(d["params"]["gate"], 0.9)
            self.assertEqual(d["live"], [])
            self.assertIn("checks", d)
        finally:
            server.shutdown()
            server.server_close()
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
