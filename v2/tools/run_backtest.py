#!/usr/bin/env python3
"""
tools/run_backtest.py  v1.0
CLI to run backtests on momentum breakout strategy.
Usage:
  python3 tools/run_backtest.py --symbol SPY --days 90 --commission 0.0005 --slippage 0.0005
  python3 tools/run_backtest.py --synthetic --n 500 --trend 0.0002
"""
import sys, os, argparse

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from datetime import datetime, timedelta, timezone

from autonome.strategy.momentum_breakout import MomentumBreakout
from autonome.risk.risk_manager import RiskManager
from autonome.backtest.engine import BacktestEngine
from autonome.backtest.metrics import print_report
from autonome.backtest.data_loader import load_from_alpaca, generate_synthetic


def main():
    p = argparse.ArgumentParser(description="Run momentum breakout backtest")
    p.add_argument("--symbol", default="SPY")
    p.add_argument("--days", type=int, default=90)
    p.add_argument("--commission", type=float, default=0.0005)
    p.add_argument("--slippage", type=float, default=0.0005)
    p.add_argument("--synthetic", action="store_true")
    p.add_argument("--n", type=int, default=500)
    p.add_argument("--trend", type=float, default=0.0001)
    p.add_argument("--timeframe", default="1Hour")
    p.add_argument("--entry", choices=["next_open", "close"], default="next_open")
    args = p.parse_args()

    # Load data
    if args.synthetic:
        print(f"Generating {args.n} synthetic bars for {args.symbol}")
        bars = generate_synthetic(args.symbol, n=args.n, trend=args.trend)
    else:
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=args.days)
        print(f"Loading {args.days} days of {args.timeframe} bars for {args.symbol}...")
        try:
            bars = load_from_alpaca(args.symbol, start, end, timeframe=args.timeframe)
        except Exception as e:
            print(f"Alpaca load failed: {e}")
            print("Falling back to synthetic data...")
            bars = generate_synthetic(args.symbol, n=args.n)

    print(f"Loaded {len(bars)} bars.")

    # Strategy + risk
    strat = MomentumBreakout({
        "ema_fast": 9, "ema_slow": 21, "volume_surge_z": 1.5,
        "atr_period": 14, "atr_sl_mult": 2.0, "atr_tp_mult": 3.0,
        "cooldown_bars": 6,
    })
    risk = RiskManager()
    risk.reset_day(100000.0)  # reset daily tracking

    # Engine
    engine = BacktestEngine(
        strategy=strat,
        risk_manager=risk,
        commission_rate=args.commission,
        slippage=args.slippage,
        entry_at=args.entry,
    )

    print("Running backtest...")
    result = engine.run(bars)
    print_report(result, symbol=args.symbol)


if __name__ == "__main__":
    main()
