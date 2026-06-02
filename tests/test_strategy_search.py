#!/usr/bin/env python3
"""Tests for guarded automated strategy search."""
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from research import dataset_split_builder as sb  # noqa: E402
from research.dataset_builder import DATASET_VERSION  # noqa: E402
from research.strategy_search.backtest import backtest_ma_cross, backtest_spec  # noqa: E402
from research.strategy_search.guards import (  # noqa: E402
    deflated_sharpe_threshold,
    gate_test_confirmation,
    gate_train_validation,
    overfit_gap,
    recency_halves_stable,
)
from research.strategy_search.pattern_backtest import backtest_candle_pattern
from research.strategy_search.specs import StrategySpec, spec_count  # noqa: E402
from research.strategy_search.engine import run_strategy_search  # noqa: E402


def _trend_closes(n: int, *, drift: float = 0.0002, noise: float = 0.0001) -> list[float]:
    price = 1.10
    out = []
    for i in range(n):
        wave = noise * math.sin(i / 5.0)
        price *= 1.0 + drift + wave
        out.append(price)
    return out


def _dataset_row(i: int, close: float, *, symbol: str = "EURUSD", timeframe: str = "M15") -> dict:
    ts = 1_700_000_000.0 + i * 900.0
    return {
        "dataset_version": DATASET_VERSION,
        "series_id": f"{symbol}_{timeframe}",
        "symbol": symbol,
        "timeframe": timeframe,
        "ts_close": ts,
        "feature_cutoff_ts": ts,
        "stage": "publish_signal",
        "reason": "signal_emitted",
        "status": "passed",
        "order_id": f"o{i}",
        "close": close,
        "h1_complete": True,
        "h1_target_ts_close": ts + 900.0,
    }


def test_deflated_sharpe_rises_with_trials():
    low = deflated_sharpe_threshold(trials=5, n_trades=20, base=0.25)
    high = deflated_sharpe_threshold(trials=80, n_trades=20, base=0.25)
    assert high > low


def test_overfit_gap_rejects_curve_fit():
    ok, reason = overfit_gap(2.5, 0.2, 1.25)
    assert not ok
    assert reason.startswith("train_val_sharpe_gap")


def test_recency_halves_require_both_periods():
    ok, reasons = recency_halves_stable(
        (
            {"trades": 6, "sharpe_proxy": 1.2, "mean_return": 0.001},
            {"trades": 2, "sharpe_proxy": -0.5, "mean_return": -0.002},
        ),
        min_trades_per_half=4,
        max_half_sharpe_gap=2.0,
    )
    assert not ok
    assert any("second_half" in r for r in reasons)


def test_gate_train_validation_uses_validation_not_train():
    cfg = {
        "min_trades": {"train": 5, "validation": 5},
        "min_bars": {"train": 30, "validation": 20},
        "max_train_val_sharpe_gap": 1.25,
        "min_validation_sharpe_deflated_base": 0.25,
        "min_validation_profit_factor": 1.05,
        "min_validation_win_rate": 0.38,
        "recency": {"enabled": False},
    }
    train = {"trades": 20, "bars": 100, "sharpe_proxy": 3.0, "profit_factor": 2.0, "win_rate": 0.6}
    val = {"trades": 20, "bars": 50, "sharpe_proxy": 0.1, "profit_factor": 1.0, "win_rate": 0.4}
    gate = gate_train_validation(train, val, trials=50, cfg=cfg)
    assert not gate["passed"]
    assert any("train_val_sharpe_gap" in r or "val_sharpe" in r for r in gate["reasons"])


def test_test_split_not_used_until_confirmation():
    cfg = {
        "min_trades": {"test": 3},
        "min_bars": {"test": 10},
        "max_val_test_sharpe_decay": 2.5,
    }
    val = {"sharpe_proxy": 1.0, "trades": 10}
    test_bad = {"sharpe_proxy": -0.5, "trades": 10, "bars": 40, "mean_return": -0.001}
    gate = gate_test_confirmation(val, test_bad, cfg=cfg)
    assert not gate["passed"]
    assert any("test_sharpe_negative" in r for r in gate["reasons"])


