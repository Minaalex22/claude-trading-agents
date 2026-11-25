import tempfile
import unittest
from datetime import date, datetime, timezone

from trading import option_flows as F
from trading import whale_bridge as W


def alert(**kw):
    row = {"id": "a1", "ticker": "SPX", "option_chain": "SPXW261125C06700000", "type": "call", "total_premium": "4200000",
           "total_ask_side_prem": "4000000", "total_bid_side_prem": "200000", "volume": 900, "open_interest": 100,
           "underlying_price": None, "all_opening_trades": True, "created_at": "2026-11-25T15:00:00Z"}
    row.update(kw)
    return row


class ParseTests(unittest.TestCase):
    def test_occ(self):
        o = W.parse_occ("SPXW261125P06650000")
        self.assertEqual((o["root"], o["expiry"], o["type"], o["strike"]), ("SPXW", date(2026, 11, 25), "PUT", 6650.0))
        self.assertEqual(W.parse_occ("junk"), {})

    def test_alert_to_print(self):
        levels = {"SPX": {"prior_high": 6702.0, "prior_low": 6600.0, "prior_vwap": 6650.0}}
        p = W.to_print(alert(), levels, {"SPX": 6700.0}, today=date(2026, 11, 25))
        self.assertEqual((p.underlying, p.option_type, p.action, p.dte, p.strike, p.spot, p.key_level),
                         ("SPX", "CALL", "BUY_TO_OPEN", 0, 6700.0, 6700.0, 6702.0))
        d = F.decode(p)
        self.assertEqual(d.bias, "BULLISH")
        self.assertTrue(d.at_key_level)

    def test_put_bought_is_bearish_and_sold_is_bullish(self):
        put = alert(option_chain="SPXW261125P06650000", type="put")
        self.assertEqual(F.decode(W.to_print(put, {}, {"SPX": 6700}, date(2026, 11, 25))).bias, "BEARISH")
        sold = alert(option_chain="SPXW261125P06650000", type="put", total_ask_side_prem="1", total_bid_side_prem="4000000")
        self.assertEqual(W.to_print(sold, {}, {}, date(2026, 11, 25)).action, "SELL_TO_OPEN")
        self.assertEqual(F.decode(W.to_print(sold, {}, {}, date(2026, 11, 25))).bias, "BULLISH")

    def test_filters(self):
        today = date(2026, 11, 25)
        self.assertIsNone(W.to_print(alert(total_premium="2900000"), {}, {}, today))
        self.assertIsNone(W.to_print(alert(all_opening_trades=False), {}, {}, today))
        self.assertIsNone(W.to_print(alert(ticker="GME"), {}, {}, today))
        self.assertEqual(W.to_print(alert(option_chain="SPXW261128C06700000"), {}, {}, today).dte, 3)

    def test_rth(self):
        self.assertTrue(W.us_rth_open(datetime(2026, 11, 25, 15, 0, tzinfo=timezone.utc)))   # 10:00 New York
        self.assertFalse(W.us_rth_open(datetime(2026, 11, 25, 22, 0, tzinfo=timezone.utc)))  # after the close
        self.assertFalse(W.us_rth_open(datetime(2026, 11, 28, 15, 0, tzinfo=timezone.utc)))  # Saturday


class BridgeTests(unittest.TestCase):
    def test_poll_dedupes_feeds_and_shares_with_engine(self):
        urls = []

        def http(url, headers):
            urls.append(url)
            self.assertEqual(headers["Authorization"], "Bearer tok")
            return {"data": [alert(), alert()]}

        def yahoo(url):
            return {"chart": {"result": [{"meta": {"regularMarketPrice": 6700.0}, "indicators": {"quote": [
                {"high": [6702, 6710], "low": [6600, 6650], "close": [6690, 6700]}]}}]}}
        with tempfile.TemporaryDirectory() as d:
            feed, engine_feed = F.FlowFeed(), F.FlowFeed()
            engine_feed.follow(d)
            b = W.WhaleBridge({"UW_API_TOKEN": "tok"}, feed=feed, http=http, yahoo=yahoo, folder=d)
            out = b.poll()
            self.assertEqual(len(out), 1)  # same alert twice -> once
            self.assertIn("min_premium=3000000", urls[0])
            self.assertIn("max_dte=0", urls[0])
            self.assertIn("all_opening=true", urls[0])
            self.assertEqual(b.levels["SPX"]["prior_vwap"], round((6702 + 6600 + 6690) / 3, 2))
            self.assertEqual(b.poll(), [])  # nothing new
            self.assertIn("newer_than=", urls[1])
            self.assertEqual(engine_feed.sync(force=True), 1)  # the engine process sees the print
            self.assertEqual(len(engine_feed.active), 1)

    def test_no_token_and_budget(self):
        self.assertEqual(W.WhaleBridge({}).poll(), [])
        b = W.WhaleBridge({"UW_API_TOKEN": "t", "UW_MAX_CALLS_DAY": "1"}, feed=F.FlowFeed(),
                          http=lambda u, h: {"data": []}, yahoo=lambda u: {}, folder="")
        b.poll()
        b.poll()
        self.assertEqual(b.last["state"], "budget")
        self.assertEqual(b.calls, 1)




class ParseAndIngestTests(unittest.TestCase):
    def test_parse_and_ingest_without_network(self):
        feed = F.FlowFeed()
        b = W.WhaleBridge({"UW_API_TOKEN": "t"}, feed=feed, folder="")
        rows = [alert(id="x1", option_chain="SPXW%sC06700000" % W.ny_time().strftime("%y%m%d")),
                alert(id="x2", total_premium="1000000")]
        out = b.parse_and_ingest_alerts(rows)
        self.assertEqual([o["underlying"] for o in out], ["SPX"])
        self.assertEqual(out[0]["bias"], "BULLISH")
        self.assertEqual(b.parse_and_ingest_alerts(rows), [])  # already seen


if __name__ == "__main__":
    unittest.main()
