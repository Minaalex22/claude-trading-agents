"""Cheaper, faster, restart-safe desk: indicator cache, headline dedup, cheap Claude reads, memory, watchdog,
rollover shield, constant-R sizing, passive Scalper limits and the live-promotion check."""

import io
import json
import statistics
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from trading import engine, execution_guard, indicators, memory, strategy_lab
from trading.hooks import OrderCheck, rollover, run_hooks
from trading.news_feed import LiveNewsFeed, keywords, same_story, story_key
from trading.ops_guard import (HeadlineBatchDeduplicator, TickHealth, check_feed_and_rollover_health,
                               compute_volatility_parity_notional, rollover_blocked)
from trading.reading_room import ClaudeReader, ReadingRoom
from trading.watchdog import FeedWatchdog


def utc(*a):
    return datetime(*a, tzinfo=timezone.utc)


class IndicatorCacheTests(unittest.TestCase):
    def tearDown(self):
        indicators.end()

    def test_fast_maths_matches_statistics(self):
        xs = [100 + (i * 37 % 11) * 0.13 for i in range(61)]
        self.assertAlmostEqual(indicators.pstdev(xs), statistics.pstdev(xs), places=12)
        self.assertAlmostEqual(indicators.fmean(xs), statistics.fmean(xs), places=12)

    def test_agents_on_one_symbol_share_one_read_per_tick(self):
        indicators.begin_tick(1)
        p = [1.0, 2.0, 3.0, 4.0]
        a = indicators.series("BTC", p)
        hits = indicators.hits
        self.assertIs(indicators.series("BTC", list(p)), a)
        self.assertEqual(indicators.hits, hits + 1)
        self.assertIsNot(indicators.series("ETH", p), a)  # other symbol
        self.assertIsNot(indicators.series("BTC", p[1:]), a)  # other window
        indicators.begin_tick(2)
        self.assertIsNot(indicators.series("BTC", p), a)  # new tick: worked out again

    def test_no_sharing_outside_a_desk_tick(self):
        indicators.end()
        p = [1.0, 2.0, 3.0]
        self.assertIsNot(indicators.series("BTC", p), indicators.series("BTC", p))


class HeadlineDedupTests(unittest.TestCase):
    def test_reworded_story_is_one_story(self):
        a = keywords("Nvidia beats Q3 earnings estimates", "NVDA")
        b = keywords("NVDA smashes Q3 profit forecast - Reuters", "NVDA")
        self.assertTrue(same_story(a, b))
        self.assertEqual(story_key("NVDA", 0, "Nvidia beats Q3 earnings estimates"),
                         story_key("NVDA", 0, "NVDA smashes Q3 profit forecast"))
        self.assertFalse(same_story(a, keywords("Nvidia misses Q3 revenue, guides lower", "NVDA")))
        self.assertFalse(same_story(a, keywords("Nvidia unveils new AI chip", "NVDA")))

    def test_feed_counts_the_reworded_repeat_as_a_second_source(self):
        now = 1_794_830_400.0
        rss = {"https://a.example/rss": [("Nvidia beats Q3 earnings estimates", now)],
               "https://b.example/rss": [("NVDA smashes Q3 profit forecast", now + 60)]}
        feed = LiveNewsFeed(["NVDA"], start_thread=False, clock=lambda: now + 120)
        feed.sources = lambda: [(u, "NVDA") for u in rss]
        import trading.news_feed as nf
        old = nf.parse_rss
        nf.parse_rss = lambda xml: xml
        try:
            feed.get = lambda url: rss[url]
            feed.poll_once()
        finally:
            nf.parse_rss = old
        out = feed.tick({}, 1)
        self.assertEqual(len(out), 2)
        self.assertEqual([h["weight"] for h in out], [0.5, 0.5])
        self.assertTrue(out[1]["confirmed"])  # the second site confirms, it isn't a new story
        self.assertEqual(feed.merged, 1)

    def test_batch_deduplicator(self):
        d = HeadlineBatchDeduplicator()
        now = utc(2026, 11, 24, 3)
        items = [{"symbol": "NVDA", "title": "Nvidia beats Q3 earnings estimates"},
                 {"symbol": "NVDA", "title": "NVDA smashes Q3 profit forecast"},
                 {"symbol": "TSLA", "title": "Tesla recalls 2 million cars"}]
        self.assertEqual(len(d.filter_unique_for_claude(items, now)), 2)
        self.assertEqual(d.filter_unique_for_claude(items, now), [])  # already sent this bucket


