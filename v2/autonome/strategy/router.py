"""
autonome/strategy/router.py  v1.0
Multi-strategy router. Analyzes regime, runs all strategies, picks best signal.
"""
from __future__ import annotations

import logging
from typing import List, Optional, Dict, Any

from autonome.strategy.momentum_breakout import MomentumBreakout, Signal
from autonome.strategy.pullback_to_ema import PullbackToEMA
from autonome.strategy.ema_crossover import EMACrossover
from autonome.strategy.regime import RegimeFilter
from autonome.data.bars import BarStore

log = logging.getLogger("strategy.router")


class StrategyRouter:
    """
    Routes signals to the best strategy based on market regime.

    Strategies:
    - momentum_breakout: for volatile trending markets
    - pullback_to_ema: for range-bound markets with mean reversion
    - ema_crossover: for smooth trending markets

    Selection logic (no LLM):
    1. Compute regime score from EMA slopes + ATR trend
    2. Run all strategies, get candidates
    3. Pick strategy matching regime
    4. Return best candidate signal
    """

    def __init__(self, params: dict, use_llm: bool = False):
        self.strategies = {
            "momentum": MomentumBreakout(params.get("momentum", {})),
            "pullback": PullbackToEMA(params.get("pullback", {})),
            "crossover": EMACrossover(params.get("crossover", {})),
        }
        self.use_llm = use_llm
        self._last_idx: dict[str, int] = {}
        
        self.min_gap = params.get("min_gap", 3)  # bars between signals

    def _regime_score(self, buf: list) -> str:
        """Classify regime from bar buffer."""
        if len(buf) < 55:
            return "unknown"

        closes = [b.close for b in buf]

        # EMA slopes
        def _ema(values, period):
            if len(values) < period:
                return None
            k = 2.0 / (period + 1)
            result = sum(values[:period]) / period
            for v in values[period:]:
                result = v * k + result * (1 - k)
            return result

        ema9 = _ema(closes, 9)
        ema21 = _ema(closes, 21)
        ema50 = _ema(closes, 50)

        if ema9 is None or ema21 is None or ema50 is None:
            return "unknown"

        # Price relative to EMAs
        above_50 = closes[-1] > ema50
        above_21 = closes[-1] > ema21
        nine_above_21 = ema9 > ema21

        # Trend strength: how long has 9 > 21?
        ema9_series = []
        ema21_series = []
        k9 = 2.0 / 10
        k21 = 2.0 / 22
        e9 = sum(closes[:9]) / 9
        e21 = sum(closes[:21]) / 21
        ema9_series.append(e9)
        ema21_series.append(e21)
        for c in closes[9:]:
            e9 = c * k9 + e9 * (1 - k9)
            ema9_series.append(e9)
        for c in closes[21:]:
            e21 = c * k21 + e21 * (1 - k21)
            ema21_series.append(e21)

        # Count consecutive 9>21
        cross_count = 0
        for e9, e2 in zip(reversed(ema9_series[-20:]), reversed(ema21_series[-20:])):
            if e9 > e2:
                cross_count += 1
            else:
                break

        if above_50 and nine_above_21 and cross_count >= 10:
            return "strong_trend"
        elif above_50 and nine_above_21:
            return "trending"
        elif abs(ema9 - ema21) / closes[-1] < 0.005:
            return "range_bound"
        elif closes[-1] < ema50:
            return "downtrend"
        else:
            return "mixed"

    def scan(self, symbol: str, store: BarStore, global_bar_idx: int = 0) -> Optional[Signal]:
        buf = store.buffers.get(symbol)
        if not buf or len(buf) < 55:
            return None

        if self._last_signal_bar is not None and global_bar_idx < self._last_signal_bar + self.min_gap:
            return None

        regime = self._regime_score(list(buf))

        candidates = []
        for name, strat in self.strategies.items():
            sig = strat.scan(symbol, store, global_bar_idx)
            if sig:
                candidates.append((name, sig))

        if not candidates:
            return None

        # Select strategy based on regime
        preferred = {
            "strong_trend": ["crossover", "momentum"],
            "trending": ["momentum", "crossover"],
            "range_bound": ["pullback"],
            "mixed": ["pullback", "crossover"],
            "downtrend": [],  # no long signals in downtrend
            "unknown": ["momentum"],
        }[regime]

        # Find best candidate from preferred strategies
        selected = None
        selected_name = None
        for name, sig in candidates:
            if name in preferred:
                if selected is None or sig.confidence > selected.confidence:
                    selected = sig
                    selected_name = name

        if selected is not None:
            selected.meta["regime"] = regime
            selected.meta["selected_strategy"] = selected_name
            self._last_idx[symbol] = global_bar_idx
            log.info("Router: %s selected %s at bar %d (conf=%.2f)",
                     regime, selected_name, global_bar_idx, selected.confidence)
            return selected

        return None
