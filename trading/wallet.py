"""Wallet: one place for every balance the app knows about, for the /wallet page.

What it shows:
- Practice wallet: a paper cash balance with its own ledger. The owner can add or take out practice money;
  nothing real moves. Saved in $EVENTS_DIR/wallet.json.
- Paper desk: money the agents' desk made or lost on closed practice trades.
- Polymarket practice desk: locked-in paper profit of the simulated Polymarket bot.
- MetaTrader 5 demo (MetaApi): live balance and equity when METAAPI_TOKEN and METAAPI_ACCOUNT_ID are set
  and the MT5 bridge (trading/metaapi.py) is installed. Read only here.
- eToro, moomoo and the other platforms: connected or not (key names only, never values).
- Crypto watch list: public Bitcoin and Ethereum addresses, balances read from public block explorers.

What it never does: hold private keys or recovery phrases, send money, or trade real funds. Anything that looks
like a private key or seed phrase is refused before it is stored. Real money stays locked
(trading/platforms.py LIVE_TRADING_ENABLED).
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
import urllib.request
from pathlib import Path

START_CASH = 100_000.0
MAX_MOVE = 1_000_000.0
LEDGER_KEEP = 500
MAX_WATCH = 20
BALANCE_TTL = 120  # seconds a fetched on-chain balance is reused
HTTP_TIMEOUT = 8

BTC_ADDR = re.compile(r"(bc1[ac-hj-np-z02-9]{25,62}|[13][a-km-zA-HJ-NP-Z1-9]{25,34})")
ETH_ADDR = re.compile(r"0x[0-9a-fA-F]{40}")
# Shapes of secrets we refuse to store: raw 32-byte keys, WIF keys, extended private keys, seed phrases.
SECRET_SHAPES = (re.compile(r"(0x)?[0-9a-fA-F]{64}"), re.compile(r"[5KL][1-9A-HJ-NP-Za-km-z]{50,51}"),
                 re.compile(r"[xyzt]prv[1-9A-HJ-NP-Za-km-z]{100,}"))
SECRET_MSG = ("That looks like a private key or recovery phrase. Never paste those anywhere, including here. "
              "Only add a public address.")
EXPLORER = {"btc": "https://mempool.space/address/{}", "eth": "https://etherscan.io/address/{}"}


class WalletError(ValueError):
    pass


def looks_secret(text: str) -> bool:
    t = text.strip()
    if len(t.split()) >= 12:  # BIP-39 recovery phrases are 12 to 24 words
        return True
    return any(p.fullmatch(t) for p in SECRET_SHAPES)


def chain_of(address: str) -> str:
    if ETH_ADDR.fullmatch(address):
        return "eth"
    if BTC_ADDR.fullmatch(address):
        return "btc"
    raise WalletError("Not a Bitcoin or Ethereum public address.")


def _http_json(url: str, body: dict | None = None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json",
                                                          "User-Agent": "ats-wallet/1"})
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:  # nosec: fixed public explorer URLs only
        return json.loads(r.read(200_000))


def fetch_balance(chain: str, address: str, http=_http_json) -> float:
    """Public balance in whole coins, from a public explorer. Raises on any network or format problem."""
    if chain == "btc":
        d = http(f"https://blockstream.info/api/address/{address}")
        s = d["chain_stats"]
        return (s["funded_txo_sum"] - s["spent_txo_sum"]) / 1e8
    if chain == "eth":
        d = http("https://cloudflare-eth.com", {"jsonrpc": "2.0", "id": 1, "method": "eth_getBalance",
                                                "params": [address, "latest"]})
        return int(d["result"], 16) / 1e18
    raise WalletError("unknown chain")


def _mt5(env) -> dict:
    """The MT5 demo account through the MetaApi bridge, when it is installed and configured. Read only."""
    try:
        from . import metaapi  # added by the MT5 bridge
    except ImportError:
        return {"state": "not_installed"}
    if not metaapi.configured(env):
        return {"state": "not_connected"}
    try:
        acc = metaapi.public_account(metaapi.MetaApi(env).account())
    except Exception as e:  # network or MetaApi error: show it, never crash the page
        return {"state": "error", "error": type(e).__name__}
    return {"state": "connected", **{k: acc.get(k) for k in ("balance", "equity", "currency", "broker", "demo")}}


class Wallet:
    def __init__(self, events_dir: Path | str, env=os.environ, http=_http_json, clock=time.time):
        self.path = Path(events_dir) / "wallet.json"
        self.env, self.http, self.clock = env, http, clock
        self.lock = threading.Lock()
        self.cache: dict[str, tuple[float, float | None, str | None]] = {}
        self.data = self._load()

    # ---- storage
    def _load(self) -> dict:
        try:
            d = json.loads(self.path.read_text())
            assert isinstance(d.get("ledger"), list) and isinstance(d.get("watch"), list)
            d["cash"] = float(d["cash"])
            return d
        except (OSError, ValueError, KeyError, TypeError, AssertionError):
            return {"cash": START_CASH, "ledger": [{"ts": round(self.clock()), "kind": "start", "amount": START_CASH,
                                                     "balance": START_CASH, "note": "practice wallet opened"}],
                    "watch": []}

    def _save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.data))
            tmp.replace(self.path)
        except OSError:
            pass  # read-only disk: the wallet still works for this run

    # ---- practice money
    def move(self, kind: str, amount, note: str = "") -> dict:
        if kind not in ("deposit", "withdraw"):
            raise WalletError("kind must be deposit or withdraw")
        try:
            amount = round(float(amount), 2)
        except (TypeError, ValueError):
            raise WalletError("amount must be a number") from None
        if not (0 < amount <= MAX_MOVE):
            raise WalletError(f"amount must be between 0 and {MAX_MOVE:,.0f}")
        with self.lock:
            if kind == "withdraw" and amount > self.data["cash"]:
                raise WalletError("not enough practice cash")
            self.data["cash"] = round(self.data["cash"] + (amount if kind == "deposit" else -amount), 2)
            entry = {"ts": round(self.clock()), "kind": kind, "amount": amount, "balance": self.data["cash"],
                     "note": str(note)[:80]}
            self.data["ledger"] = (self.data["ledger"] + [entry])[-LEDGER_KEEP:]
            self._save()
            return entry

    # ---- crypto watch list (public addresses only)
    def watch(self, address: str, label: str = "") -> dict:
        address = str(address).strip()
        if looks_secret(address) or looks_secret(str(label)):
            raise WalletError(SECRET_MSG)
        chain = chain_of(address)
        with self.lock:
            if any(w["address"] == address for w in self.data["watch"]):
                raise WalletError("already on the list")
            if len(self.data["watch"]) >= MAX_WATCH:
                raise WalletError(f"at most {MAX_WATCH} addresses")
            w = {"chain": chain, "address": address, "label": re.sub(r"[^\w .'-]", "", str(label))[:30]}
            self.data["watch"].append(w)
            self._save()
            return w

    def unwatch(self, address: str) -> bool:
        with self.lock:
            before = len(self.data["watch"])
            self.data["watch"] = [w for w in self.data["watch"] if w["address"] != str(address).strip()]
            self._save()
            return len(self.data["watch"]) < before

    def _balance(self, w: dict) -> tuple[float | None, str | None]:
        now = self.clock()
        hit = self.cache.get(w["address"])
        if hit and now - hit[0] < BALANCE_TTL:
            return hit[1], hit[2]
        try:
            bal, err = round(fetch_balance(w["chain"], w["address"], self.http), 8), None
        except Exception:
            bal, err = (hit[1] if hit else None), "couldn't reach the block explorer"
        self.cache[w["address"]] = (now, bal, err)
        return bal, err

    # ---- the whole picture
    def snapshot(self, desk: dict | None = None, polymarket: dict | None = None, platforms: list | None = None,
                 prices: dict | None = None) -> dict:
        desk, prices = desk or {}, prices or {}
        with self.lock:
            cash, ledger, watch = self.data["cash"], list(self.data["ledger"]), [dict(w) for w in self.data["watch"]]
        deposits = sum(e["amount"] for e in ledger if e["kind"] in ("start", "deposit"))
        withdrawals = sum(e["amount"] for e in ledger if e["kind"] == "withdraw")
        desk_pnl = float((desk.get("totals") or {}).get("pnl") or 0.0)
        poly_pnl = float((polymarket or {}).get("total") or 0.0)
        total = round(cash + desk_pnl + poly_pnl, 2)
        accounts = [
            {"key": "practice", "name": "Practice wallet", "kind": "practice", "balance": round(cash, 2),
             "currency": "USD", "state": "connected", "note": "paper cash you can add or take out"},
            {"key": "desk", "name": "Agents' paper desk", "kind": "practice", "balance": round(desk_pnl, 2),
             "currency": "USD", "state": "connected",
             "note": f"profit on {(desk.get('totals') or {}).get('trades', 0)} closed practice trades"},
            {"key": "polymarket", "name": "Polymarket practice desk", "kind": "practice", "balance": round(poly_pnl, 2),
             "currency": "USD", "state": "connected" if polymarket else "idle",
             "note": "simulated books, paper fills" if polymarket else "starts when you open the Polymarket page"},
        ]
        mt5 = _mt5(self.env)
        accounts.append({"key": "mt5", "name": "MetaTrader 5 demo", "kind": "broker", "currency": mt5.get("currency") or "USD",
                         "balance": mt5.get("balance"), "equity": mt5.get("equity"), "state": mt5["state"],
                         "note": {"connected": f"{mt5.get('broker') or 'MT5'} · {'demo' if mt5.get('demo') else 'NOT demo: read only'}",
                                  "not_connected": "add METAAPI_TOKEN and METAAPI_ACCOUNT_ID in Render settings",
                                  "not_installed": "turns on when the MT5 bridge is merged",
                                  "error": f"MetaApi did not answer ({mt5.get('error')})"}[mt5["state"]]})
        for p in platforms or []:
            if p.get("key") in ("metatrader5", "mt5", "polymarket"):
                continue
            accounts.append({"key": p["key"], "name": p["name"], "kind": "broker", "balance": None, "currency": None,
                             "state": ("keys_set" if p.get("configured") else "not_connected") if p.get("needs_keys") else "paper",
                             "note": f"{p.get('mode', 'paper')} mode · " + (
                                 "keys are in the vault" if p.get("configured") and p.get("needs_keys")
                                 else "missing " + ", ".join(p.get("missing") or []) if p.get("missing") else "no keys needed")})
        crypto, crypto_usd = [], 0.0
        for w in watch:
            bal, err = self._balance(w)
            coin = w["chain"].upper()
            px = next((v for k, v in prices.items() if str(k).upper().startswith(coin) and isinstance(v, (int, float))), None)
            usd = round(bal * px, 2) if bal is not None and px else None
            crypto_usd += usd or 0.0
            crypto.append({**w, "balance": bal, "usd": usd, "error": err, "coin": w["chain"].upper(),
                           "explorer": EXPLORER[w["chain"]].format(w["address"])})
        return {"mode": "practice", "live_trading": "locked", "currency": "USD",
                "total": total, "practice_cash": round(cash, 2), "desk_pnl": round(desk_pnl, 2),
                "polymarket_pnl": round(poly_pnl, 2), "deposits": round(deposits, 2),
                "withdrawals": round(withdrawals, 2),
                "profit": round(total - deposits + withdrawals, 2),
                "crypto_usd": round(crypto_usd, 2), "accounts": accounts, "crypto": crypto,
                "ledger": ledger[::-1][:100],
                "rules": ["Practice money only. Nothing real is sent or received.",
                          "Private keys and recovery phrases are refused, never stored.",
                          "Crypto addresses are watched read-only from public explorers.",
                          "Real-money trading stays locked until you are happy with the win rate."]}
