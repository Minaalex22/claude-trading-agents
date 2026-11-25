"""Check the MetaTrader 5 demo link through MetaApi once (mina, 25 Nov 2026).

    METAAPI_TOKEN=... METAAPI_MT5_DEMO_ACCOUNT_ID=... python scripts/verify_metaapi_demo.py [SYMBOL] [DESK_PRICE]

Run it in the Render Shell (the secrets are already in the Environment tab) or on your computer. It uses the app's
own MetaApi client (trading/metaapi.py, plain REST: no extra package), confirms the account is a DEMO account, prints
its balance and equity, reads one live broker price and, given the paper desk's price, the quote drift against the
0.15% limit (trading/execution_guard.py). Read-only: it places no orders. Never paste the token into a chat.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from trading import execution_guard, metaapi  # noqa: E402


def verify(env=os.environ, symbol: str = "EURUSD", desk_price: float | None = None, http=metaapi.http_json) -> dict:
    if not metaapi.configured(env):
        return {"ok": False, "error": "set METAAPI_TOKEN (and METAAPI_MT5_DEMO_ACCOUNT_ID or METAAPI_ACCOUNT_ID)"}
    try:
        api = metaapi.MetaApi(env, http)
        acct = api.account()
        out = {"ok": True, "account": metaapi.public_account(acct)}
        out["demo"] = out["account"]["demo"]
        if not out["demo"]:
            out["ok"] = False
            out["error"] = "this MetaApi account is NOT a demo account: the app keeps MT5 orders on demo only"
        mid = api.price(symbol)
        out["broker_price"] = {symbol: mid}
        if desk_price:
            within, gap = execution_guard.quote_drift(float(desk_price), mid)
            out["drift"] = {"desk": float(desk_price), "broker": mid, "gap_pct": gap,
                            "limit_pct": round(execution_guard.DRIFT_MAX * 100, 3), "within": within}
        return out
    except Exception as e:  # noqa: BLE001 - shown to the person, never the token
        return {"ok": False, "error": metaapi.explain(e)}


if __name__ == "__main__":
    sym = sys.argv[1] if len(sys.argv) > 1 else "EURUSD"
    desk = float(sys.argv[2]) if len(sys.argv) > 2 else None
    res = verify(symbol=sym, desk_price=desk)
    print(json.dumps(res, indent=2, default=str))
    sys.exit(0 if res["ok"] else 1)
