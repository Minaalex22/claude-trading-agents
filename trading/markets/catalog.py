"""The 25 products shown in the Markets section."""
from dataclasses import dataclass, asdict


@dataclass(frozen=True)
class Product:
    symbol: str          # our id, used in URLs
    name: str
    kind: str            # crypto | forex | commodity | index | share
    feed_id: str         # id at the data source (Coinbase product or Yahoo ticker)
    base_price: float    # rough level, used by the simulated feed

    def to_dict(self):
        return asdict(self)


PRODUCTS = [
    # crypto (Coinbase: live candles, trades and order book)
    Product("BTC", "Bitcoin", "crypto", "BTC-USD", 95000),
    Product("ETH", "Ethereum", "crypto", "ETH-USD", 3400),
    Product("SOL", "Solana", "crypto", "SOL-USD", 180),
    Product("XRP", "XRP", "crypto", "XRP-USD", 1.1),
    Product("DOGE", "Dogecoin", "crypto", "DOGE-USD", 0.3),
    # forex
    Product("EURUSD", "Euro / US Dollar", "forex", "EURUSD=X", 1.08),
    Product("GBPUSD", "British Pound / US Dollar", "forex", "GBPUSD=X", 1.27),
    Product("USDJPY", "US Dollar / Japanese Yen", "forex", "JPY=X", 152),
    Product("AUDUSD", "Australian Dollar / US Dollar", "forex", "AUDUSD=X", 0.65),
    Product("USDCAD", "US Dollar / Canadian Dollar", "forex", "CAD=X", 1.39),
    # commodities
    Product("GOLD", "Gold", "commodity", "GC=F", 2650),
    Product("SILVER", "Silver", "commodity", "SI=F", 31),
    Product("OIL", "Crude Oil (WTI)", "commodity", "CL=F", 70),
    Product("NATGAS", "Natural Gas", "commodity", "NG=F", 3.1),
    Product("COPPER", "Copper", "commodity", "HG=F", 4.1),
    # indices
    Product("SPX", "S&P 500", "index", "^GSPC", 5900),
    Product("NDX", "Nasdaq 100", "index", "^NDX", 20800),
    Product("DJI", "Dow Jones", "index", "^DJI", 43500),
    Product("ASX200", "ASX 200", "index", "^AXJO", 8300),
    Product("DAX", "DAX 40", "index", "^GDAXI", 19300),
    # shares
    Product("AAPL", "Apple", "share", "AAPL", 228),
    Product("NVDA", "Nvidia", "share", "NVDA", 145),
    Product("TSLA", "Tesla", "share", "TSLA", 340),
    Product("MSFT", "Microsoft", "share", "MSFT", 415),
    Product("AMZN", "Amazon", "share", "AMZN", 205),
]

BY_SYMBOL = {p.symbol: p for p in PRODUCTS}
KINDS = ["crypto", "forex", "commodity", "index", "share"]


def get(symbol: str) -> Product:
    try:
        return BY_SYMBOL[symbol.upper()]
    except KeyError:
        raise KeyError(f"unknown product {symbol!r}")
