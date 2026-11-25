"""MCP server for ATAS through the ATAS MCP Bridge, with our guards (trading/atas_bridge.py) on every order.

Run it on the computer where ATAS runs, for Claude Code, Claude Desktop or JARVIS:
    pip install mcp
    python tools/atas/bridge_mcp.py
Claude Code: claude mcp add atas -- python /path/to/claude-trading-agents/tools/atas/bridge_mcp.py

It reads the app link and token that One account > Connect ATAS saved in ~/.jarvisfeed/config, and asks the app's
Demo/Live switch before every order. Orders are off for real money until all three of these are set: the app on LIVE,
LIVE_MONEY=on on Render, and ATAS_ALLOW_LIVE_ORDERS=true here.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from mcp.server.fastmcp import FastMCP  # noqa: E402

from trading import atas_bridge  # noqa: E402

mcp = FastMCP("atas")
bridge = atas_bridge.from_config()


def _safe(fn, *a, **k):
    try:
        return fn(*a, **k)
    except atas_bridge.GuardError as e:
        return {"ok": False, "blocked": str(e)}
    except ConnectionError as e:
        return {"ok": False, "error": str(e)}


@mcp.tool()
def atas_status() -> dict:
    """Bridge connection, chart symbol, ATAS portfolio, tick size."""
    return _safe(bridge.status)


@mcp.tool()
def atas_quote() -> dict:
    """Last price, best bid/ask with sizes, spread, DOM imbalance."""
    return _safe(bridge.quote)


@mcp.tool()
def atas_dom() -> dict:
    """Depth of market: bids and asks with volume."""
    return _safe(bridge.dom)


@mcp.tool()
def atas_candles() -> dict:
    """Recent candles with volume and delta (up to 2000)."""
    return _safe(bridge.candles)


@mcp.tool()
def atas_position() -> dict:
    """Open position and P&L."""
    return _safe(bridge.position)


@mcp.tool()
def atas_orders() -> dict:
    """Working orders."""
    return _safe(bridge.orders)


@mcp.tool()
def atas_trades() -> dict:
    """Fills this session."""
    return _safe(bridge.trades)


@mcp.tool()
def atas_snapshot() -> dict:
    """Status, quote, DOM, candles, orders, position and trades in one call."""
    return _safe(bridge.snapshot)


@mcp.tool()
def atas_place_order(direction: str, order_type: str, qty: float, stop_loss: float,
                     price: float | None = None, trigger_price: float | None = None) -> dict:
    """Guarded order: buy/sell, market/limit/stop/stoplimit, with a required stop loss (a protective stop is placed)."""
    return _safe(bridge.place_order, direction, order_type, qty, stop_loss, price=price, trigger_price=trigger_price)


@mcp.tool()
def atas_cancel_order(order_id: str) -> dict:
    """Cancel a working order by id."""
    return _safe(bridge.cancel_order, order_id)


@mcp.tool()
def atas_close_position() -> dict:
    """Flatten the open position at market."""
    return _safe(bridge.close_position)


if __name__ == "__main__":
    mcp.run()
