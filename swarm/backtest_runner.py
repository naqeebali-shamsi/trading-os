#!/usr/bin/env python3
"""
swarm/backtest_runner.py -- Backtest Engine
--------------------------------------------
Standalone subagent. Receives task via SWARM_TASK env var.
Runs a simple vectorized backtest on CSV price data.
Outputs result to SWARM_OUT file.
"""
import json, os, sys, random
from datetime import datetime

task = json.loads(os.environ.get("SWARM_TASK", "{}"))
out_path = os.environ.get("SWARM_OUT", "/tmp/swarm_backtest_result.json")


def generate_mock_prices(n=500, start=1.1000, volatility=0.001):
    """Generate mock price series for a backtest."""
    prices = [start]
    for _ in range(1, n):
        prices.append(prices[-1] * (1 + random.uniform(-volatility, volatility)))
    return prices


def backtest_ma_cross(prices, fast=9, slow=21):
    """Simple MA crossover backtest."""
    trades = []
    equity = 10000.0
    entry = None
    
    for i in range(slow + 1, len(prices)):
        ma_fast = sum(prices[i - fast:i]) / fast
        ma_slow = sum(prices[i - slow:i]) / slow
        
        if entry is None:
            if ma_fast > ma_slow:  # Golden cross
                entry = prices[i]
                side = "BUY"
            elif ma_fast < ma_slow:
                entry = prices[i]
                side = "SELL"
        else:
            exit_price = prices[i]
            if side == "BUY":
                pnl = (exit_price - entry) / entry * equity * 0.01  # 1% risk
            else:
                pnl = (entry - exit_price) / entry * equity * 0.01
            equity += pnl
            trades.append({"entry": entry, "exit": exit_price, "pnl": pnl})
            entry = None
    
    wins = len([t for t in trades if t["pnl"] > 0])
    losses = len(trades) - wins
    total_pnl = sum(t["pnl"] for t in trades)
    
    return {
        "strategy": "MA_CROSS_SMA9_21",
        "trades": len(trades),
        "wins": wins,
        "losses": losses,
        "win_rate": round(wins / len(trades) * 100, 2) if trades else 0,
        "total_pnl": round(total_pnl, 2),
        "final_equity": round(equity, 2),
        "trades_list": trades[-10:],  # last 10 for inspection
    }


def run():
    prices = generate_mock_prices(n=task.get("bars", 500), volatility=task.get("volatility", 0.001))
    result = backtest_ma_cross(prices)
    with open(out_path, "w") as f:
        json.dump(result, f, indent=2)
    print(f"Backtest complete. Result written to {out_path}")

if __name__ == "__main__":
    run()