class FakeClaude:
    def __init__(self):
        self.calls = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self.create))

    def create(self, **kw):
        self.calls.append(kw)
        n = len(kw["messages"][0]["content"].splitlines()) - 1
        reads = [{"id": i, "base_rate": 0.5, "premortem": "x", "direction": 0.4, "why": "y"} for i in range(n)]
        usage = SimpleNamespace(input_tokens=50, cache_read_input_tokens=900, cache_creation_input_tokens=0,
                                output_tokens=40)
        return SimpleNamespace(stop_reason="end_turn", usage=usage,
                               content=[SimpleNamespace(type="text", text=json.dumps({"reads": reads}))])


class CheapReadsTests(unittest.TestCase):
    def wait(self, cond, secs=5):
        end = time.time() + secs
        while not cond() and time.time() < end:
            time.sleep(0.02)
        self.assertTrue(cond())

    def test_routine_headlines_never_reach_claude(self):
        fake = FakeClaude()
        room = ReadingRoom(["NVDA"], claude=ClaudeReader(fake, batch_seconds=0))
        room.update({"NVDA": 100.0}, [{"symbol": "NVDA", "text": "NVDA: Nvidia shares plunge after downgrade"}])
        self.assertEqual(room.read["free"], 1)
        time.sleep(0.2)
        self.assertEqual(fake.calls, [])

    def test_one_call_per_batch_with_cached_system_prompt(self):
        fake = FakeClaude()
        reader = ClaudeReader(fake, batch_seconds=0.3)
        for t in ("AAPL: Apple weighs options in China", "MSFT: Microsoft in talks over AI deal",
                  "GOLD: Central banks meet this week"):
            reader.headline(t)
        self.wait(lambda: reader.calls == 1 and not reader.status()["waiting"])
        self.assertEqual(len(fake.calls), 1)
        system = fake.calls[0]["system"]
        self.assertEqual(system[0]["cache_control"], {"type": "ephemeral", "ttl": "1h"})
        self.assertGreater(len(system[0]["text"].split()), 380)  # over the cacheable minimum
        self.assertEqual(reader.status()["tokens"]["cache_read"], 900)

    def test_reworded_headline_reuses_the_read(self):
        fake = FakeClaude()
        reader = ClaudeReader(fake, batch_seconds=0)
        reader.headline("NVDA: Nvidia beats Q3 earnings estimates")
        self.wait(lambda: reader.headline("NVDA: Nvidia beats Q3 earnings estimates") is not None)
        self.assertEqual(reader.headline("NVDA: NVDA smashes Q3 profit forecast"), 0.4)
        self.assertEqual(reader.reused, 1)
        self.assertEqual(len(fake.calls), 1)


class MemoryTests(unittest.TestCase):
    def test_new_learning_survives_a_restart(self):
        with tempfile.TemporaryDirectory() as d:
            env = {"AGENT_COUNT": "40", "TICKS": "30", "TICK_SECONDS": "0", "EVENTS_DIR": d}
            desk = engine.run(env, stream=io.StringIO())
            trader = next(a for a in desk.agents if a.role == "trader")
            from trading import dual_brain
            dual_brain.state(trader)["ledger"].row("breakout|trend")[0] = 7
            desk.managers.benched = {trader.agent_id}
            desk.orbit.arms[5].recent_pnl_usd.extend([10.0, -5.0])
            heat = list(desk.orbit.arms[5].recent_pnl_usd)
            memory.save(desk, Path(d) / "memory-0.json")
            again = engine.run({**env, "TICKS": "1"}, stream=io.StringIO())
            t2 = next(a for a in again.agents if a.agent_id == trader.agent_id)
            self.assertEqual(dual_brain.state(t2)["ledger"].row("breakout|trend")[0], 7)
            self.assertIsInstance(dual_brain.state(t2)["ledger"], dual_brain.Ledger)
            self.assertIn(trader.agent_id, again.managers.benched | {trader.agent_id})
            self.assertEqual(list(again.orbit.arms[5].recent_pnl_usd)[:len(heat)], heat)

    def test_saves_every_15_minutes_of_real_time(self):
        self.assertFalse(memory.due(1000.0, 1000.0 + 899, 7))
        self.assertTrue(memory.due(1000.0, 1000.0 + 900, 7))
        self.assertTrue(memory.due(1000.0, 1000.0, memory.SAVE_EVERY))


