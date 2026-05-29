#!/usr/bin/env python3
"""
sensory/ohlc_engine.py — Multi-Symbol Candle Aggregation
---------------------------------------------------------
Aggregates ticks into OHLC candles for configurable timeframes.
Publishes candle.close events per symbol/TF.
Maintains rolling buffers for pattern analysis.

Supported TFs: M1, M5, M15, M30, H1, H4, D1, W1
"""
import json, time, math
from pathlib import Path
from collections import defaultdict, deque
from dataclasses import dataclass, asdict
from typing import Dict, List, Optional, Tuple

@dataclass
class Candle:
    symbol: str
    timeframe: str
    ts_open: float
    ts_close: float
    open_price: float
    high: float
    low: float
    close: float
    tick_count: int = 0
    volume: float = 0.0  # accumulated price movement proxy

    def to_dict(self):
        d = asdict(self)
        # round prices for JSON cleanliness
        for k in ("open_price", "high", "low", "close", "volume"):
            d[k] = round(d[k], 5)
        d["body_size"] = round(abs(self.close - self.open_price), 5)
        d["range"] = round(self.high - self.low, 5)
        d["upper_shadow"] = round(self.high - max(self.open_price, self.close), 5)
        d["lower_shadow"] = round(min(self.open_price, self.close) - self.low, 5)
        d["is_bullish"] = self.close >= self.open_price
        return d


TF_SECONDS = {
    "M1": 60, "M5": 300, "M15": 900, "M30": 1800,
    "H1": 3600, "H4": 14400, "D1": 86400, "W1": 604800,
}

class OHLCEngine:
    """
    Per-symbol candle builder. Call on_tick() for each incoming tick.
    Active candles are built in-memory; completed candles are broadcast.
    """
    def __init__(self, timeframes: List[str] = None, max_history: int = 500):
        self.timeframes = timeframes or ["M5", "M15", "H1"]
        self.max_history = max_history
        # symbol -> tf -> deque of completed candles
        self.history: Dict[str, Dict[str, deque]] = defaultdict(
            lambda: defaultdict(lambda: deque(maxlen=max_history))
        )
        # symbol -> tf -> current building candle
        self.active: Dict[str, Dict[str, Optional[Candle]]] = defaultdict(dict)

    def _current_period(self, ts: float, tf: str) -> int:
        """Return period index (open timestamp) for given timeframe."""
        sec = TF_SECONDS[tf]
        if tf == "W1":
            # Monday 00:00 UTC as week start
            return math.floor(ts / sec) * sec
        return math.floor(ts / sec) * sec

    def on_tick(self, symbol: str, bid: float, ask: float, ts: float = None) -> List[dict]:
        """
        Process a single tick. Returns list of completed candle dicts that just closed.
        """
        if ts is None:
            ts = time.time()
        completed = []
        for tf in self.timeframes:
            period = self._current_period(ts, tf)
            active = self.active[symbol].get(tf)
            if active is None or period >= active.ts_close:
                # Close previous candle
                if active is not None:
                    self.history[symbol][tf].append(active)
                    completed.append(active.to_dict())
                # Open new candle
                price = bid  # use bid as baseline
                self.active[symbol][tf] = Candle(
                    symbol=symbol, timeframe=tf,
                    ts_open=period, ts_close=period + TF_SECONDS[tf],
                    open_price=price, high=price, low=price, close=price,
                    tick_count=1, volume=abs(ask - bid),
                )
            else:
                active.high = max(active.high, bid, ask)
                active.low = min(active.low, bid, ask)
                active.close = bid
                active.tick_count += 1
                active.volume += abs(ask - bid)
        return completed

    def get_history(self, symbol: str, tf: str, count: int = 50) -> List[dict]:
        """Return last N candle dicts for symbol/TF."""
        buf = self.history[symbol].get(tf, deque())
        return [c.to_dict() for c in list(buf)[-count:]]

    def get_current(self, symbol: str, tf: str) -> Optional[dict]:
        """Return the currently-building candle."""
        c = self.active[symbol].get(tf)
        return c.to_dict() if c else None

    def get_all_symbols(self) -> List[str]:
        return list(self.active.keys())

    def get_multi_tf_snapshot(self, symbol: str, tfs: List[str] = None) -> dict:
        """Get OHLC data across multiple TFs for LLM context."""
        tfs = tfs or self.timeframes
        snap = {}
        for tf in tfs:
            hist = self.get_history(symbol, tf, 5)
            cur = self.get_current(symbol, tf)
            snap[tf] = {
                "history": hist,
                "current": cur,
            }
        return snap


# Singleton instance
ENGINE = OHLCEngine(timeframes=["M1", "M5", "M15", "H1", "H4", "D1"])


def process_bus_tick(bus_event: dict) -> List[dict]:
    """Convenience: feed a market.tick bus event into the engine."""
    p = bus_event.get("payload", bus_event)  # support both raw and bus-wrapped
    return ENGINE.on_tick(
        symbol=p.get("symbol", "UNKNOWN"),
        bid=p.get("bid", 0.0),
        ask=p.get("ask", 0.0),
        ts=p.get("ts", time.time()),
    )

# Test
if __name__ == "__main__":
    e = OHLCEngine(["M1"])
    t = time.time()
    e.on_tick("EURUSD", 1.10000, 1.10002, t)
    e.on_tick("EURUSD", 1.10005, 1.10007, t + 10)
    e.on_tick("EURUSD", 1.09995, 1.09997, t + 30)
    e.on_tick("EURUSD", 1.10010, 1.10012, t + 61)  # should close previous
    print("History candles:", len(e.get_history("EURUSD", "M1")))
    print("Current candle:", e.get_current("EURUSD", "M1"))
