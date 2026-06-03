"""
autonome/strategy/regime.py  v1.1
Market regime filter with pre-computed EMA/ATR.
"""
from __future__ import annotations

import statistics
from typing import List, Optional
from datetime import datetime

from autonome.data.bars import Bar


def ema(values: List[float], period: int) -> List[float]:
    if len(values) < period:
        return []
    k = 2.0 / (period + 1)
    result = [sum(values[:period]) / period]
    for v in values[period:]:
        result.append(v * k + result[-1] * (1 - k))
    return result


def atr(highs: List[float], lows: List[float], closes: List[float], period: int = 14) -> List[float]:
    if len(closes) < period + 1:
        return []
    trs = []
    for i in range(1, len(closes)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
        trs.append(tr)
    if len(trs) < period:
        return []
    atrs = [sum(trs[:period]) / period]
    for t in trs[period:]:
        atrs.append((atrs[-1] * (period - 1) + t) / period)
    return atrs


class RegimeFilter:
    """
    Pre-computed regime filter.
    Only allows trading when price > EMA(50) and ATR is expanding.
    """

    def __init__(
        self,
        daily_bars: List[Bar],
        ema_period: int = 50,
        atr_period: int = 14,
        atr_lookback: int = 20,
    ):
        self.daily_bars = daily_bars
        self.ema_period = ema_period
        self.atr_period = atr_period
        self.atr_lookback = atr_lookback

        # Pre-compute EMA and ATR series for fast lookup
        self._precompute()

    def _precompute(self):
        if len(self.daily_bars) < self.ema_period + self.atr_period + 1:
            self.regimes = []
            return

        closes = [b.close for b in self.daily_bars]
        highs = [b.high for b in self.daily_bars]
        lows = [b.low for b in self.daily_bars]

        ema_vals = ema(closes, self.ema_period)
        # EMA starts at index ema_period-1; pad with None
        self.ema_series = [None] * (self.ema_period - 1) + ema_vals

        atr_vals = atr(highs, lows, closes, self.atr_period)
        # ATR starts at index atr_period; pad with None
        self.atr_series = [None] * self.atr_period + atr_vals

        # Build regime decisions for each bar
        self.regimes: List[tuple[bool, str]] = []
        for i in range(len(self.daily_bars)):
            if self.ema_series[i] is None or self.atr_series[i] is None:
                self.regimes.append((False, "warmup"))
                continue

            bar = self.daily_bars[i]
            if bar.close <= self.ema_series[i]:
                self.regimes.append((False, f"below_ema{self.ema_period}"))
                continue

            # ATR > median of last N ATR readings
            atr_start = max(0, i - self.atr_lookback + 1)
            atr_slice = [a for a in self.atr_series[atr_start:i+1] if a is not None]
            if not atr_slice:
                self.regimes.append((False, "no_atr_history"))
                continue
            atr_median = statistics.median(atr_slice)
            if self.atr_series[i] < atr_median:
                self.regimes.append((False, "vol_compression"))
                continue

            self.regimes.append((True, "ok"))

    def check(self, current_time: datetime) -> tuple[bool, str]:
        """Fast O(log n) lookup via bisect."""
        import bisect
        # Find index of daily bar at or before current_time
        times = [b.t for b in self.daily_bars]
        idx = bisect.bisect_right(times, current_time) - 1
        if idx < 0 or idx >= len(self.regimes):
            return False, "no_data"
        return self.regimes[idx]
