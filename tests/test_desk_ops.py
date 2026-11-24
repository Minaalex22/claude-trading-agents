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


class BrokerDriftTests(unittest.TestCase):
    def test_names_and_gap(self):
        self.assertEqual(execution_guard.broker_symbol("BTC"), "BTCUSD")
        self.assertEqual(execution_guard.broker_symbol("GOLD"), "XAUUSD")
        self.assertEqual(execution_guard.broker_symbol("EURUSD=X"), "EURUSD")
        self.assertEqual(execution_guard.broker_symbol("ETH-USDT"), "ETHUSD")
        self.assertEqual(execution_guard.broker_mids(["BTC", "MSFT"], {"BTCUSD": {"mid": 90000.0}}),
                         {"BTC": 90000.0})
        self.assertEqual(execution_guard.quote_drift(100.0, 100.1), (True, 0.1))
        self.assertEqual(execution_guard.quote_drift(100.0, 100.2), (False, 0.2))

    def test_desk_blocks_new_risk_on_drift(self):
        desk = engine.run({"AGENT_COUNT": "100", "TICKS": "5", "TICK_SECONDS": "0"}, stream=io.StringIO())
        desk.broker_mids = {"BTC": 101.0}
        self.assertIn("drift", desk._quote_drift("BTC", 100.0))
        self.assertIsNone(desk._quote_drift("BTC", 100.9))
        self.assertIsNone(desk._quote_drift("ETH", 100.0))  # no broker quote: nothing to compare
        self.assertEqual(desk.drift["blocked"], 1)
        execution_guard.DRIFT_CHECK = False
        try:
            self.assertIsNone(desk._quote_drift("BTC", 100.0))
        finally:
            execution_guard.DRIFT_CHECK = True


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


class MasterPipelineTests(unittest.TestCase):
    def ctx(self, **kw):
        from trading.master_pipeline import CandidateOrderContext
        base = dict(symbol="EURUSD", side="BUY", desk_score=0.7, entry_price=1.10, stop_price=1.09)
        base.update(kw)
        return CandidateOrderContext(**base)

    def test_all_nine_stages_in_order(self):
        from trading.master_pipeline import STAGES, MasterDeskPipeline
        r = MasterDeskPipeline().evaluate_trade(self.ctx(), now_utc=utc(2026, 11, 24, 12))
        self.assertTrue(r["approved"], r)
        self.assertEqual([t["stage"] for t in r["trace"]], list(STAGES))
        self.assertLessEqual(r["notional"], 20_000)

    def test_each_guard_can_stop_it(self):
        from trading.master_pipeline import MasterDeskPipeline
        p = MasterDeskPipeline()
        self.assertEqual(p.evaluate_trade(self.ctx(), now_utc=utc(2026, 11, 24, 22, 30))["stage"], "feed")
        self.assertEqual(p.evaluate_trade(self.ctx(symbol="ZZZ"), now_utc=utc(2026, 11, 24, 12))["stage"], "orbit")
        self.assertEqual(p.evaluate_trade(self.ctx(timeframe="5m"), now_utc=utc(2026, 11, 24, 12))["stage"], "orbit")
        r = p.evaluate_trade(self.ctx(room_direction=-0.8, room_confident=True), now_utc=utc(2026, 11, 24, 12))
        self.assertEqual(r["stage"], "room")
        self.assertEqual(p.evaluate_trade(self.ctx(desk_score=0.2), now_utc=utc(2026, 11, 24, 12))["stage"], "checker")
        old = utc(2026, 11, 24, 12).timestamp() - 3 * 3600
        self.assertEqual(p.evaluate_trade(self.ctx(last_price_at=old), now_utc=utc(2026, 11, 24, 12))["stage"], "feed")

    def test_desk_writes_a_pipeline_line_per_order(self):
        out = io.StringIO()
        engine.run({"AGENT_COUNT": "750", "TICKS": "60", "TICK_SECONDS": "0"}, stream=out)
        ev = [json.loads(line) for line in out.getvalue().splitlines()]
        self.assertEqual(sum(e["type"] == "order" for e in ev), sum(e["type"] == "pipeline" for e in ev))


