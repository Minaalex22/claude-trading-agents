"""Time Machine page: replay odds, opening-range scan, Congress filings, budget + risk, practice tickets."""
import io
import json
import threading
import unittest
import urllib.parse
import zipfile
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path
import http.client
import tempfile

from trading import timemachine as tm
from trading.dashboard import EventHub, make_handler
from trading.webauth import COOKIE, WebAuth


def chart_json(days=25, price=100.0):
    """A Yahoo-shaped 5-minute chart for `days` weekdays of full US sessions (EDT, 9:30 = 13:30 UTC)."""
    ts, o, h, l, c = [], [], [], [], []
    p = price
    for d in range(days):
        day = datetime(2026, 9, 1 + d, 13, 30, tzinfo=timezone.utc).timestamp()
        for k in range(tm.STOCK_BARS):
            step = 0.2 if (d + k) % 3 else -0.25
            ts.append(int(day + k * 300))
            o.append(p); c.append(p + step); h.append(max(p, p + step) + 0.1); l.append(min(p, p + step) - 0.1)
            p += step
    return {"chart": {"result": [{"meta": {}, "timestamp": ts,
                                  "indicators": {"quote": [{"open": o, "high": h, "low": l, "close": c, "volume": [1] * len(ts)}]}}]}}


def house_zip(rows):
    xml = "<FinancialDisclosure>" + "".join(
        f"<Member><Prefix/><Last>{last}</Last><First>{first}</First><Suffix/><FilingType>{ft}</FilingType>"
        f"<StateDst>CA11</StateDst><Year>2026</Year><FilingDate>{date}</FilingDate><DocID>{doc}</DocID></Member>"
        for first, last, ft, date, doc in rows) + "</FinancialDisclosure>"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("2026FD.xml", xml)
    return buf.getvalue()


class ParseAndReplay(unittest.TestCase):
    def setUp(self):
        self.sessions = tm.parse_chart(chart_json())

    def test_parse_full_sessions_in_new_york_time(self):
        self.assertEqual(len(self.sessions), 25)
        self.assertTrue(all(s["full"] for s in self.sessions))
        self.assertEqual(self.sessions[0]["bars"][0][0], 0)
        self.assertEqual(tm._ny_offset(datetime(2026, 7, 1, tzinfo=timezone.utc).timestamp()), -4 * 3600)
        self.assertEqual(tm._ny_offset(datetime(2026, 12, 1, tzinfo=timezone.utc).timestamp()), -5 * 3600)

    def test_entry_time_is_the_bar_close(self):
        self.assertEqual(tm.slot_for("10:15"), 8)
        self.assertEqual(tm.slot_for("9:35"), 0)
        with self.assertRaises(ValueError):
            tm.slot_for("10:17")
        with self.assertRaises(ValueError):
            tm.slot_for("16:00")

    def test_odds_add_up_and_repeat_with_the_seed(self):
        a = tm.replay(self.sessions, 100, 99, 102.4, 8, runs=2000, seed=5)
        b = tm.replay(self.sessions, 100, 99, 102.4, 8, runs=2000, seed=5)
        self.assertEqual(a, b)
        self.assertAlmostEqual(a["target_first"] + a["stop_first"] + a["flat"], 100, delta=0.2)
        self.assertEqual(sum(a["histogram"]["counts"]), 2000)
        self.assertEqual(a["sessions_used"], 25)
        self.assertIn("not thousands of real trades", a["warning"])
        self.assertLessEqual(a["close_spread"]["p5"], a["close_spread"]["p95"])

    def test_sessions_setting_and_refusals(self):
        self.assertEqual(tm.replay(self.sessions, 100, 99, 102, 8, runs=500, last_n=12)["sessions_used"], 12)
        with self.assertRaises(ValueError):
            tm.replay(self.sessions[:5], 100, 99, 102, 8)
        with self.assertRaises(ValueError):
            tm.replay(self.sessions, 100, 101, 102, 8)       # sell stop but target above

    def test_sell_side_and_verdict(self):
        r = tm.replay(self.sessions, 100, 101, 97.6, 8, runs=1000)
        self.assertEqual(r["side"], "sell")
        self.assertEqual(tm.verdict(29.4, 25.5, 1.0), "Close to a coin flip")
        self.assertEqual(tm.verdict(20, 60, 1.0), "Strongly toward the stop")
        self.assertEqual(tm.verdict(24.1, 50.5, 2.0), "Close to break-even")

    def test_practice_bars_when_offline(self):
        def down(url, timeout=12.0):
            raise OSError("offline")
        sess, source = tm.Bars(get=down).sessions("TSLA")
        self.assertIn("practice", source)
        self.assertEqual(len(sess), 60)


