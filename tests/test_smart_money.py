import json
import unittest
from datetime import date

from trading import smart_money as sm

FORM4 = b"""<?xml version="1.0"?><ownershipDocument>
<reportingOwner><reportingOwnerId><rptOwnerName>Jane Doe</rptOwnerName></reportingOwnerId>
<reportingOwnerRelationship><isOfficer>1</isOfficer><officerTitle>CFO</officerTitle></reportingOwnerRelationship></reportingOwner>
<nonDerivativeTable>
<nonDerivativeTransaction><transactionDate><value>%s</value></transactionDate>
<transactionCoding><transactionCode>P</transactionCode></transactionCoding>
<transactionAmounts><transactionShares><value>1000</value></transactionShares><transactionPricePerShare><value>50</value></transactionPricePerShare></transactionAmounts></nonDerivativeTransaction>
<nonDerivativeTransaction><transactionDate><value>%s</value></transactionDate>
<transactionCoding><transactionCode>M</transactionCode></transactionCoding>
<transactionAmounts><transactionShares><value>9999</value></transactionShares></transactionAmounts></nonDerivativeTransaction>
</nonDerivativeTable></ownershipDocument>""" % ((date.today().isoformat().encode(),) * 2)


class SmartMoneyTest(unittest.TestCase):
    def test_parse_counts_only_open_market(self):
        owners, trades = sm.parse_form4(FORM4)
        self.assertEqual(owners[0]["title"], "CFO")
        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0]["value"], 50000.0)

    def test_signal_and_vote(self):
        s = sm.summarize("AAPL", [{"date": "2026-11-01", "side": "buy", "value": 900.0, "plan_10b5_1": False, "name": "A"},
                                  {"date": "2026-11-02", "side": "sell", "value": 100.0, "plan_10b5_1": True, "name": "B"}])
        self.assertEqual(s["signal"], "buying")
        self.assertEqual(sm.vote(s)[0], 1)
        self.assertEqual(sm.vote(sm.summarize("X", []))[0], 0)
        self.assertEqual(sm.vote(None)[0], 0)

    def test_needs_user_agent(self):
        snap = sm.SmartMoney("").snapshot()
        self.assertFalse(snap["ready"])
        self.assertIn("SEC_USER_AGENT", snap["setup"])

    def test_fetch_with_fake_sec(self):
        today = date.today().isoformat()

        def fetch(url):
            if url.endswith("company_tickers.json"):
                return json.dumps({"0": {"ticker": "AAPL", "cik_str": 320193}}).encode()
            if "submissions" in url:
                return json.dumps({"filings": {"recent": {"form": ["4", "10-K"], "accessionNumber": ["0001-26-1", "x"],
                                                          "primaryDocument": ["xslF345X05/f4.xml", "y"],
                                                          "filingDate": [today, today]}}}).encode()
            return FORM4

        s = sm.SmartMoney("Test test@example.com", fetch=fetch).insiders("AAPL")
        self.assertEqual(s["buys"], 1)
        self.assertEqual(s["signal"], "buying")


if __name__ == "__main__":
    unittest.main()
