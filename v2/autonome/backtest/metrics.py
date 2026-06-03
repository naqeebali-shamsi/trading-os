"""
autonome/backtest/metrics.py  v1.1
Performance metrics for backtest results.
"""
from __future__ import annotations

import math, statistics
from datetime import datetime
from typing import List, Dict

from autonome.backtest.engine import BacktestResult, TradeResult


def compute_metrics(result: BacktestResult) -> Dict:
    """Compute full performance metrics from a BacktestResult."""
    trades = result.trades
    if not trades:
        return {"error": "no trades"}

    equity_curve = result.equity_curve
    initial = result.initial_equity
    final = equity_curve[-1][1] if equity_curve else initial

    pnls = [t.pnl for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]

    total_trades = len(trades)
    win_count = len(wins)
    loss_count = len(losses)
    win_rate = win_count / total_trades if total_trades > 0 else 0.0

    gross_profit = sum(wins) if wins else 0.0
    gross_loss = abs(sum(losses)) if losses else 0.0
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

    avg_win = statistics.mean(wins) if wins else 0.0
    avg_loss = statistics.mean(losses) if losses else 0.0
    avg_trade = statistics.mean(pnls) if pnls else 0.0

    loss_rate = 1.0 - win_rate
    expectancy = (win_rate * avg_win) - (loss_rate * abs(avg_loss))

    equity_values = [e for _, e in equity_curve]
    total_return = (final - initial) / initial if initial > 0 else 0.0

    peak = initial
    max_dd = 0.0
    max_dd_pct = 0.0
    for e in equity_values:
        if e > peak:
            peak = e
        dd = peak - e
        dd_pct = dd / peak if peak > 0 else 0.0
        if dd_pct > max_dd_pct:
            max_dd = dd
            max_dd_pct = dd_pct

    if len(equity_values) >= 2:
        daily_returns = []
        for i in range(1, len(equity_values)):
            if equity_values[i - 1] > 0:
                daily_returns.append(
                    (equity_values[i] - equity_values[i - 1]) / equity_values[i - 1]
                )
        if len(daily_returns) >= 2:
            mean_dr = statistics.mean(daily_returns)
            std_dr = statistics.stdev(daily_returns)
            sharpe = (mean_dr / std_dr) * math.sqrt(252) if std_dr > 0 else 0.0
        else:
            sharpe = 0.0
    else:
        sharpe = 0.0

    calmar = (total_return / max_dd_pct) if max_dd_pct > 0 else float("inf")
    avg_bars = statistics.mean(t.bars_held for t in trades) if trades else 0

    max_consec_losses = 0
    current_consec = 0
    for p in pnls:
        if p <= 0:
            current_consec += 1
            max_consec_losses = max(max_consec_losses, current_consec)
        else:
            current_consec = 0

    return {
        "total_trades": total_trades,
        "win_count": win_count,
        "loss_count": loss_count,
        "win_rate": win_rate,
        "profit_factor": profit_factor,
        "expectancy": expectancy,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "avg_trade": avg_trade,
        "total_return": total_return,
        "total_return_pct": total_return * 100,
        "max_drawdown": max_dd,
        "max_drawdown_pct": max_dd_pct * 100,
        "sharpe": sharpe,
        "calmar": calmar,
        "avg_bars_held": avg_bars,
        "max_consecutive_losses": max_consec_losses,
        "gross_profit": gross_profit,
        "gross_loss": gross_loss,
        "net_pnl": final - initial,
        "final_equity": final,
        "signals_generated": result.signals_generated,
        "signals_regime_rejected": result.signals_regime_rejected,
        "signals_risk_rejected": result.signals_risk_rejected,
    }


def print_report(result: BacktestResult, symbol: str = "") -> None:
    """Pretty-print backtest metrics."""
    m = compute_metrics(result)
    if "error" in m:
        print("No trades to report.")
        return

    print(f"\n{'=' * 60}")
    print(f"  BACKTEST REPORT{f' — {symbol}' if symbol else ''}")
    print(f"{'=' * 60}")
    print(f"  Initial Equity:     ${result.initial_equity:,.2f}")
    print(f"  Final Equity:       ${m['final_equity']:,.2f}")
    print(f"  Total Return:       {m['total_return_pct']:+.2f}%")
    print(f"  Net P&L:            ${m['net_pnl']:+.2f}")
    print(f"  Max Drawdown:       {m['max_drawdown_pct']:.2f}%")
    print(f"  Sharpe (ann):       {m['sharpe']:.2f}")
    print(f"  Calmar:             {m['calmar']:.2f}")
    print(f"  --- Trades ---")
    print(f"  Total Trades:       {m['total_trades']}")
    print(f"  Win Rate:           {m['win_rate']:.1%}")
    print(f"  Profit Factor:      {m['profit_factor']:.2f}")
    print(f"  Expectancy:         ${m['expectancy']:+.2f}")
    print(f"  Avg Win:            ${m['avg_win']:+.2f}")
    print(f"  Avg Loss:           ${m['avg_loss']:+.2f}")
    print(f"  Avg Trade:          ${m['avg_trade']:+.2f}")
    print(f"  Avg Bars Held:      {m['avg_bars_held']:.1f}")
    print(f"  Max Consec Losses:  {m['max_consecutive_losses']}")
    print(f"  Signals: {m['signals_generated']}  RegimeRejected: {m['signals_regime_rejected']}  RiskRejected: {m['signals_risk_rejected']}")
    print(f"{'=' * 60}\n")
