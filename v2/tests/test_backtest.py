"""
v2/tests/test_backtest.py
Backtest engine tests.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from autonome.backtest.engine import BacktestEngine
from autonome.backtest.metrics import compute_metrics
from autonome.backtest.data_loader import generate_synthetic
from autonome.strategy.momentum_breakout import MomentumBreakout
from autonome.risk.risk_manager import RiskManager


def test_synthetic_backtest_runs_without_errors():
    bars = generate_synthetic("FAKE", n=200, trend=0.0002, volatility=0.015)
    strat = MomentumBreakout({
        "ema_fast": 9, "ema_slow": 21, "volume_surge_z": 1.0,
        "atr_period": 14, "atr_sl_mult": 2.0, "atr_tp_mult": 3.0,
        "cooldown_bars": 0,
    })
    risk = RiskManager()
    risk.reset_day(100000.0)

    engine = BacktestEngine(
        strategy=strat,
        risk_manager=risk,
        commission_rate=0.0005,
        slippage=0.0005,
    )
    result = engine.run(bars)

    assert result is not None
    metrics = compute_metrics(result)
    assert "error" not in metrics  # should have trades in 200 bars with this vol
    print(f"Trades: {metrics['total_trades']}, Return: {metrics['total_return_pct']:+.2f}%, WinRate: {metrics['win_rate']:.1%}")


def test_empty_bars_returns_no_trades():
    strat = MomentumBreakout({})
    risk = RiskManager()
    risk.reset_day(100000.0)
    engine = BacktestEngine(strategy=strat, risk_manager=risk)
    result = engine.run([])
    assert len(result.trades) == 0
    print("PASS: empty bars handled")


def test_metrics_computation():
    # 2 winning trades, 1 losing trade
    from autonome.backtest.engine import BacktestResult, TradeResult
    from datetime import datetime, timezone

    trades = [
        TradeResult("X", "LONG", datetime.now(timezone.utc), datetime.now(timezone.utc),
                    100.0, 110.0, 10.0, 100.0, 0.10, "target", 0.02, 5),
        TradeResult("X", "LONG", datetime.now(timezone.utc), datetime.now(timezone.utc),
                    100.0, 90.0, 10.0, -100.0, -0.10, "stop", 0.05, 3),
        TradeResult("X", "LONG", datetime.now(timezone.utc), datetime.now(timezone.utc),
                    100.0, 115.0, 10.0, 150.0, 0.15, "target", 0.01, 7),
    ]
    result = BacktestResult(trades=trades, initial_equity=100000.0)
    result.equity_curve = [
        (datetime.now(timezone.utc), 100000.0),
        (datetime.now(timezone.utc), 100100.0),
        (datetime.now(timezone.utc), 100000.0),
        (datetime.now(timezone.utc), 100150.0),
    ]

    m = compute_metrics(result)
    assert m["total_trades"] == 3
    assert m["win_count"] == 2
    assert m["loss_count"] == 1
    assert abs(m["win_rate"] - 0.667) < 0.01
    assert abs(m["total_return"] - 0.0015) < 0.0001
    print("PASS: metrics computation")


if __name__ == "__main__":
    test_synthetic_backtest_runs_without_errors()
    print("PASS: test_synthetic_backtest_runs_without_errors")
    test_empty_bars_returns_no_trades()
    print("PASS: test_empty_bars_returns_no_trades")
    test_metrics_computation()
    print("PASS: test_metrics_computation")
    print("\nALL BACKTEST TESTS PASSED")