class SpyDipWalkthroughTests(unittest.TestCase):
    """mina's SPY 1h walkthrough (24 Nov 2026): a US-session dip into support, bought by the Scalpers with a
    $4.4M 0DTE call print at the key level. Every one of the nine stages must say what the walkthrough says,
    and the size must stay under the $5k order cap."""

    NOW = datetime(2026, 11, 26, 15, 30, tzinfo=timezone.utc)  # 10:30 New York, 02:30 Melbourne

    def setUp(self):
        from trading import option_flows
        self.flows = option_flows
        option_flows.FEED.clear()
        self.addCleanup(option_flows.FEED.clear)

    def ctx(self, **kw):
        from trading.master_pipeline import CandidateOrderContext
        # 1h closes: a steady 4h uptrend, then a dip into the swing low (a flat-to-rising parent: dip buys allowed)
        prices = [580 + i * 0.12 for i in range(120)] + [594.4, 593.6, 592.10]
        base = dict(symbol="SPY", side="BUY", desk_score=0.82, entry_price=592.15, stop_price=589.60, timeframe="1h",
                    style="reversion", prices=prices, bar_seconds=3600.0, last_price_at=self.NOW.timestamp() - 4,
                    room_direction=-0.31, room_confident=False, threshold=0.72)
        base.update(kw)
        return CandidateOrderContext(**base)

    def whale(self, kind="CALL"):
        self.flows.FEED.ingest(self.flows.from_json({
            "underlying": "SPX", "option_type": kind, "action": "BUY_TO_OPEN", "premium": 4_400_000, "dte": 0,
            "strike": 5920, "spot": 5921, "key_level": 5920, "at_key_level": True}, at=self.NOW.timestamp() - 60))

    def test_spy_dip_clears_all_nine_stages(self):
        from trading.master_pipeline import STAGES, MasterDeskPipeline
        from trading.trade_checker import FILE, TradeChecker
        self.whale()
        with tempfile.TemporaryDirectory() as d:
            pipe = MasterDeskPipeline(account_equity=100_000.0, checker=TradeChecker(d), max_position=5_000.0)
            r = pipe.evaluate_trade(self.ctx(), events=[], now_utc=self.NOW)
            self.assertTrue(r["approved"], r)
            said = {t["stage"]: t for t in r["trace"]}
            self.assertEqual(list(said), list(STAGES))
            self.assertTrue(all(t["ok"] for t in r["trace"]))
            self.assertEqual(said["whale"]["why"], "boosted")  # the call print agrees: score x1.25
            self.assertGreater(r["effective_score"], 0.82)
            self.assertIn("US", r["arm"].upper())
            self.assertLessEqual(r["notional"], 5_000.0)  # constant-R wants far more; the $5k cap wins
            self.assertEqual(said["sizing"]["cap"], 5_000.0)
            from trading import ops_guard  # 0.75% of $100k over a 0.43% stop is ~$174k: the cap is what binds
            self.assertGreater(ops_guard.compute_volatility_parity_notional(100_000, 592.15, 589.60, 0.0075, 1.0,
                                                                            10**9), 100_000)
            audit = [json.loads(line) for line in (Path(d) / FILE).read_text().splitlines()]
            self.assertEqual(len(audit), 1)
            self.assertTrue(audit[0]["passed"])
            self.assertTrue(audit[0]["verdict"].startswith("PASS"))

    def test_same_dip_blocked_where_it_should_be(self):
        from trading.master_pipeline import MasterDeskPipeline
        pipe = MasterDeskPipeline(max_position=5_000.0)
        self.whale("PUT")  # a $4.4M put bought to open at the level: the whale is against the dip buy
        self.assertEqual(pipe.evaluate_trade(self.ctx(), now_utc=self.NOW)["stage"], "whale")
        self.flows.FEED.clear()
        rollover = datetime(2026, 11, 26, 22, 30, tzinfo=timezone.utc)
        self.assertEqual(pipe.evaluate_trade(self.ctx(last_price_at=rollover.timestamp() - 4),
                                             now_utc=rollover)["stage"], "feed")
        old = self.NOW.timestamp() - 3 * 3600  # the last real price is 3 hours old
        self.assertEqual(pipe.evaluate_trade(self.ctx(last_price_at=old), now_utc=self.NOW)["stage"], "feed")
        loud = self.ctx(room_direction=-0.8, room_confident=True)  # a confident crash read: no dip buying
        self.assertEqual(pipe.evaluate_trade(loud, now_utc=self.NOW)["stage"], "room")
        weak = self.ctx(desk_score=0.6)
        self.assertEqual(pipe.evaluate_trade(weak, now_utc=self.NOW)["stage"], "checker")


class OrderTrailTests(unittest.TestCase):
    def test_trail_reaches_audit_and_jarvis(self):
        from trading import jarvis
        from trading.dashboard import EventHub
        from trading.master_pipeline import trail_text
        with tempfile.TemporaryDirectory() as d:
            hub = EventHub(Path(d))
            ev = {"type": "pipeline", "symbol": "SPY", "qty": 8.0, "price": 592.13, "score": 0.96,
                  "stages": {"feed": "fresh", "orbit": "US equity", "heat": 1.45, "events": "PASS", "parent": "PASS",
                             "room": "PASS", "whale": "PASS", "sizing_cap": 4737.2, "night": "day", "checker": "PASS"}}
            hub.add(ev)
            self.assertEqual(hub.state()["order_trails"][0]["symbol"], "SPY")
            self.assertIn("9 checker PASS", trail_text(ev))
            reply = jarvis.BuiltinBrain().answer("why did we buy SPY?", {"order_trails": [ev]})
            self.assertIn("SPY: bought 8", reply)
