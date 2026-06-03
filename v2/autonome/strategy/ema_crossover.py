"""
autonome/strategy/ema_crossover.py  v1.0
Trend-following strategy: EMA(9) crosses EMA(21) with volume confirmation.

Logic:
- LONG when EMA(9) crosses above EMA(21)
- Volume confirmation: volume > 1.2× 20-period average
- Stop: below recent swing low or EMA(21), whichever is lower
- Target: trailing stop at EMA(21) or 6×ATR
"""
from __future__ import annotations

import logging
from typing import Optional, List

from autonome.strategy.momentum_breakout import Signal
from autonome.data.bars import Bar, BarStore

log = logging.getLogger("strategy.ema_cross")


def _ema_series(values: List[float], period: int) -> List[float]:
    if len(values) < period:
        return []
    k = 2.0 / (period + 1)
    result = [sum(values[:period]) / period]
    for v in values[period:]:
        result.append(v * k + result[-1] * (1 - k))
    return result


def _atr(ohlc: List[tuple[float, float, float]], period: int = 14) -> Optional[float]:
    if len(ohlc) < period + 1:
        return None
    trs = []
    for i in range(1, len(ohlc)):
        high, low, close = ohlc[i]
        _, _, prev_close = ohlc[i-1]
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        trs.append(tr)
    return sum(trs[-period:]) / period


def _find_swing_low(lows: List[float], lookback: int = 5) -> float:
    """Find lowest low in the last N bars."""
    return min(lows[-lookback:]) if len(lows) >= lookback else min(lows)


class EMACrossover:
    def __init__(self, params: dict):
        self.ema_fast = params.get("ema_fast", 9)
        self.ema_slow = params.get("ema_slow", 21)
        self.atr_period = 14
        self.vol_ma_period = 20
        self.vol_mult = params.get("vol_mult", 1.2)
        self.stop_mode = params.get("stop_mode", "trailing_ema")  # trailing_ema | swing_low
        self.cooldown = 5  # bars after exit before re-entry
        self._last_trade_bar_idx: Optional[int] = None

    def scan(self, symbol: str, store: BarStore, global_bar_idx: int = 0) -> Optional[Signal]:
        buf = store.buffers.get(symbol)
        if not buf or len(buf) < self.ema_slow + self.atr_period + 2:
            return None

        # Cooldown
        if self._last_trade_bar_idx is not None and self._last_trade_bar_idx + self.cooldown > global_bar_idx:
            return None

        closes = [b.close for b in buf]
        highs = [b.high for b in buf]
        lows = [b.low for b in buf]
        volumes = [b.volume for b in buf]

        ema_f = _ema_series(closes, self.ema_fast)
        ema_s = _ema_series(closes, self.ema_slow)
        if len(ema_f) < 3 or len(ema_s) < 3:
            return None

        # Detect crossover: previous bar EMA(9) < EMA(21), current bar EMA(9) > EMA(21)
        prev_cross = ema_f[-2] > ema_s[-2] and ema_f[-3] <= ema_s[-3]
        if not prev_cross:
            return None

        # Volume confirmation
        vol_ma = sum(volumes[-self.vol_ma_period:]) / min(len(volumes), self.vol_ma_period)
        if buf[-1].volume < vol_ma * self.vol_mult:
            return None

        entry_price = buf[-1].close

        # Stop logic
        if self.stop_mode == "swing_low":
            stop_loss = _find_swing_low(lows, lookback=5)
        else:
            # Trailing stop: 2×ATR below current EMA(21)
            ohlc = [(b.high, b.low, b.close) for b in buf]
            atr_val = _atr(ohlc, self.atr_period)
            if atr_val is None:
                return None
            stop_loss = ema_s[-1] - 2 * atr_val

        if stop_loss >= entry_price:
            return None

        # Target: 6×ATR for trend following
        ohlc = [(b.high, b.low, b.close) for b in buf]
        atr_val = _atr(ohlc, self.atr_period)
        if atr_val is None:
            return None
        take_profit = entry_price + 6 * atr_val

        # Signal quality: slope of EMA crossover
        slope_f = (ema_f[-1] - ema_f[-3]) / 2
        slope_s = (ema_s[-1] - ema_s[-3]) / 2
        score = 0.5 + min(0.5, (slope_f - max(0, slope_s)) / entry_price * 100)

        self._last_trade_bar_idx = global_bar_idx
        return Signal(
            symbol=symbol,
            direction="LONG",
            confidence=round(score, 2),
            entry_price=round(entry_price, 4),
            stop_loss=round(stop_loss, 4),
            take_profit=round(take_profit, 4),
            meta={
                "strategy": "ema_crossover",
                "ema_fast": round(ema_f[-1], 2),
                "ema_slow": round(ema_s[-1], 2),
                "vol_vs_ma": round(buf[-1].volume / vol_ma, 2),
            },
        )
