"""The Pelosi trader: two dates per trade, entries only after the announcement, ranges, buy/sell, paper book."""
import tempfile
import unittest
from datetime import date
from pathlib import Path

from trading import pelosi, pelosi_ptr

FIX = Path(__file__).resolve().parent / "fixtures" / "pelosi"

CAPITOL = """filer_name,chamber,party,state,transaction_date,filing_date,disclosure_delay_days,is_late_filing,ticker,asset_name,security_type,transaction_type,amount_range,amount_min_usd,amount_max_usd,filing_url
Nancy Pelosi,house,D,CA,2026-07-24,2026-08-21,28,No,BE,Bloom Energy,call_option,Purchase,"$1,000,001 - $5,000,000",1000001,5000000,https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/2026/20035143.pdf
Nancy Pelosi,house,D,CA,2026-10-07,2026-11-06,30,No,WBD,Warner Bros,stock,Sale (Full),"$50,001 - $100,000",50001,100000,https://x
Kevin Hern,house,R,OK,2026-08-01,2026-09-30,60,Yes,ROK,Rockwell,stock,Purchase,"$1,001 - $15,000",1001,15000,https://x
"""


def prices(tickers, start=date(2026, 1, 1), n=320, step=0.001):
    from datetime import timedelta
    d = [(start + timedelta(days=i)).isoformat() for i in range(n)]
    return {t: (d, [100 * (1 + step) ** i for i in range(n)]) for t in tickers}


class TwoDatesAndRanges(unittest.TestCase):
    def test_capitol_rows_keep_both_dates_range_and_side(self):
        rows = pelosi.from_capitol_csv(CAPITOL)
        be = rows[0]
        self.assertEqual((be["trade_date"], be["announce_date"], be["lag_days"]), ("2026-07-24", "2026-08-21", 28))
        self.assertEqual((be["side"], be["kind"], be["amount_low"], be["amount_high"]), ("buy", "call", 1000001, 5000000))
        self.assertEqual(be["amount"], "$1,000,001 - $5,000,000")  # the range as filed, not a midpoint
        self.assertEqual(rows[1]["side"], "sell")
        self.assertTrue(rows[2]["late"])  # 60 days > 45

    def test_estimated_announce_is_the_legal_deadline(self):
        self.assertEqual(pelosi.announce_deadline(date(2026, 1, 1), date(2026, 1, 5)), date(2026, 2, 4))  # notified+30
        self.assertEqual(pelosi.announce_deadline(date(2026, 1, 1), date(2026, 1, 30)), date(2026, 2, 15))  # trade+45
        r = pelosi.from_hsw_json([{"transaction_date": "01/02/2026", "disclosure_date": "01/02/2026", "ticker": "NVDA",
                                   "type": "Purchase", "amount": "$250,001 - $500,000", "representative": "Nancy Pelosi",
                                   "filing_id": "123", "asset_type": "Stock"}])[0]
        self.assertTrue(r["announce_estimated"])
        self.assertEqual(r["announce_date"], "2026-02-01")
        self.assertEqual(r["trade_date"], "2026-01-02")  # the trade date is kept

    def test_merge_prefers_real_filing_dates(self):
        est = pelosi.from_hsw_json([{"transaction_date": "07/24/2026", "disclosure_date": "07/24/2026", "ticker": "BE",
                                     "type": "Purchase", "amount": "$1,000,001 - $5,000,000",
                                     "representative": "Nancy Pelosi", "filing_id": "20035143", "asset_type": "call_option"}])
        merged = pelosi.merge(est, pelosi.from_capitol_csv(CAPITOL))
        be = [t for t in merged if t["ticker"] == "BE"]
        self.assertEqual(len(be), 1)
        self.assertEqual(be[0]["announce_date"], "2026-08-21")
        fixed = pelosi.merge(est, filed={"20035143": "2026-08-20"})[0]
        self.assertEqual((fixed["announce_date"], fixed["announce_estimated"]), ("2026-08-20", False))


class NoLookAhead(unittest.TestCase):
    def test_backtest_buys_the_day_after_the_announcement(self):
        trades = pelosi.from_capitol_csv(CAPITOL)
        r = pelosi.backtest(trades, prices(["BE", "WBD", "SPY"]), hold=20)
        self.assertEqual(len(r["trades"]), 1)
        t = r["trades"][0]
        self.assertEqual(t["entry"], "2026-08-22")  # never the trade date 2026-07-24
        self.assertGreater(t["move_trade_to_announce"], 0)
        self.assertEqual(r["stats"]["win_rate"], 100.0)

    def test_announced_sale_closes_early(self):
        rows = pelosi.from_capitol_csv(CAPITOL) + pelosi.from_capitol_csv(
            CAPITOL.splitlines()[0] + "\nNancy Pelosi,house,D,CA,2026-08-25,2026-09-01,7,No,BE,Bloom,stock,Sale,"
            '"$1,000,001 - $5,000,000",1000001,5000000,https://x\n')
        t = pelosi.backtest(rows, prices(["BE", "SPY"]), hold=60)["trades"][0]
        self.assertEqual((t["exit"], t["exit_why"]), ("2026-09-02", "member sold"))