class ScannerCongressRisk(unittest.TestCase):
    def test_opening_range_breakout(self):
        bars = [(k, 100, 101, 99, 100.5) for k in range(3)] + [(3, 100.5, 101.6, 100.4, 101.5)]
        hit = tm.opening_range({"date": "d", "bars": bars}, 15)
        self.assertEqual((hit["side"], hit["range_high"], hit["stop"], hit["entry_time"]), ("buy", 101, 100, "09:50"))
        self.assertAlmostEqual(hit["target"], 101.5 + 1.5 * 2.4, places=4)
        self.assertIsNone(tm.opening_range({"date": "d", "bars": bars[:3]}, 15))

    def test_house_index_keeps_only_trade_reports(self):
        raw = house_zip([("Nancy", "Pelosi", "P", "10/2/2026", "20035590"), ("A", "B", "C", "10/3/2026", "1"),
                         ("Cleo", "Fields", "P", "11/1/2026", "20036001")])
        rows = tm.parse_house_index(raw, 2026)
        self.assertEqual([r["member"] for r in rows], ["Cleo Fields", "Nancy Pelosi"])
        self.assertTrue(rows[1]["link"].endswith("/ptr-pdfs/2026/20035590.pdf"))
        c = tm.Congress(get=lambda url, timeout=0: raw)
        self.assertEqual(c.latest(member="pelosi")["total"], 1)
        self.assertFalse(tm.Congress(get=lambda url, timeout=0: (_ for _ in ()).throw(OSError())).latest()["ok"])

    def test_budget_and_money_rules(self):
        ok = tm.plan_check(100, 99, 102.4, balance=10_000, risk_pct=0.5)
        self.assertEqual((ok["shares"], ok["verdict"], ok["broker"], ok["real_money"]), (10, "PASS", "Demo", "locked"))
        low_rr = tm.plan_check(100, 99, 101, balance=10_000)
        self.assertEqual(low_rr["verdict"], "BLOCK")
        too_far = tm.plan_check(100, 80, 148, balance=10_000)
        self.assertEqual((too_far["shares"], too_far["verdict"]), (0, "BLOCK"))
        bad_odds = tm.plan_check(100, 99, 102.4, machine={"verdict": "Strongly toward the stop", "target_first": 5, "stop_first": 80})
        self.assertEqual(bad_odds["verdict"], "BLOCK")

    def test_tickets_only_after_a_pass(self):
        t = tm.PracticeTickets()
        with self.assertRaises(ValueError):
            t.add("TSLA", "buy", 5, 100, 99, 101, {"verdict": "BLOCK"})
        tk = t.add("TSLA", "buy", 5, 100, 99, 102.4, {"verdict": "PASS"})
        self.assertEqual((tk["mode"], len(t.list())), ("Demo", 1))


class Page(unittest.TestCase):
    def setUp(self):
        self._orig = tm._get
        chart = json.dumps(chart_json()).encode()
        tm._get = lambda url, timeout=12.0: chart if "chart" in url else (_ for _ in ()).throw(OSError("no"))
        self.tmp = tempfile.TemporaryDirectory()
        Path(self.tmp.name, "events-0.jsonl").write_text('{"ts":1,"shard":0,"type":"start","agents":10}\n')
        hub = EventHub(Path(self.tmp.name))
        hub.poll_files()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(hub, WebAuth("pw-pw-pw-pw-pw", "secret")))
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        resp, _ = self.req("POST", "/login", urllib.parse.urlencode({"password": "pw-pw-pw-pw-pw"}),
                           {"Content-Type": "application/x-www-form-urlencoded"})
        self.cookie = resp.getheader("Set-Cookie").split(";")[0].split("=", 1)[1]

    def tearDown(self):
        tm._get = self._orig
        self.server.shutdown()
        self.server.server_close()
        self.tmp.cleanup()

    def req(self, method, path, body=None, headers=None, cookie=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=10)
        h = {"Host": "ats.example.com", **(headers or {})}
        if cookie:
            h["Cookie"] = f"{COOKIE}={cookie}"
        conn.request(method, path, body=body, headers=h)
        r = conn.getresponse()
        data = r.read()
        conn.close()
        return r, data

    def test_page_api_and_ticket(self):
        r, _ = self.req("GET", "/timemachine")
        self.assertEqual(r.status, 303)
        r, html = self.req("GET", "/timemachine", cookie=self.cookie)
        self.assertEqual(r.status, 200)
        self.assertIn(b"Time <b>Machine</b>", html)
        r, body = self.req("GET", "/api/timemachine?symbol=TSLA&entry=100&stop=99&target=102.4&time=10:15&runs=1000",
                           cookie=self.cookie)
        b = json.loads(body)
        self.assertEqual(r.status, 200, b)
        self.assertIn("live 5-minute bars", b["source"])
        self.assertEqual(b["check"]["broker"], "Demo")
        r, body = self.req("GET", "/api/timemachine/scan", cookie=self.cookie)
        self.assertEqual(r.status, 200)
        self.assertIn("hits", json.loads(body))
        r, body = self.req("GET", "/api/timemachine/congress", cookie=self.cookie)
        self.assertFalse(json.loads(body)["ok"])
        r, body = self.req("GET", "/api/timemachine?symbol=TSLA&stop=abc", cookie=self.cookie)
        self.assertEqual(r.status, 400)
        r, body = self.req("POST", "/api/timemachine/ticket", json.dumps({"symbol": "TSLA", "entry": 100, "stop": 99, "target": 101}),
                           {"Content-Type": "application/json", "Origin": "http://ats.example.com"}, cookie=self.cookie)
        self.assertEqual(r.status, 400)          # 1:1 fails the 2.4x rule: no ticket


if __name__ == "__main__":
    unittest.main()
