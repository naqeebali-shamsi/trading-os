"""Backtest discrete candle-pattern strategies using sensory detectors."""
from __future__ import annotations

from typing import Callable, Dict, List, Optional

from sensory.candle_patterns import (
    engulfing,
    hammer,
    harami,
    marubozu,
    pinbar,
    shooting_star,
    three_soldiers_crows,
)

from research.strategy_search.metrics import apply_cost, trade_stats

DETECTOR_BY_FAMILY: Dict[str, Callable] = {
    "engulfing": engulfing,
    "hammer": hammer,
    "shooting_star": shooting_star,
    "harami": harami,
    "three_soldiers_crows": three_soldiers_crows,
    "pinbar": pinbar,
    "marubozu": marubozu,
}


def backtest_candle_pattern(
    candles: List[dict],
    *,
    family: str,
    hold_bars: int,
    cost_per_trade: float,
    min_warmup: int = 3,
) -> dict:
    detector = DETECTOR_BY_FAMILY.get(family)
    if not detector or len(candles) < min_warmup + hold_bars + 1:
        stats = trade_stats([])
        stats["bars"] = len(candles)
        return stats

    trades: List[float] = []
    i = min_warmup
    while i < len(candles):
        history = candles[: i + 1]
        hit = detector(history)
        direction = (hit or {}).get("direction")
        if direction not in ("bullish", "bearish"):
            i += 1
            continue
        entry_idx = i
        try:
            entry_price = float(candles[entry_idx]["close"])
        except (TypeError, ValueError, KeyError):
            i += 1
            continue
        exit_idx = min(entry_idx + int(hold_bars), len(candles) - 1)
        if exit_idx <= entry_idx:
            break
        try:
            exit_price = float(candles[exit_idx]["close"])
        except (TypeError, ValueError, KeyError):
            i += 1
            continue
        if direction == "bullish":
            raw = (exit_price - entry_price) / entry_price
        else:
            raw = (entry_price - exit_price) / entry_price
        trades.append(apply_cost(raw, cost_per_trade))
        i = exit_idx + 1

    stats = trade_stats(trades)
    stats["bars"] = len(candles)
    return stats
