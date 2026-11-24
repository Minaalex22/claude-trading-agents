"""Phone alerts through Telegram: so a real-money proposal is seen inside its 5-minute window.

Set TELEGRAM_BOT_TOKEN (from @BotFather) and TELEGRAM_CHAT_ID (your chat with the bot) and the app sends:
- a real-money proposal waiting for your approval (with DASHBOARD_URL, a link to approve it),
- each bridge result (filled or failed),
- a stop switching on or off: the daily loss stop, the drawdown halt, the kill switch.
- the 07:00 Night Shift briefing (trading/night_shift.py).

Quiet hours (`quiet`, set by the app to Night Shift's sleep window): single-trade results are held, not sent, and go
out together in the first result message after 07:00.

Messages go out on a background thread, so trading never waits on Telegram. The same text is not sent twice
within REPEAT_SECONDS, and at most MAX_PER_HOUR messages go out an hour, so a fault can't flood your phone. The
token is only ever put in the request URL to Telegram and never logged. Without both settings, nothing is sent.
"""

import json
import os
import queue
import threading
import time
import urllib.parse
import urllib.request

REPEAT_SECONDS = 600
MAX_PER_HOUR = 30
API = "https://api.telegram.org"


def _post(url: str, data: dict) -> None:
    body = urllib.parse.urlencode(data).encode()
    with urllib.request.urlopen(urllib.request.Request(url, data=body), timeout=10) as resp:  # noqa: S310
        json.loads(resp.read() or b"{}")


class Alerts:
    def __init__(self, token: str | None, chat_id: str | None, dashboard_url: str = "", post=_post,
                 clock=time.time, start_thread: bool = True):
        self.on = bool(token and chat_id)
        self._url = f"{API}/bot{token}/sendMessage" if self.on else ""
        self.chat_id = chat_id
        self.dashboard_url = dashboard_url.rstrip("/")
        self.post = post
        self.clock = clock
        self.recent: dict[str, float] = {}
        self.sent_times: list[float] = []
        self.sent = 0
        self.failed = 0
        self.q: queue.Queue = queue.Queue(maxsize=100)
        self.quiet = None  # callable: True during quiet hours (no single-trade alerts)
        self.held: list[str] = []
        if self.on and start_thread:
            threading.Thread(target=self._run, name="alerts", daemon=True).start()

    @classmethod
    def from_env(cls, env=os.environ, **kw) -> "Alerts":
        return cls(env.get("TELEGRAM_BOT_TOKEN"), env.get("TELEGRAM_CHAT_ID"), env.get("DASHBOARD_URL", ""), **kw)

    def send(self, text: str) -> bool:
        """Queue a message. False if alerts are off, it repeats a recent one, or the hourly cap is reached."""
        if not self.on:
            return False
        now = self.clock()
        if now - self.recent.get(text, float("-inf")) < REPEAT_SECONDS:
            return False
        self.sent_times = [t for t in self.sent_times if now - t < 3600]
        if len(self.sent_times) >= MAX_PER_HOUR:
            return False
        self.recent[text] = now
        if len(self.recent) > 500:
            self.recent = {k: v for k, v in self.recent.items() if now - v < REPEAT_SECONDS}
        self.sent_times.append(now)
        try:
            self.q.put_nowait(text)
        except queue.Full:
            return False
        return True

    def flush(self) -> None:
        """Send everything queued now (used when no thread runs, e.g. in tests)."""
        while not self.q.empty():
            self._deliver(self.q.get_nowait())

    def _deliver(self, text: str) -> None:
        try:
            self.post(self._url, {"chat_id": self.chat_id, "text": text, "disable_web_page_preview": "true"})
            self.sent += 1
        except Exception:  # a dead network or a bad token must never stop the desk
            self.failed += 1

    def _run(self) -> None:
        while True:
            self._deliver(self.q.get())

    # ---- the messages ----
    def proposal(self, order: dict) -> bool:
        link = f"\nApprove or reject: {self.dashboard_url}/" if self.dashboard_url else ""
        return self.send(f"Real money: {order['side'].upper()} {order['qty']} {order['symbol']} at ${order['price']} "
                         f"(${order['qty'] * order['price']:,.0f}). Waiting for your approval for 5 minutes.{link}")

    def result(self, order: dict) -> bool:
        text = (f"Real money {order['status']}: {order['side']} {order['qty']} {order['symbol']}. "
                f"{order.get('detail', '')}"[:600])
        if self.quiet and self.quiet():
            self.held = (self.held + [text])[-20:]  # quiet hours: keep it for the morning
            return False
        if self.held:
            text = ("While you slept: " + " | ".join(self.held) + "\n" + text)[:3500]
            self.held = []
        return self.send(text)

    def stop(self, shard, reason: str | None) -> bool:
        if reason:
            return self.send(f"Trading stopped (shard {shard}): {reason}. New risk is blocked; exits still work.")
        return self.send(f"Trading resumed (shard {shard}): all stops clear.")

    def status(self) -> dict:
        return {"on": self.on, "sent": self.sent, "failed": self.failed}
