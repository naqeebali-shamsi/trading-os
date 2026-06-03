"""
autonome/strategy/momentum_breakout.py  v2.2
Single strategy: post-breakout momentum with volume confirmation.
Includes earnings avoidance.
"""
from __future__ import annotations

import logging, statistics
from dataclasses import dataclass
from typing import List, Optional

from autonome.data.bars import Bar, BarStore

log = logging.getLogger("strategy.momentum")


@dataclass(frozen=True)
class Signal:
    symbol: str
    direction: str   # LONG or SHORT
    entry_price: float
    stop_loss: float
    take_profit: float
    confidence: float   # 0.0-1.0
    meta: dict


class MomentumBreakout:
    """
    Conditions for LONG:
      1. Close above EMA(21)  (trend filter)
      2. Close above prior bar high  (breakout)
      3. Volume > mean + 1.5*std     (volume confirmation)
      4. Not within N bars of last signal  (cooldown)
    SHORT is symmetric with EMA below.
    Stop = ATR(14) * 2.0; Target = ATR * 3.0.
    """
    def __init__(self, params: dict):
        self.ema_fast = params.get("ema_fast", 9)
        self.ema_slow = params.get("ema_slow", 21)
        self.vol_z = params.get("volume_surge_z", 1.5)
        self.atr_period = params.get("atr_period", 14)
        self.atr_sl_mult = params.get("atr_sl_mult", 2.0)
        self.atr_tp_mult = params.get("atr_tp_mult", 3.0)
        self.min_confirm = params.get("min_bars_confirm", 2)
        self.cooldown = params.get("cooldown_bars", 6)
        self._last_idx: dict[str, int] = {}
        self.earnings_calendar = None  # set by supervisor after init
        self.earnings_enabled = True
        self.earnings_buffer_days = 2

    @staticmethod
    def _ema(values: List[float], period: int) -> List[float]:
        if len(values) < period:
            return []
        k = 2.0 / (period + 1)
        ema = [sum(values[:period]) / period]
        for v in values[period:]:
            ema.append(v * k + ema[-1] * (1 - k))
        return ema

    def _atr(self, bars: List[Bar]) -> float:
        if len(bars) < self.atr_period + 1:
            return 0.0
        trs = []
        for i in range(1, len(bars)):
            b = bars[i]
            p = bars[i - 1]
            tr1 = b.high - b.low
            tr2 = abs(b.high - p.close)
            tr3 = abs(b.low - p.close)
            trs.append(max(tr1, tr2, tr3))
        return statistics.mean(trs[-self.atr_period:])

    def _volume_surge(self, bars: List[Bar]) -> bool:
        if len(bars) < 20:
            return False
        vols = [b.volume for b in bars[:-1]]
        mean = statistics.mean(vols)
        try:
            std = statistics.stdev(vols)
        except statistics.StatisticsError:
            std = 0.0
        return bars[-1].volume > mean + self.vol_z * std

    def scan(self, symbol: str, store: BarStore, global_bar_idx: int) -> Optional[Signal]:
        if self.earnings_enabled and self.earnings_calendar:
            if self.earnings_calendar.is_earnings_week(symbol, self.earnings_buffer_days):
                log.info("Skipped %s: earnings within %dd", symbol, self.earnings_buffer_days)
                return None

        bars = store.history(symbol, max(self.ema_slow + self.atr_period + 10, 50))
        if len(bars) < self.ema_slow + 5:
            return None

        closes = [b.close for b in bars]
        ema_fast = self._ema(closes, self.ema_fast)
        ema_slow = self._ema(closes, self.ema_slow)
        if not ema_fast or not ema_slow:
            return None

        last = bars[-1]
        prev = bars[-2]
        atr = self._atr(bars)
        if atr <= 0:
            return None

        if global_bar_idx - self._last_idx.get(symbol, -999) < self.cooldown:
            return None

        if last.close > ema_slow[-1] and last.close > prev.high:
            if self._volume_surge(bars):
                if ema_fast[-1] > ema_slow[-1]:
                    sl = round(last.close - atr * self.atr_sl_mult, 2)
                    tp = round(last.close + atr * self.atr_tp_mult, 2)
                    risk = last.close - sl
                    reward = tp - last.close
                    confidence = min(0.95, reward / (risk + 1e-9) / 3.0)
                    self._last_idx[symbol] = global_bar_idx
                    return Signal(symbol, "LONG", last.close, sl, tp, confidence,
                                  {"type": "breakout_long", "resistance": round(prev.high, 2), "atr": round(atr, 2)})

        if last.close < ema_slow[-1] and last.close < prev.low:
            if self._volume_surge(bars):
                if ema_fast[-1] < ema_slow[-1]:
                    sl = round(last.close + atr * self.atr_sl_mult, 2)
                    tp = round(last.close - atr * self.atr_tp_mult, 2)
                    risk = sl - last.close
                    reward = last.close - tp
                    confidence = min(0.95, reward / (risk + 1e-9) / 3.0)
                    self._last_idx[symbol] = global_bar_idx
                    return Signal(symbol, "SHORT", last.close, sl, tp, confidence,
                                  {"type": "breakout_short", "support": round(prev.low, 2), "atr": round(atr, 2)})

        return None
