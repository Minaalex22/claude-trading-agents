"""Telegram phone alerts: off without settings, no repeats, an hourly cap, and never stopping the desk."""

import unittest

from trading.alerts import MAX_PER_HOUR, Alerts


class Clock:
    def __init__(self):
        self.t = 1_800_000_000.0

    def __call__(self):
        return self.t


class AlertTests(unittest.TestCase):
    def make(self, post=None):
        self.posts = []
        self.clock = Clock()
        return Alerts("123:abc", "42", "https://app.example", post=post or (lambda url, d: self.posts.append((url, d))),
                      clock=self.clock, start_thread=False)

    def test_off_without_settings(self):
        self.assertFalse(Alerts(None, "42", start_thread=False).send("hi"))
        self.assertFalse(Alerts.from_env({}, start_thread=False).on)

    def test_proposal_message_and_link(self):
        a = self.make()
        self.assertTrue(a.proposal({"side": "buy", "qty": 4, "symbol": "AAPL", "price": 120.0}))
        a.flush()
        url, data = self.posts[0]
        self.assertTrue(url.endswith("/bot123:abc/sendMessage"))
        self.assertEqual(data["chat_id"], "42")
        self.assertIn("BUY 4 AAPL", data["text"])
        self.assertIn("https://app.example/", data["text"])

    def test_no_repeats_and_hourly_cap(self):
        a = self.make()
        self.assertTrue(a.send("same"))
        self.assertFalse(a.send("same"))  # repeat within 10 minutes
        self.clock.t += 601
        self.assertTrue(a.send("same"))
        for i in range(MAX_PER_HOUR):
            a.send(f"msg {i}")
        self.assertFalse(a.send("one too many"))
        self.clock.t += 3601
        self.assertTrue(a.send("next hour"))

    def test_failure_never_raises(self):
        def boom(url, d):
            raise OSError("network down")
        a = self.make(post=boom)
        a.stop(0, "daily loss limit hit")
        a.flush()
        self.assertEqual(a.status(), {"on": True, "sent": 0, "failed": 1})


if __name__ == "__main__":
    unittest.main()
