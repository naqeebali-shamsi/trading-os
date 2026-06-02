"""Shared backtest metrics helpers."""
from __future__ import annotations

import math
from typing import List


def apply_cost(raw_return: float, cost_per_trade: float) -> float:
    return raw_return - cost_per_trade


def trade_stats(trade_returns: List[float]) -> dict:
    if not trade_returns:
        return {
            "trades": 0,
            "win_rate": 0.0,
            "mean_return": 0.0,
            "total_return": 0.0,
            "profit_factor": 0.0,
            "sharpe_proxy": 0.0,
        }
    wins = sum(1 for r in trade_returns if r > 0)
    gross_wins = sum(r for r in trade_returns if r > 0)
    gross_losses = sum(-r for r in trade_returns if r < 0)
    mean = sum(trade_returns) / len(trade_returns)
    var = sum((r - mean) ** 2 for r in trade_returns) / max(len(trade_returns) - 1, 1)
    std = math.sqrt(var)
    sharpe = (mean / std) * math.sqrt(252) if std > 0 else 0.0
    if gross_losses > 0:
        pf = gross_wins / gross_losses
    else:
        pf = float("inf") if gross_wins > 0 else 0.0
    return {
        "trades": len(trade_returns),
        "win_rate": wins / len(trade_returns),
        "mean_return": mean,
        "total_return": sum(trade_returns),
        "profit_factor": pf if math.isfinite(pf) else 999.0,
        "sharpe_proxy": sharpe,
    }
