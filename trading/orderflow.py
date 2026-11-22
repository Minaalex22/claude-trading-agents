"""Our own ATAS-style order flow engine.

Feed it trades (price, size, and who hit whom) and it builds what order flow
platforms show: footprint bars (bid/ask volume at each price), delta and
cumulative delta, a volume profile (POC, value area), big trades, stacked
imbalances and absorption. ``OrderFlowBook.view()`` turns that into one
bullish/bearish read that Atlas can use as a trade confirmation.

Works from any trade feed. If the feed doesn't say which side was the
aggressor, the tick rule is used (uptick = buyer, downtick = seller).
"""
from __future__ import annotations

import math
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Iterable

BUY = "buy"    # aggressive buyer lifted the ask
SELL = "sell"  # aggressive seller hit the bid


@dataclass
class Trade:
    ts: float          # seconds since epoch
    price: float
    size: float
    side: str | None = None  # BUY / SELL, or None to infer with the tick rule


@dataclass
class Level:
    bid: float = 0.0  # volume sold into the bid (aggressive sellers)
    ask: float = 0.0  # volume bought at the ask (aggressive buyers)

    @property
    def total(self) -> float:
        return self.bid + self.ask

    @property
    def delta(self) -> float:
        return self.ask - self.bid


@dataclass
class FootprintBar:
    start: float
    open: float
    high: float
    low: float
    close: float
    levels: dict[float, Level] = field(default_factory=lambda: defaultdict(Level))
    big_trades: list[Trade] = field(default_factory=list)
    trades: int = 0

    @property
    def volume(self) -> float:
        return sum(lv.total for lv in self.levels.values())

    @property
    def delta(self) -> float:
        return sum(lv.delta for lv in self.levels.values())

    @property
    def poc(self) -> float | None:
        if not self.levels:
            return None
        return max(self.levels.items(), key=lambda kv: (kv[1].total, -kv[0]))[0]

    def imbalances(self, tick: float, ratio: float = 3.0, min_vol: float = 0.0):
        """Diagonal imbalances, the way footprint charts compare them.

        Buy imbalance at price p: ask volume at p >= ratio x bid volume at p - tick.
        Sell imbalance at price p: bid volume at p >= ratio x ask volume at p + tick.
        """
        buys, sells = [], []
        for p, lv in self.levels.items():
            below = self.levels.get(_snap(p - tick, tick))
            above = self.levels.get(_snap(p + tick, tick))
            b = below.bid if below else 0.0
            a = above.ask if above else 0.0
            if lv.ask > min_vol and lv.ask >= ratio * max(b, 1e-12) and lv.ask > b:
                buys.append(p)
            if lv.bid > min_vol and lv.bid >= ratio * max(a, 1e-12) and lv.bid > a:
                sells.append(p)
        return sorted(buys), sorted(sells)

    def stacked(self, tick: float, ratio: float = 3.0, run: int = 3):
        """Longest runs of consecutive imbalanced prices (>= run in a row)."""
        buys, sells = self.imbalances(tick, ratio)
        return _runs(buys, tick, run), _runs(sells, tick, run)

    def to_dict(self, tick: float) -> dict:
        sb, ss = self.stacked(tick)
        return {
            "start": self.start, "open": self.open, "high": self.high,
            "low": self.low, "close": self.close, "volume": round(self.volume, 8),
            "delta": round(self.delta, 8), "poc": self.poc, "trades": self.trades,
            "levels": {str(p): {"bid": round(lv.bid, 8), "ask": round(lv.ask, 8)}
                       for p, lv in sorted(self.levels.items(), reverse=True)},
            "big_trades": [{"price": t.price, "size": t.size, "side": t.side}
                           for t in self.big_trades],
            "stacked_buy": sb, "stacked_sell": ss,
        }


def _snap(price: float, tick: float) -> float:
    return round(round(price / tick) * tick, 10)


def _runs(prices: list[float], tick: float, run: int) -> list[list[float]]:
    out, cur = [], []
    for p in prices:
        if cur and abs(p - cur[-1] - tick) > tick * 1e-6:
            if len(cur) >= run:
                out.append([cur[0], cur[-1]])
            cur = []
        cur.append(p)
    if len(cur) >= run:
        out.append([cur[0], cur[-1]])
    return out


def volume_profile(levels: dict[float, float], area: float = 0.70) -> dict:
    """POC plus value area (the prices around POC holding ``area`` of volume)."""
    if not levels:
        return {"poc": None, "vah": None, "val": None, "volume": 0.0}
    prices = sorted(levels)
    total = sum(levels.values())
    poc_i = max(range(len(prices)), key=lambda i: (levels[prices[i]], -prices[i]))
    lo = hi = poc_i
    got = levels[prices[poc_i]]
    while got < area * total and (lo > 0 or hi < len(prices) - 1):
        down = levels[prices[lo - 1]] if lo > 0 else -1
        up = levels[prices[hi + 1]] if hi < len(prices) - 1 else -1
        if up >= down:
            hi += 1
            got += levels[prices[hi]]
        else:
            lo -= 1
            got += levels[prices[lo]]
    return {"poc": prices[poc_i], "vah": prices[hi], "val": prices[lo],
            "volume": round(total, 8)}