class MinasGuards(unittest.TestCase):
    def test_chase_cap_skips_a_stock_that_already_ran_15_percent(self):
        trades = pelosi.from_capitol_csv(CAPITOL)
        r = pelosi.backtest(trades, prices(["BE", "SPY"], step=0.006), hold=20)  # ~19% between trade and announce
        self.assertEqual((len(r["trades"]), r["skipped"]["chase"]), (0, 1))
        self.assertEqual(len(pelosi.backtest(trades, prices(["BE", "SPY"], step=0.006), hold=20, chase_cap=0)["trades"]), 1)

    def test_trend_gate_skips_below_the_200_day_average(self):
        trades = pelosi.from_capitol_csv(CAPITOL)
        r = pelosi.backtest(trades, prices(["BE", "SPY"], step=-0.001), hold=20)
        self.assertEqual(r["skipped"]["trend"], 1)
        self.assertEqual(len(pelosi.backtest(trades, prices(["BE", "SPY"], step=-0.001), hold=20, trend_gate=False)["trades"]), 1)

    def test_sage_lean_plus_020_only_above_the_200_day(self):
        b = pelosi.CongressBias()
        b.publish({"NVDA": {"bias": 0.5, "why": "x"}})
        self.assertAlmostEqual(b.sage_lean("NVDA", 0.55, True), 0.75)
        self.assertEqual(b.sage_lean("NVDA", 0.55, False), 0.55)
        self.assertEqual(b.boost("NVDA", 0.5, above_200=False), 0.5)
        self.assertEqual(b.sage_lean("AAPL", 0.55, True), 0.55)

    def test_unusual_whales_congress_rows(self):
        r = pelosi.from_uw([{"name": "Nancy Pelosi", "ticker": "NVDA", "txn_type": "Buy", "transaction_date": "2026-11-01",
                             "filed_at_date": "2026-11-20", "amounts": "$1,000,001 - $5,000,000"}])[0]
        self.assertEqual((r["side"], r["lag_days"], r["amount_low"], r["amount_high"]), ("buy", 19, 1000001, 5000000))

    def test_book_skips_a_chased_stock(self):
        with tempfile.TemporaryDirectory() as tmp:
            book = pelosi.PaperBook(Path(tmp) / "b.json")
            closes = lambda s: (["2026-07-24"], [40.0])  # noqa: E731
            log = book.step(pelosi.from_capitol_csv(CAPITOL)[:1], lambda s: 50.0, ["pelosi"], today=date(2026, 8, 22),
                            closes=closes)
            self.assertIn("chase cap", log[0])


class PaperBookAndAgents(unittest.TestCase):
    def test_book_buys_announced_purchase_and_sells_on_announced_sale(self):
        with tempfile.TemporaryDirectory() as tmp:
            book = pelosi.PaperBook(Path(tmp) / "b.json")
            buys = pelosi.from_capitol_csv(CAPITOL)[:1]
            log = book.step(buys, lambda s: 50.0, ["pelosi"], today=date(2026, 7, 30))
            self.assertEqual(log, [])  # traded but not announced yet: nothing to act on
            log = book.step(buys, lambda s: 50.0, ["pelosi"], today=date(2026, 8, 22))
            self.assertTrue(log[0].startswith("BUY BE"))
            self.assertEqual(book.snapshot()["open"][0]["trade_date"], "2026-07-24")
            sale = dict(buys[0], side="sell", announce_date="2026-09-01", trade_date="2026-08-25")
            log = book.step([sale], lambda s: 60.0, ["pelosi"], today=date(2026, 9, 1))
            self.assertIn("member sold", log[0])
            snap = pelosi.PaperBook(Path(tmp) / "b.json").snapshot()  # saved and reloaded
            self.assertEqual(snap["closed"][0]["ret"], 0.2)
            self.assertEqual(snap["real_money"], "locked")

    def test_agents_see_buys_up_sales_down_and_it_fades(self):
        rows = pelosi.from_capitol_csv(CAPITOL)
        m = pelosi.signal_map(rows, ["pelosi"], today=date(2026, 11, 7))
        self.assertLess(m["WBD"]["bias"], 0)  # announced sale
        self.assertNotIn("BE", m)  # announced 2026-08-21: older than 60 days
        m = pelosi.signal_map(rows, ["pelosi"], today=date(2026, 8, 22))
        self.assertGreater(m["BE"]["bias"], 0.4)
        self.assertNotIn("WBD", m)  # not announced yet on that day
        b = pelosi.CongressBias()
        b.publish(m)
        self.assertAlmostEqual(b.boost("BE", 0.5), 0.575)
        self.assertAlmostEqual(b.boost("BE", -0.5), -0.425)
        self.assertEqual(b.boost("AAPL", 0.5), 0.5)

    def test_bias_travels_through_events_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            w, r = pelosi.CongressBias(), pelosi.CongressBias()
            r.follow(tmp)
            w.publish({"NVDA": {"bias": 0.5, "why": "x"}}, tmp)
            self.assertEqual(r.bias("NVDA-USD")[0], 0.5)


class VendoredSkill(unittest.TestCase):
    def test_house_index_and_ptr_parse(self):
        idx = pelosi_ptr.parse_index(pelosi_ptr.read_index_bytes((FIX / "2026FD.zip").read_bytes()), 2026)
        self.assertTrue(any(r["last"] == "Pelosi" and r["type"] == "P" for r in idx))
        rep = pelosi_ptr.parse_ptr_text((FIX / "ptr-20035143.txt").read_text(), filed="2026-08-21")
        be = [r for r in rep["rows"] if r["ticker"] == "BE"]
        self.assertTrue(be and be[0]["days_to_disclose"] == 28)

    def test_feed_offline_and_page_data(self):
        feed = pelosi.Feed(get=lambda url: CAPITOL.encode() if url == pelosi.CAPITOL_CSV else (_ for _ in ()).throw(OSError("off")))
        feed.refresh()
        self.assertEqual(feed.status()["trades"], 3)
        with tempfile.TemporaryDirectory() as tmp:
            d = pelosi.page_data(feed, pelosi.PaperBook(Path(tmp) / "b.json"))
        self.assertEqual(len(d["pelosi"]), 2)
        self.assertTrue(any(t["side"] == "sell" for t in d["signals"]))
        self.assertIn("45 days", d["note"])


if __name__ == "__main__":
    unittest.main()
