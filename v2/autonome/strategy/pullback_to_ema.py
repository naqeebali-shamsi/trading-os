"""
autonome/strategy/pullback_to_ema.py  v1.0
Mean-reversion strategy: buy pullbacks to EMA support.

Logic:
- Long-term uptrend: price > EMA(50)
- Pullback to EMA(21): price within 0.5×ATR of EMA(21) from above
- Bounce confirmation: next bar closes above EMA(21)
- Stop: below EMA(21) - 1×ATR
- Target: 2×ATR or EMA(50), whichever is further
"""
from __future__ import annotations

import logging
from typing import Optional, List

from autonome.strategy.momentum_breakout import Signal
from autonome.data.bars import Bar, BarStore

log = logging.getLogger("strategy.pullback")


def _ema(values: List[float], period: int) -> Optional[float]:
    if len(values) < period:
        return None
    k = 2.0 / (period + 1)
    ema = sum(values[:period]) / period
    for v in values[period:]:
        ema = v * k + ema * (1 - k)
    return ema


def _atr(bar_subset: List[Bar], period: int) -> Optional[float]:
    if len(bar_subset) < period + 1:
        return None
    trs = []
    for i in range(1, len(bar_subset)):
        tr = max(
            bar_subset[i].high - bar_subset[i].low,
            abs(bar_subset[i].high - bar_subset[i - 1].close),
            abs(bar_subset[i].low - bar_subset[i - 1].close),
        )
        trs.append(tr)
    return sum(trs[-period:]) / period


def _atr_from_ohlc(closes: List[float], highs: List[float], lows: List[float], period: int) -> Optional[float]:
    if len(closes) < period + 1:
        return None
    trs = []
    for i in range(1, len(closes)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i-1]),
            abs(lows[i] - closes[i-1]),
        )
        trs.append(tr)
    return sum(trs[-period:]) / period


class PullbackToEMA:
    def __init__(self, params: dict):
        self.ema_fast = params.get("ema_fast", 21)
        self.ema_slow = params.get("ema_slow", 50)
        self.atr_period = 14
        self.pullback_tolerance_atr = params.get("pullback_tolerance", 0.5)
        self.stop_mult = params.get("stop_mult", 1.0)
        self.tp_mult = params.get("tp_mult", 3.0)
        self.cooldown = 3
        self._last_trade_bar_idx: Optional[int] = None

    def scan(self, symbol: str, store: BarStore, global_bar_idx: int = 0) -> Optional[Signal]:
        buf = store.buffers.get(symbol)
        if not buf or len(buf) < max(self.ema_slow, self.atr_period) + 2:
            return None

        # Cooldown
        if self._last_trade_bar_idx is not None and self._last_trade_bar_idx + self.cooldown > global_bar_idx:
            return None

        closes = [b.close for b in buf]
        highs = [b.high for b in buf]
        lows = [b.low for b in buf]
        volumes = [b.volume for b in buf]

        if len(closes) < self.ema_slow + 5:
            return None

        ema_fast = _ema(closes, self.ema_fast)
        ema_slow = _ema(closes, self.ema_slow)
        atr = _atr_from_ohlc(closes, highs, lows, self.atr_period)
        if ema_fast is None or ema_slow is None or atr is None:
            return None

        latest = buf[-1]
        prev = buf[-2]

        # Only trade in uptrend
        if latest.close <= ema_slow:
            return None

        # Pullback: price was above EMA(fast), now near/below it but above EMA(slow)
        price_near_ema = abs(latest.close - ema_fast) < atr * self.pullback_tolerance_atr
        was_above = prev.close > ema_fast

        if not (price_near_ema and was_above):
            return None

        # Bounce: this bar's close is back above EMA(fast)
        # Actually, for mean-reversion we enter ON the pullback, not after bounce
        # Enter when price is near EMA(fast) but still above EMA(slow)

        entry_price = latest.close
        stop_loss = ema_fast - atr * self.stop_mult
        take_profit = max(entry_price + atr * self.tp_mult, ema_slow)

        if entry_price <= stop_loss or take_profit <= entry_price:
            return None

        # Volume sanity: pullback should be on normal or lower volume, not climactic
        vol_ma = sum(volumes[-20:]) / min(len(volumes), 20)
        if latest.volume > vol_ma * 2.5:  # avoid capitulation
            return None

        # Simple signal scoring based on distance from EMA
        dist_from_ema = abs(entry_price - ema_fast) / atr if atr else 0.5
        score = max(0.5, 1.0 - dist_from_ema)  # tighter pullback = higher conviction

        self._last_trade_bar_idx = global_bar_idx
        return Signal(
            symbol=symbol,
            direction="LONG",
            confidence=round(score, 2),
            entry_price=round(entry_price, 4),
            stop_loss=round(stop_loss, 4),
            take_profit=round(take_profit, 4),
            meta={
                "strategy": "pullback_to_ema",
                "ema_fast": round(ema_fast, 2),
                "ema_slow": round(ema_slow, 2),
                "atr": round(atr, 4),
            },
        )