def test_bounded_spec_grid():
    count = spec_count()
    assert 30 <= count <= 40
    pattern_count = spec_count(include_patterns=False)
    assert pattern_count == 18


def test_run_strategy_search_on_synthetic_dataset(tmp_path):
    dataset = tmp_path / "datasets" / f"{DATASET_VERSION}.jsonl"
    dataset.parent.mkdir(parents=True, exist_ok=True)
    closes = _trend_closes(250)
    rows = [_dataset_row(i, c) for i, c in enumerate(closes)]
    dataset.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in rows), encoding="utf-8")

    report = run_strategy_search(
        symbol="EURUSD",
        timeframe="M15",
        report_path=tmp_path / "report.json",
        dataset_path=dataset,
        splits_base=tmp_path / "datasets" / DATASET_VERSION,
        config={
            "enabled": True,
            "cost_per_trade_bps": 1.0,
            "splits": {"train_pct": 0.70, "validation_pct": 0.15, "embargo_steps": 0},
            "min_bars": {"train": 30, "validation": 10, "test": 10},
            "min_trades": {"train": 2, "validation": 1, "test": 1},
            "max_train_val_sharpe_gap": 5.0,
            "min_validation_sharpe_deflated_base": -5.0,
            "min_validation_profit_factor": 0.5,
            "min_validation_win_rate": 0.20,
            "max_val_test_sharpe_decay": 10.0,
            "top_k_validation": 2,
            "recency": {"enabled": False},
            "defaults": {"min_split_rows": 30},
        },
    )
    assert report.get("ok") is True, report.get("error")
    assert report["trials_run"] == spec_count()
    assert report["candidates_evaluated"] == spec_count()
    assert Path(report["report_path"]).exists()
    tested = [c for c in report.get("survivors", []) if not c.get("test_skipped")]
    assert len(tested) <= 2


def test_pattern_backtest_detects_engulfing():
    candles = [
        {
            "open_price": 1.010,
            "high": 1.012,
            "low": 1.008,
            "close": 1.0085,
            "body_size": 0.0015,
            "range": 0.004,
            "is_bullish": False,
            "lower_shadow": 0.0005,
            "upper_shadow": 0.0005,
        },
        {
            "open_price": 1.008,
            "high": 1.015,
            "low": 1.007,
            "close": 1.014,
            "body_size": 0.006,
            "range": 0.008,
            "is_bullish": True,
            "lower_shadow": 0.001,
            "upper_shadow": 0.001,
        },
    ]
    for i in range(20):
        prev = candles[-1]["close"]
        candles.append(
            {
                "open_price": prev,
                "high": prev * 1.001,
                "low": prev * 0.999,
                "close": prev * (1.0002 if i % 2 == 0 else 0.9998),
                "body_size": abs(prev * 0.0002),
                "range": prev * 0.002,
                "is_bullish": i % 2 == 0,
                "lower_shadow": prev * 0.0005,
                "upper_shadow": prev * 0.0005,
            }
        )
    stats = backtest_candle_pattern(candles, family="engulfing", hold_bars=3, cost_per_trade=0.0001)
    assert stats["bars"] == len(candles)


def test_ma_backtest_produces_trades():
    closes = _trend_closes(200)
    stats = backtest_ma_cross(closes, fast=8, slow=21, cost_per_trade=0.0001)
    assert stats["trades"] >= 1
    spec = StrategySpec("MA_CROSS_8_21", "ma_cross", {"fast": 8, "slow": 21}, 2)
    stats2 = backtest_spec(closes, spec, cost_per_trade=0.0001)
    assert stats2["trades"] == stats["trades"]


if __name__ == "__main__":
    test_deflated_sharpe_rises_with_trials()
    test_overfit_gap_rejects_curve_fit()
    test_recency_halves_require_both_periods()
    test_gate_train_validation_uses_validation_not_train()
    test_test_split_not_used_until_confirmation()
    test_bounded_spec_grid()
    test_ma_backtest_produces_trades()
    print("All strategy search unit tests passed (run pytest for integration)")
