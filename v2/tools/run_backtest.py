#!/usr/bin/env python3
"""
tools/run_backtest.py  v3.0
CLI to run backtests. Yahoo Finance primary.
Supports multiple strategies: momentum, pullback, crossover, router.
"""
import sys, os, argparse, csv
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from autonome.strategy.router import StrategyRouter
from autonome.strategy.momentum_breakout import MomentumBreakout
from autonome.strategy.pullback_to_ema import PullbackToEMA
from autonome.strategy.ema_crossover import EMACrossover
from autonome.strategy.regime import RegimeFilter
from autonome.risk.risk_manager import RiskManager
from autonome.backtest.engine import BacktestEngine
from autonome.backtest.metrics import compute_metrics, print_report
from autonome.backtest.data_loader import load_bars_with_regime
from autonome.data.yahoo_feed import fetch_history


def create_strategy(name: str, params: dict):
    if name == "momentum":
        return MomentumBreakout(params.get("momentum", {}))
    elif name == "pullback":
        return PullbackToEMA(params.get("pullback", {}))
    elif name == "crossover":
        return EMACrossover(params.get("crossover", {}))
    elif name == "router":
        return StrategyRouter(params, use_llm=False)
    else:
        raise ValueError(f"Unknown strategy: {name}")


def run_single(
    symbol: str,
    days: int,
    timeframe: str,
    commission: float,
    slippage: float,
    entry: str,
    use_regime: bool,
    strategy_name: str,
    params: dict,
) -> tuple[dict, Any]:
    """Run one backtest configuration. Returns (metrics, result)."""
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days + 10)
    # For 15m data, Yahoo limits history so request shorter window
    if timeframe in ("15m", "30m"):
        start = end - timedelta(days=min(days, 60) + 5)

    if use_regime:
        bars, daily = load_bars_with_regime(symbol, start, end, timeframe)
        regime = RegimeFilter(daily_bars=daily)
    else:
        bars = fetch_history(symbol, start, end, timeframe)
        if not bars:
            print(f"No data for {symbol}, aborting.")
            return {"error": "no_data"}, None
        regime = None

    print(f"Loaded {len(bars)} {timeframe} bars for {symbol}")

    strat = create_strategy(strategy_name, params)
    risk = RiskManager()
    risk.reset_day(100000.0)

    engine = BacktestEngine(
        strategy=strat,
        risk_manager=risk,
        regime_filter=regime,
        commission_rate=commission,
        slippage=slippage,
        entry_at=entry,
    )

    result = engine.run(bars)
    metrics = compute_metrics(result)
    return metrics, result


def main():
    p = argparse.ArgumentParser(description="Run backtest on momentum/p pullback/crossover strategies")
    p.add_argument("--symbol", default="SPY")
    p.add_argument("--days", type=int, default=252)
    p.add_argument("--timeframe", default="1d")
    p.add_argument("--commission", type=float, default=0.0005)
    p.add_argument("--slippage", type=float, default=0.0005)
    p.add_argument("--entry", choices=["next_open", "close"], default="next_open")
    p.add_argument("--regime", action="store_true", help="Apply regime filter")
    p.add_argument("--strategy", choices=["momentum", "pullback", "crossover", "router"], default="router")
    p.add_argument("--sweep", action="store_true", help="Run parameter sweep")
    args = p.parse_args()

    if args.sweep:
        run_sweep(args)
        return

    params = {
        "momentum": {
            "ema_fast": 9, "ema_slow": 21, "volume_surge_z": 1.0,
            "atr_sl_mult": 1.5, "atr_tp_mult": 4.0, "cooldown_bars": 3,
        },
        "pullback": {
            "ema_fast": 21, "ema_slow": 50, "pullback_tolerance": 0.5,
            "stop_mult": 1.0, "tp_mult": 3.0,
        },
        "crossover": {
            "ema_fast": 9, "ema_slow": 21, "vol_mult": 1.2, "stop_mode": "trailing_ema",
        },
    }

    metrics, result = run_single(
        args.symbol, args.days, args.timeframe,
        args.commission, args.slippage, args.entry, args.regime,
        args.strategy, params,
    )
    if "error" not in metrics and result is not None:
        print_report(result, symbol=args.symbol)