class OrderFlowBook:
    """Order flow for one symbol: rolling footprint bars plus a session profile."""

    def __init__(self, symbol: str, tick: float, bar_seconds: int = 60,
                 keep_bars: int = 240, big_trade_sigma: float = 3.0,
                 big_trade_min: float = 0.0):
        if tick <= 0:
            raise ValueError("tick must be positive")
        self.symbol = symbol
        self.tick = tick
        self.bar_seconds = bar_seconds
        self.bars: deque[FootprintBar] = deque(maxlen=keep_bars)
        self.cum_delta = 0.0
        self.cvd: deque[float] = deque(maxlen=keep_bars)  # CVD at each bar close
        self.session: dict[float, float] = defaultdict(float)
        self.big_trade_sigma = big_trade_sigma
        self.big_trade_min = big_trade_min
        self._last_price: float | None = None
        self._last_side = BUY
        # running mean/var of trade size (Welford) for big-trade detection
        self._n = 0
        self._mean = 0.0
        self._m2 = 0.0

    # ---- feeding -------------------------------------------------------
    def add(self, trade: Trade) -> None:
        if trade.size <= 0 or not math.isfinite(trade.price):
            return
        side = trade.side or self._tick_rule(trade.price)
        trade = Trade(trade.ts, trade.price, trade.size, side)
        self._last_price, self._last_side = trade.price, side

        start = trade.ts - (trade.ts % self.bar_seconds)
        bar = self.bars[-1] if self.bars else None
        if bar is None or start > bar.start:
            if bar is not None:
                self.cvd.append(self.cum_delta)
            bar = FootprintBar(start, trade.price, trade.price, trade.price, trade.price)
            self.bars.append(bar)
        elif start < bar.start:
            return  # late trade for an old bar; ignore rather than rewrite history

        p = _snap(trade.price, self.tick)
        lv = bar.levels[p]
        if side == BUY:
            lv.ask += trade.size
            self.cum_delta += trade.size
        else:
            lv.bid += trade.size
            self.cum_delta -= trade.size
        bar.high = max(bar.high, trade.price)
        bar.low = min(bar.low, trade.price)
        bar.close = trade.price
        bar.trades += 1
        self.session[p] += trade.size

        if self._is_big(trade.size):
            bar.big_trades.append(trade)
        self._update_size_stats(trade.size)

    def add_many(self, trades: Iterable[Trade]) -> None:
        for t in trades:
            self.add(t)

    def _tick_rule(self, price: float) -> str:
        if self._last_price is None or price == self._last_price:
            return self._last_side
        return BUY if price > self._last_price else SELL

    def _is_big(self, size: float) -> bool:
        if size < self.big_trade_min:
            return False
        if self._n < 30:
            return self.big_trade_min > 0
        sd = math.sqrt(self._m2 / (self._n - 1))
        if sd <= 0:
            return False  # every trade the same size (price-tick mode): nothing stands out
        return size >= self._mean + self.big_trade_sigma * sd

    def _update_size_stats(self, size: float) -> None:
        self._n += 1
        d = size - self._mean
        self._mean += d / self._n
        self._m2 += d * (size - self._mean)

    # ---- reading -------------------------------------------------------
    def profile(self) -> dict:
        return volume_profile(dict(self.session))

    def view(self, lookback: int = 10) -> dict:
        """One read for Atlas: direction (-1..1), confidence (0..1) and why."""
        bars = list(self.bars)[-lookback:]
        if len(bars) < 2:
            return {"symbol": self.symbol, "direction": 0.0, "confidence": 0.0,
                    "reasons": ["not enough order flow yet"], "ready": False}
        reasons: list[str] = []
        score = 0.0
        vol = sum(b.volume for b in bars) or 1.0

        # 1. delta pressure over the lookback
        d = sum(b.delta for b in bars) / vol
        score += 1.5 * d
        if abs(d) > 0.1:
            reasons.append(f"{'buyers' if d > 0 else 'sellers'} in control "
                           f"(delta {d:+.0%} of volume)")

        # 2. price vs CVD divergence (price up, CVD down = weak rally)
        p_move = bars[-1].close - bars[0].open
        cvd_move = sum(b.delta for b in bars)
        rng = max(b.high for b in bars) - min(b.low for b in bars) or self.tick
        if p_move / rng > 0.3 and cvd_move < 0:
            score -= 0.4
            reasons.append("price rising on selling delta (bearish divergence)")
        elif p_move / rng < -0.3 and cvd_move > 0:
            score += 0.4
            reasons.append("price falling on buying delta (bullish divergence)")

        # 3. stacked imbalances in the last 3 bars
        for b in bars[-3:]:
            sb, ss = b.stacked(self.tick)
            if sb:
                score += 0.25
                reasons.append(f"stacked buy imbalance {sb[-1][0]}-{sb[-1][1]}")
            if ss:
                score -= 0.25
                reasons.append(f"stacked sell imbalance {ss[-1][0]}-{ss[-1][1]}")

        # 4. big trades
        big_buy = sum(t.size for b in bars for t in b.big_trades if t.side == BUY)
        big_sell = sum(t.size for b in bars for t in b.big_trades if t.side == SELL)
        if big_buy + big_sell > 0:
            tilt = (big_buy - big_sell) / (big_buy + big_sell)
            score += 0.3 * tilt
            reasons.append(f"big trades lean {'buy' if tilt > 0 else 'sell'} "
                           f"({big_buy:g} vs {big_sell:g})")

        # 5. absorption: heavy one-sided delta at the bar extreme but price
        # closes back the other way
        last = bars[-1]
        if last.levels and last.volume > 0:
            hi_lv = last.levels.get(_snap(last.high, self.tick))
            lo_lv = last.levels.get(_snap(last.low, self.tick))
            mid = (last.high + last.low) / 2
            if hi_lv and hi_lv.ask > 0.25 * last.volume and last.close < mid:
                score -= 0.3
                reasons.append("buying absorbed at the high")
            if lo_lv and lo_lv.bid > 0.25 * last.volume and last.close > mid:
                score += 0.3
                reasons.append("selling absorbed at the low")

        # 6. where price sits vs session value area
        prof = self.profile()
        if prof["vah"] is not None:
            if last.close > prof["vah"]:
                score += 0.15
                reasons.append("trading above value area")
            elif last.close < prof["val"]:
                score -= 0.15
                reasons.append("trading below value area")

        direction = max(-1.0, min(1.0, score))
        confidence = min(1.0, abs(direction) * min(1.0, len(bars) / lookback))
        return {"symbol": self.symbol, "direction": round(direction, 3),
                "confidence": round(confidence, 3), "reasons": reasons,
                "ready": True, "cum_delta": round(self.cum_delta, 8),
                "profile": prof}

    def snapshot(self, bars: int = 30) -> dict:
        """Everything the chart page needs, JSON-ready."""
        return {"symbol": self.symbol, "tick": self.tick,
                "bar_seconds": self.bar_seconds,
                "bars": [b.to_dict(self.tick) for b in list(self.bars)[-bars:]],
                "cvd": list(self.cvd)[-bars:] + [round(self.cum_delta, 8)],
                "profile": self.profile(),
                "session_levels": {str(p): round(v, 8)
                                   for p, v in sorted(self.session.items())},
                "view": self.view()}


