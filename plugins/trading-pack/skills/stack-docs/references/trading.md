# Orders, brokers, platforms and risk

Read the `trading-safety` skill too. Paper trading only.

- **Paper broker:** `trading/broker.py` `PaperBroker(cash, fee_bps)` — `fill`, `position`, `equity`,
  `gross_exposure`.
- **Risk:** `trading/risk.py` `RiskLimits`, `RiskManager(limits, broker, kill_path)` —
  `approve(symbol, qty, prices)` returns `(qty_allowed, reason_or_None)`, `stop_reason()`,
  `update(prices)`. Every order goes through `approve`. A kill file stops trading.
- **Platforms:** `trading/platforms.py` — `LIVE_TRADING_ENABLED = False` (locked), `Platform`,
  `Connector.place_order(..., live=False)`, `resolve_mode`, `LiveTradingLocked`. Live is refused.
- **Bridge:** `trading/bridge.py` (moomoo paper account unless the real-money phrase is set; never set it).
- **Live proposals:** `trading/live_orders.py`, approved by a person through `/api/live/decide`.
- **Market data:** `trading/market.py`, `trading/live_market.py`, `trading/markets/`, order flow in
  `trading/orderflow.py`.
- **Prediction markets:** `trading/polymarket.py`, `trading/predictor.py`, `trading/xmarket.py`
  (read-only for Australian users).