class WatchdogTests(unittest.TestCase):
    def test_old_and_stuck_prices_block_and_alert_once(self):
        said, now = [], [10_000.0]
        w = FeedWatchdog(["BTC", "AAPL"], alert=said.append, clock=lambda: now[0])
        stale = w.check({"BTC": 50.0, "AAPL": 1.0}, {"BTC": 10_000.0, "AAPL": 10_000.0 - 3 * 3600}, bar_seconds=3600)
        self.assertEqual(stale, {"AAPL"})  # 3 hours old on 1h bars (> 2 bars)
        self.assertEqual(len(said), 1)
        w.check({"BTC": 50.0, "AAPL": 1.0}, {"BTC": 10_000.0, "AAPL": 10_000.0 - 3 * 3600}, bar_seconds=3600)
        self.assertEqual(len(said), 1)  # once per problem, not every tick
        now[0] += 1900  # BTC price unchanged for 31 minutes: a frozen feed
        stale = w.check({"BTC": 50.0, "AAPL": 1.0}, {"BTC": now[0], "AAPL": now[0]}, bar_seconds=3600)
        self.assertEqual(stale, {"BTC"})
        self.assertTrue(any("cleared" in s or "fixed" in s for s in said))  # AAPL came back

    def test_dead_news_and_slow_feed_are_flagged(self):
        said = []
        w = FeedWatchdog(["BTC"], alert=said.append)
        for polls in (1, 2, 3):
            w.check({}, None, news_status={"polls": polls, "feeds_ok": 0})
        self.assertTrue(any("news" in s for s in said))
        w.check({}, None, poll_seconds=15, last_poll_seconds=60)
        self.assertTrue(any("slow" in s for s in said))


class RolloverTests(unittest.TestCase):
    def test_blackout_window_is_inside_not_always(self):
        self.assertIsNotNone(rollover_blocked("EURUSD", utc(2026, 11, 24, 22, 30)))
        self.assertIsNone(rollover_blocked("EURUSD", utc(2026, 11, 24, 12, 0)))  # the draft blocked this too
        self.assertIsNone(rollover_blocked("EURUSD", utc(2026, 11, 24, 23, 6)))
        self.assertIsNone(rollover_blocked("BTC", utc(2026, 11, 24, 22, 30)))  # crypto has no rollover
        self.assertIsNotNone(rollover_blocked("GOLD", utc(2026, 11, 27, 21, 0)))  # Friday close
        self.assertIsNone(rollover_blocked("GOLD", None))  # simulator

    def test_hook_blocks_new_risk_but_never_exits(self):
        t = utc(2026, 11, 24, 22, 0).timestamp()
        self.assertEqual(rollover(OrderCheck("EURUSD", 1000, 0, 1.1, now=t))[0], 0.0)
        qty, notes = run_hooks(OrderCheck("EURUSD", -1000, 1000, 1.1, now=t))  # closing
        self.assertEqual(qty, -1000)

    def test_full_health_check(self):
        now = utc(2026, 11, 24, 12)
        ok = TickHealth("EURUSD", "1h", utc(2026, 11, 24, 11), 1.1000, 1.1001, 0.0020)
        self.assertEqual(check_feed_and_rollover_health(ok, now), (True, "PASS"))
        old = TickHealth("EURUSD", "1h", utc(2026, 11, 24, 9), 1.1000, 1.1001, 0.0020)
        self.assertIn("stale_feed_block", check_feed_and_rollover_health(old, now)[1])
        wide = TickHealth("EURUSD", "1h", utc(2026, 11, 24, 11), 1.1000, 1.1010, 0.0020)
        self.assertIn("wide_spread_block", check_feed_and_rollover_health(wide, now)[1])


