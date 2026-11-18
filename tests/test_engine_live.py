import unittest

from trading.engine import feed_status
from trading.events import EventLog


class FakeMarket:
    def status(self):
        return {"market": "live", "priced": 3, "polls": 7, "errors": 1, "last_error": "BTC: timeout"}


class FakeNews:
    def status(self):
        return {"news": "live", "sources": 24, "feeds_ok": 20, "polls": 2, "last_error": "feed down"}


class FeedStatusTest(unittest.TestCase):
    """MARKET=live: the price and news feeds both report polls and last_error. Merging them used to crash every
    engine shard on its first tick (emit() got multiple values for 'polls'), so the live app never traded."""

    def test_clashing_news_keys_get_a_prefix(self):
        status = feed_status(FakeMarket(), FakeNews())
        self.assertEqual(status["polls"], 7)
        self.assertEqual(status["news_polls"], 2)
        self.assertEqual(status["last_error"], "BTC: timeout")
        self.assertEqual(status["news_last_error"], "feed down")
        self.assertEqual(status["feeds_ok"], 20)

    def test_without_news(self):
        self.assertEqual(feed_status(FakeMarket(), None), FakeMarket().status())

    def test_can_be_emitted(self):
        import io
        out = io.StringIO()
        log = EventLog(0, stream=out)
        log.emit("market", **feed_status(FakeMarket(), FakeNews()))
        log.close()
        self.assertIn('"news_polls": 2', out.getvalue().replace('":2', '": 2'))


if __name__ == "__main__":
    unittest.main()