def auto_tick(price: float) -> float:
    """A readable price step for a symbol we have no tick size for: about 0.05% of price,
    rounded to 1, 2 or 5 x a power of ten (BTC 60,000 -> 20, a $25 stock -> 0.01)."""
    raw = abs(price) * 0.0005
    if raw <= 0:
        return 0.01
    exp = math.floor(math.log10(raw))
    base = raw / 10 ** exp
    step = 1 if base < 1.5 else 2 if base < 3.5 else 5
    return round(step * 10 ** exp, 10)


class OrderFlowDesk:
    """All symbols' books in one place. The app feeds trades here."""

    def __init__(self, ticks: dict[str, float] | None = None, **book_kwargs):
        self.ticks = dict(ticks or {})
        self.book_kwargs = book_kwargs
        self.books: dict[str, OrderFlowBook] = {}

    def book(self, symbol: str, price: float | None = None) -> OrderFlowBook:
        if symbol not in self.books:
            tick = self.ticks.get(symbol) or (auto_tick(price) if price else 0.01)
            self.books[symbol] = OrderFlowBook(symbol, tick, **self.book_kwargs)
        return self.books[symbol]

    def add(self, symbol: str, trade: Trade) -> None:
        self.book(symbol, trade.price).add(trade)

    def snapshot(self, bars: int = 30) -> dict:
        return {sym: b.snapshot(bars) for sym, b in sorted(self.books.items())}

    def view(self, symbol: str) -> dict:
        if symbol not in self.books:
            return {"symbol": symbol, "direction": 0.0, "confidence": 0.0,
                    "reasons": ["no order flow for this symbol"], "ready": False}
        return self.books[symbol].view()