class SizingTests(unittest.TestCase):
    def test_same_dollar_risk_on_every_market(self):
        btc = compute_volatility_parity_notional(100_000, 100.0, 95.0, max_order_cap=1e9)  # 5% stop
        fx = compute_volatility_parity_notional(100_000, 1.0, 0.99, max_order_cap=1e9)  # 1% stop
        self.assertAlmostEqual(btc * 0.05, fx * 0.01, places=2)  # both lose $750 at the stop
        self.assertEqual(compute_volatility_parity_notional(100_000, 1.0, 0.99), 5000.0)  # cap stays on

    def test_risk_manager_cap(self):
        from trading.broker import PaperBroker
        from trading.risk import RiskLimits, RiskManager
        r = RiskManager(RiskLimits(), PaperBroker())
        self.assertEqual(r.position_cap(100.0, 0.5, 100_000), 20_000)  # tight stop: flat limit
        self.assertAlmostEqual(r.position_cap(100.0, 5.0, 100_000), 15_000)  # 5% stop: 750 / 0.05
        r.limits.risk_per_trade = None
        self.assertEqual(r.position_cap(100.0, 5.0, 100_000), 20_000)


class PassiveEntryTests(unittest.TestCase):
    def test_fill_runaway_and_timeout(self):
        book = execution_guard.PassiveBook()
        book.place("EURUSD", 1000, 1.1000, atr=0.0010)
        filled, _ = book.step({"EURUSD": 1.0997})
        self.assertEqual(len(filled), 1)
        self.assertLess(filled[0].limit, 1.1000)  # bought below where it was asked
        book.place("EURUSD", 1000, 1.1000, atr=0.0010)
        _, cancelled = book.step({"EURUSD": 1.1010})
        self.assertIn("ran", cancelled[0][1])
        book.place("EURUSD", -1000, 1.1000, atr=0.0010)
        book.step({"EURUSD": 1.1001})
        _, cancelled = book.step({"EURUSD": 1.1000})
        self.assertIn("no fill", cancelled[0][1])
        self.assertEqual(book.status()["filled"], 1)

    def test_only_scalper_led_entries_wait(self):
        self.assertTrue(execution_guard.scalper_led({"reversion": [0.8, 0.6], "trend": [0.1]}, 5))
        self.assertFalse(execution_guard.scalper_led({"reversion": [0.1], "trend": [0.9]}, 5))
        self.assertFalse(execution_guard.scalper_led({"reversion": [-0.8], "trend": []}, 5))

    def test_desk_runs_with_resting_limits(self):
        out = io.StringIO()
        desk = engine.run({"AGENT_COUNT": "200", "TICKS": "150", "TICK_SECONDS": "0"}, stream=out)
        kinds = [json.loads(line).get("event") for line in out.getvalue().splitlines() if '"type":"limit"' in line]
        st = desk.passive.status()
        self.assertEqual(st["placed"], kinds.count("placed"))
        self.assertEqual(st["filled"], kinds.count("filled"))


class PromotionTests(unittest.TestCase):
    def trades(self, n, win_rate, symbol="EURUSD"):
        wins = int(n * win_rate)
        return ([{"symbol": symbol, "qty": 10_000, "entry": 1.0, "pnl": 30.0}] * wins
                + [{"symbol": symbol, "qty": 10_000, "entry": 1.0, "pnl": -20.0}] * (n - wins))

    def test_three_gates(self):
        with tempfile.TemporaryDirectory() as d:
            good = self.trades(60, 0.65)
            st = strategy_lab.check(good, d)["fx"]
            self.assertTrue(st["gates"]["proof"])
            self.assertFalse(st["gates"]["sim_vs_real"])  # no sim figure yet
            self.assertIn("can't sign off", strategy_lab.sign_off(d, "fx", good))
            strategy_lab.record_sim(d, {"fx": 0.62})
            self.assertFalse(strategy_lab.promoted(d, "EURUSD"))
            self.assertIn("Signed off", strategy_lab.sign_off(d, "fx", good))
            self.assertTrue(strategy_lab.promoted(d, "EURUSD"))
            self.assertFalse(strategy_lab.promoted(d, "BTC"))
            strategy_lab.withdraw(d, "fx")
            self.assertFalse(strategy_lab.promoted(d, "EURUSD"))

    def test_too_few_or_too_weak(self):
        self.assertFalse(strategy_lab.check(self.trades(40, 0.7))["fx"]["gates"]["proof"])
        self.assertFalse(strategy_lab.check(self.trades(80, 0.55))["fx"]["gates"]["proof"])
        strategy_lab.record_sim  # costs are charged: a 30/-20 trade at 6 bp on 10k still wins
        self.assertEqual(strategy_lab.arm_stats(self.trades(10, 1.0))["fx"]["win_rate"], 1.0)


if __name__ == "__main__":
    unittest.main()
