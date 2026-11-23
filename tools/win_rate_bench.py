"""Win-rate benchmark: the fixed test every brain change is judged by (paper only).

  python tools/win_rate_bench.py 11,12,13,14 '{"ENTRY_FILTERS":"off"}'     # one JSON line per seed

Seeds 1-2 were used to design changes; seeds 11-14 are the unseen (out-of-sample) check. Prints each run's
trader win rate, trades, trader and desk P&L, per-department rates, how alike the traders vote ("herd"), the
trades each entry filter stopped and the loss audit. EF_<NAME> in the JSON overrides an entry_filters setting.
"""
import os, sys, io, json, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from trading import engine, academy, loss_audit, entry_filters, desk as deskmod
def run(seed, ticks=3600, agents=400, extra=None, school=True):
    env = {"AGENT_COUNT": str(agents), "TICKS": str(ticks), "TICK_SECONDS": "0", "SEED": str(seed),
           "SESSIONS": "off", "MEMORY": "off", "READING_ROOM_CLAUDE": "off", "COACH": "off"}
    env.update(extra or {})
    if not school:
        academy.enroll = lambda a: None
    agree = []
    orig = deskmod.Desk.step
    def step(self, prices, headlines=None, now=None):
        orig(self, prices, headlines, now)
        if self.ticks % 50 == 0:
            by = {}
            for a in self.agents:
                if a.role == "trader" and a.workspace.get("last_vote"):
                    by.setdefault(a.symbol, []).append(1 if a.workspace["last_vote"] > 0 else -1)
            for v in by.values():
                if len(v) > 5:
                    agree.append(max(v.count(1), v.count(-1)) / len(v))
    deskmod.Desk.step = step
    for k, v in list(env.items()):
        if k.startswith("EF_"):
            setattr(entry_filters, k[3:], float(v))
    desk = engine.run(env, stream=io.StringIO())
    rows = {}
    for a in desk.agents:
        if a.role != "trader": continue
        r = rows.setdefault(a.department, [0, 0, 0.0])
        r[0] += a.workspace["trade_wins"]; r[1] += a.workspace["trade_losses"]; r[2] += a.pnl
    w = sum(r[0] for r in rows.values()); l = sum(r[1] for r in rows.values())
    eq = desk.broker.equity(desk.last_prices)
    aud = loss_audit.report(); aud.pop("recent")
    return {"seed": seed, "win": round(w/(w+l), 4) if w+l else None, "trades": w+l,
            "pnl": round(sum(r[2] for r in rows.values()), 1), "desk_pnl": round(eq - desk.broker.starting_cash, 1),
            "dept": {k: (round(v[0]/max(1, v[0]+v[1]), 4), v[0]+v[1], round(v[2], 1)) for k, v in rows.items()},
            "herd": round(sum(agree)/len(agree), 3) if agree else None,
            "filtered": dict(entry_filters.COUNTS), "audit": aud["top"]}
if __name__ == "__main__":
    seeds = [int(s) for s in sys.argv[1].split(",")]
    extra = json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}
    school = "noschool" not in sys.argv
    for s in seeds:
        t = time.time(); print(json.dumps(run(s, extra=extra, school=school)), round(time.time()-t), flush=True)
