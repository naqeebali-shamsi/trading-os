"""Lightweight backtests for discrete strategy specs on close-only series."""
from __future__ import annotations

from typing import List, Optional

from research.strategy_search.metrics import apply_cost, trade_stats
from research.strategy_search.pattern_backtest import backtest_candle_pattern
from research.strategy_search.specs import StrategySpec


def _trade_stats(trade_returns: List[float]) -> dict:
    return trade_stats(trade_returns)


def _apply_cost(raw_return: float, cost_per_trade: float) -> float:
    return apply_cost(raw_return, cost_per_trade)


def backtest_ma_cross(closes: List[float], *, fast: int, slow: int, cost_per_trade: float) -> dict:
    trades: List[float] = []
    entry = side = None
    for i in range(slow + 1, len(closes)):
        ma_fast = sum(closes[i - fast : i]) / fast
        ma_slow = sum(closes[i - slow : i]) / slow
        price = closes[i]
        if entry is None:
            if ma_fast > ma_slow:
                entry, side = price, "BUY"
            elif ma_fast < ma_slow:
                entry, side = price, "SELL"
        else:
            if side == "BUY":
                raw = (price - entry) / entry
            else:
                raw = (entry - price) / entry
            trades.append(_apply_cost(raw, cost_per_trade))
            entry = side = None
    stats = _trade_stats(trades)
    stats["bars"] = len(closes)
    return stats


def _rsi_series(closes: List[float], period: int) -> List[Optional[float]]:
    if len(closes) < period + 1:
        return [None] * len(closes)
    out: List[Optional[float]] = [None] * len(closes)
    gains: List[float] = []
    losses: List[float] = []
    for i in range(1, len(closes)):
        delta = closes[i] - closes[i - 1]
        gains.append(max(delta, 0.0))
        losses.append(max(-delta, 0.0))
        if i < period:
            continue
        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period
        if avg_loss == 0:
            out[i] = 100.0
        else:
            rs = avg_gain / avg_loss
            out[i] = 100.0 - (100.0 / (1.0 + rs))
    return out


def backtest_rsi_mr(
    closes: List[float],
    *,
    period: int,
    oversold: float,
    overbought: float,
    cost_per_trade: float,
) -> dict:
    rsi = _rsi_series(closes, period)
    trades: List[float] = []
    entry = side = None
    for i in range(period + 1, len(closes)):
        r = rsi[i]
        if r is None:
            continue
        price = closes[i]
        if entry is None:
            if r <= oversold:
                entry, side = price, "BUY"
            elif r >= overbought:
                entry, side = price, "SELL"
        else:
            exit_now = (side == "BUY" and r >= 50) or (side == "SELL" and r <= 50)
            if exit_now:
                if side == "BUY":
                    raw = (price - entry) / entry
                else:
                    raw = (entry - price) / entry
                trades.append(_apply_cost(raw, cost_per_trade))
                entry = side = None
    stats = _trade_stats(trades)
    stats["bars"] = len(closes)
    return stats


def backtest_donchian(closes: List[float], *, period: int, cost_per_trade: float) -> dict:
    trades: List[float] = []
    entry = side = None
    for i in range(period, len(closes)):
        window = closes[i - period : i]
        upper = max(window)
        lower = min(window)
        price = closes[i]
        if entry is None:
            if price > upper:
                entry, side = price, "BUY"
            elif price < lower:
                entry, side = price, "SELL"
        else:
            if side == "BUY" and price < lower:
                raw = (price - entry) / entry
                trades.append(_apply_cost(raw, cost_per_trade))
                entry = side = None
            elif side == "SELL" and price > upper:
                raw = (entry - price) / entry
                trades.append(_apply_cost(raw, cost_per_trade))
                entry = side = None
    stats = _trade_stats(trades)
    stats["bars"] = len(closes)
    return stats


def backtest_spec(
    closes: List[float],
    spec: StrategySpec,
    *,
    cost_per_trade: float,
    candles: List[dict] | None = None,
) -> dict:
    if spec.family == "ma_cross":
        stats = backtest_ma_cross(
            closes,
            fast=int(spec.params["fast"]),
            slow=int(spec.params["slow"]),
            cost_per_trade=cost_per_trade,
        )
    elif spec.family == "rsi_mean_reversion":
        stats = backtest_rsi_mr(
            closes,
            period=int(spec.params["period"]),
            oversold=float(spec.params["oversold"]),
            overbought=float(spec.params["overbought"]),
            cost_per_trade=cost_per_trade,
        )
    elif spec.family == "donchian_breakout":
        stats = backtest_donchian(
            closes,
            period=int(spec.params["period"]),
            cost_per_trade=cost_per_trade,
        )
    elif spec.family == "candle_pattern":
        if not candles:
            stats = _trade_stats([])
            stats["bars"] = len(closes)
        else:
            stats = backtest_candle_pattern(
                candles,
                family=str(spec.params["pattern_family"]),
                hold_bars=int(spec.params["hold_bars"]),
                cost_per_trade=cost_per_trade,
            )
    else:
        stats = _trade_stats([])
        stats["bars"] = len(closes)
    stats["param_count"] = spec.param_count
    return stats


def backtest_spec_recency_halves(
    closes: List[float],
    spec: StrategySpec,
    *,
    cost_per_trade: float,
    candles: List[dict] | None = None,
) -> dict:
    mid = len(closes) // 2
    if mid < 20 or len(closes) - mid < 20:
        return {
            "first": {"trades": 0, "sharpe_proxy": 0.0, "mean_return": 0.0},
            "second": {"trades": 0, "sharpe_proxy": 0.0, "mean_return": 0.0},
        }
    first = backtest_spec(closes[:mid], spec, cost_per_trade=cost_per_trade, candles=(candles[:mid] if candles else None))
    second = backtest_spec(
        closes[mid:],
        spec,
        cost_per_trade=cost_per_trade,
        candles=(candles[mid:] if candles else None),
    )
    return {"first": first, "second": second}
