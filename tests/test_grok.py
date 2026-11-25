"""Grok (xAI) brain: client parsing, call cap, X pulse scoring, replay, decision head, pipeline crowd, keys panel."""

import io
import json

import pytest

from trading import grok
from trading import live_apis
from trading.news_pipeline import NewsPipeline


class FakeResp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def opener_for(text, seen=None, x_posts=7):
    def _open(req, timeout=None):
        if seen is not None:
            seen.append(json.loads(req.data))
            seen[-1]["_auth"] = req.headers.get("Authorization")
            seen[-1]["_url"] = req.full_url
        body = {"output": [{"type": "custom_tool_call", "name": "x_keyword_search"},
                           {"type": "message", "content": [{"type": "output_text", "text": text,
                                                            "annotations": [{"url": "https://x.com/a/status/1"}]}]}],
                "usage": {"server_side_tool_usage_details": {"x_search_calls": 2, "x_posts_fetched": x_posts}}}
        return FakeResp(json.dumps(body).encode())
    return _open


READ = '{"lean": 0.6, "confidence": 0.8, "why": "traders cheer ETF inflows", "posts": 40, "tag": "inference"}'


def test_off_without_key():
    assert grok.GrokClient.from_env({}) is None
    assert grok.GrokClient.from_env({"XAI_API_KEY": "k", "GROK": "off"}) is None
    p = grok.XPulse.from_env({})
    assert not p.on and p.read("BTC", 0) is None and p.snapshot()["on"] is False


def test_client_sends_x_search_and_parses():
    seen = []
    c = grok.GrokClient("secret", "grok-4.7", opener=opener_for(READ, seen))
    r = c.respond("hi", x_search={"from_date": "2026-11-01", "to_date": "2026-11-02"})
    assert seen[0]["_url"] == "https://api.x.ai/v1/responses"
    assert seen[0]["_auth"] == "Bearer secret"
    assert seen[0]["tools"] == [{"type": "x_search", "from_date": "2026-11-01", "to_date": "2026-11-02"}]
    assert r["citations"] == ["https://x.com/a/status/1"] and r["x_posts"] == 7
    assert c.status()["x_searches_today"] == 2 and "secret" not in json.dumps(c.status())


def test_daily_cap():
    c = grok.GrokClient("k", daily_calls=2, opener=opener_for(READ), clock=lambda: 1_700_000_000)
    c.respond("a"); c.respond("b")
    with pytest.raises(grok.GrokLimit):
        c.respond("c")


def test_parse_read_clamps_and_rejects():
    p = grok.parse_read('sure: {"lean": 3, "confidence": -1, "why": "x"}')
    assert p["lean"] == 1.0 and p["confidence"] == 0.0 and p["tag"] == "unknown"
    assert grok.parse_read("no json here") is None
    assert grok.parse_read('{"lean": "up"}') is None


def test_pulse_votes_and_scores():
    p = grok.XPulse(grok.GrokClient("k", opener=opener_for(READ)), refresh_mins=30, score_hours=4)
    assert p.due(["BTC", "ETH"], 10_000) == ["BTC", "ETH"]
    assert p.round({"BTC": 100.0}, 10_000) == 1
    assert p.lean("BTC", 10_000) == pytest.approx(0.48)
    assert p.votes(["BTC", "ETH"], 10_000) == {"BTC": 0.48, "ETH": None}
    assert p.due(["BTC"], 10_000 + 60) == []
    p.score({"BTC": 101.0}, 10_000 + 4 * 3600)
    st = p.stats()
    assert st["checked"] == 1 and st["hit_rate"] == 1.0
    assert p.lean("BTC", 10_000 + 10 * 3600) is None  # stale pulse abstains


def test_weight_falls_when_grok_is_wrong():
    p = grok.XPulse(None)
    p.scored.extend({"symbol": "BTC", "lean": 0.5, "confidence": 0.8, "move": -0.01, "hit": False, "at": 0}
                    for _ in range(30))
    assert p.weight() == 0.25


def test_replay_scores_next_day():
    day = 86_400
    closes = {"BTC": [(day * i, 100.0 + i) for i in range(1, 8)]}  # always up: a bullish Grok hits every day
    out = grok.replay(grok.GrokClient("k", opener=opener_for(READ)), closes, days=5)
    assert out["votes"] == 5 and out["hit_rate"] == 1.0 and out["follow_pnl_pct"] > 0
    assert out["verdict"] == "not enough votes yet to tell"
    assert grok.verdict(0.6, 40).startswith("helps")


def test_pipeline_adds_crowd_on_deep_read():
    pl = NewsPipeline(seed=1)
    pl.crowd = lambda sym: 0.5
    rec = pl.run({"symbol": "BTC", "text": "Fed holds interest rate"})
    assert rec["route"] == "deep"
    assert any("X crowd (Grok)" in t["text"] for t in rec["deep"]["tags"])
    pl2 = NewsPipeline(seed=1)
    rec2 = pl2.run({"symbol": "BTC", "text": "Fed holds interest rate"})
    assert rec["deep"]["lean"] != rec2["deep"]["lean"] or rec2["deep"]["lean"] == 0.5


def test_decision_desk_grok_head():
    from trading import decision_desk
    trades = {"trades": [], "by_symbol": {}, "totals": {}}
    prices = {"history": [{"prices": {"BTC": 100.0, "ETH": 10.0}}]}
    out = decision_desk.build({}, trades, prices, None, None, {}, {"BTC": 0.5, "ETH": None})
    heads = {d["symbol"]: {h["head"]: h for h in d["heads"]} for d in out["decisions"]}
    assert heads["BTC"]["grok"]["vote"] == "up"
    assert heads["ETH"]["grok"]["vote"] == "abstain"
    without = decision_desk.build({}, trades, prices, None, None, {})
    assert all("grok" not in {h["head"] for h in d["heads"]} for d in without["decisions"])


def test_keys_panel_lists_xai_key_without_value():
    rows = {r["key"]: r for r in live_apis.keys_status({"XAI_API_KEY": "xai-very-secret"})}
    g = rows["grok"]
    assert g["demo_ready"] and not g["live_possible"]
    assert any(v["var"] == "XAI_API_KEY" and v["set"] for v in g["vars"])
    assert "xai-very-secret" not in json.dumps(rows)
    assert not {r["key"]: r for r in live_apis.keys_status({})}["grok"]["demo_ready"]


def test_grok_mini_answers_and_handles_cap():
    c = grok.GrokClient("k", daily_calls=1, opener=opener_for("Sentinel blocked 2 orders."))
    m = grok.GrokMini(c)
    assert m.answer("what are people saying on X about BTC?", {}) == "Sentinel blocked 2 orders."
    assert "cap" in m.answer("again?", {})