def run_sweep(args):
    print(f"\n{'=' * 70}")
    print(f"  PARAMETER SWEEP: {args.symbol} | {args.days} days | {args.timeframe} | {args.strategy}")
    print(f"{'=' * 70}\n")

    # Only sweep for momentum; others are simpler
    if args.strategy != "momentum":
        print("Sweep only supported for momentum strategy. Use --strategy momentum --sweep")
        return

    grids = {
        "volume_surge_z": [0.5, 1.0, 1.5],
        "atr_sl_mult": [1.0, 1.5, 2.0],
        "atr_tp_mult": [3.0, 4.0, 5.0],
        "cooldown_bars": [2, 3, 5],
    }

    results = []
    total = 1
    for v in grids.values():
        total *= len(v)

    count = 0
    for vol_z in grids["volume_surge_z"]:
        for sl in grids["atr_sl_mult"]:
            for tp in grids["atr_tp_mult"]:
                for cd in grids["cooldown_bars"]:
                    count += 1
                    params = {
                        "momentum": {
                            "volume_surge_z": vol_z,
                            "atr_sl_mult": sl,
                            "atr_tp_mult": tp,
                            "cooldown_bars": cd,
                            "ema_fast": 9, "ema_slow": 21,
                        }
                    }
                    print(f"[{count}/{total}] vol_z={vol_z} sl={sl} tp={tp} cd={cd} ...", end=" ")

                    metrics, _ = run_single(
                        args.symbol, args.days, args.timeframe,
                        args.commission, args.slippage, args.entry, args.regime,
                        "momentum", params,
                    )

                    if "error" in metrics:
                        print("NO_DATA")
                        continue

                    print(f"trades={metrics['total_trades']} return={metrics['total_return_pct']:+.2f}% sharpe={metrics['sharpe']:.2f}")
                    results.append({"params": params["momentum"], **metrics})

    if not results:
        print("No valid results.")
        return

    results.sort(key=lambda r: (r["sharpe"], r["total_return"]), reverse=True)

    csv_path = f"/mnt/e/NomadCrew[GROWTH]/trading-os/v2/data/sweep_{args.symbol}_{args.strategy}_{args.days}d.csv"
    os.makedirs(os.path.dirname(csv_path), exist_ok=True)
    with open(csv_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "volume_surge_z", "atr_sl_mult", "atr_tp_mult", "cooldown_bars",
            "total_trades", "win_rate", "total_return_pct", "sharpe", "max_drawdown_pct",
            "profit_factor", "expectancy", "avg_trade", "avg_bars_held",
            "signals_generated", "signals_regime_rejected", "signals_risk_rejected",
        ])
        writer.writeheader()
        for r in results:
            row = {**r["params"]}
            for k in list(r.keys())[1:]:
                row[k] = r[k]
            writer.writerow(row)

    print(f"\n{'=' * 70}")
    print("  TOP 10 CONFIGURATIONS (by Sharpe)")
    print(f"{'=' * 70}")
    for i, r in enumerate(results[:10], 1):
        p = r["params"]
        print(f"  {i}. vol_z={p['volume_surge_z']} sl={p['atr_sl_mult']} tp={p['atr_tp_mult']} cd={p['cooldown_bars']} | "
              f"return={r['total_return_pct']:+.2f}% sharpe={r['sharpe']:.2f} trades={r['total_trades']} win={r['win_rate']:.1%}")

    print(f"\n  Full results saved to: {csv_path}")
    print(f"{'=' * 70}\n")


if __name__ == "__main__":
    main()
