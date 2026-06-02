"""Statistical guardrails for strategy search (overfit, recency, multiple testing)."""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Tuple


def deflated_sharpe_threshold(*, trials: int, n_trades: int, base: float) -> float:
    """Minimum validation Sharpe after correcting for the number of trials searched.

    Inspired by Bailey & Lopez de Prado deflated Sharpe — simplified for discrete grids.
    """
    trials = max(int(trials), 1)
    n_trades = max(int(n_trades), 1)
    if trials <= 1:
        return float(base)
    inflation = math.sqrt(2.0 * math.log(trials) / n_trades)
    return float(base) + inflation


def overfit_gap(train_sharpe: float, val_sharpe: float, max_gap: float) -> Tuple[bool, Optional[str]]:
    gap = float(train_sharpe) - float(val_sharpe)
    if gap > float(max_gap):
        return False, f"train_val_sharpe_gap:{gap:.3f}>{max_gap}"
    return True, None


def recency_halves_stable(
    half_metrics: Tuple[dict, dict],
    *,
    min_trades_per_half: int,
    max_half_sharpe_gap: float,
) -> Tuple[bool, List[str]]:
    """Require both validation halves to have enough trades and similar Sharpe."""
    reasons: List[str] = []
    a, b = half_metrics
    for label, m in (("first_half", a), ("second_half", b)):
        trades = int(m.get("trades") or 0)
        if trades < min_trades_per_half:
            reasons.append(f"{label}_trades<{min_trades_per_half}")
        mean_ret = float(m.get("mean_return") or 0.0)
        if trades >= min_trades_per_half and mean_ret < 0:
            reasons.append(f"{label}_mean_return_negative")

    if not reasons:
        gap = abs(float(a.get("sharpe_proxy") or 0) - float(b.get("sharpe_proxy") or 0))
        if gap > max_half_sharpe_gap:
            reasons.append(f"half_sharpe_gap:{gap:.3f}>{max_half_sharpe_gap}")
    return (not reasons, reasons)


def gate_train_validation(
    train: dict,
    val: dict,
    *,
    trials: int,
    cfg: dict,
) -> dict:
    """First gate: train+validation only — test split is never used here."""
    reasons: List[str] = []
    min_trades = cfg.get("min_trades") or {}
    min_bars = cfg.get("min_bars") or {}

    for split_name, metrics, min_t_key in (
        ("train", train, "train"),
        ("validation", val, "validation"),
    ):
        if int(metrics.get("bars") or 0) < int(min_bars.get(min_t_key) or 0):
            reasons.append(f"{split_name}_bars<{min_bars.get(min_t_key)}")
        if int(metrics.get("trades") or 0) < int(min_trades.get(min_t_key) or 0):
            reasons.append(f"{split_name}_trades<{min_trades.get(min_t_key)}")

    ok_gap, gap_reason = overfit_gap(
        float(train.get("sharpe_proxy") or 0),
        float(val.get("sharpe_proxy") or 0),
        float(cfg.get("max_train_val_sharpe_gap") or 1.25),
    )
    if not ok_gap and gap_reason:
        reasons.append(gap_reason)

    val_trades = max(int(val.get("trades") or 0), 1)
    required_sharpe = deflated_sharpe_threshold(
        trials=trials,
        n_trades=val_trades,
        base=float(cfg.get("min_validation_sharpe_deflated_base") or 0.25),
    )
    val_sharpe = float(val.get("sharpe_proxy") or 0)
    if val_sharpe < required_sharpe:
        reasons.append(f"val_sharpe:{val_sharpe:.3f}<{required_sharpe:.3f}")

    pf = float(val.get("profit_factor") or 0)
    if pf < float(cfg.get("min_validation_profit_factor") or 1.05):
        reasons.append(f"val_profit_factor:{pf:.3f}<{cfg.get('min_validation_profit_factor')}")

    win_rate = float(val.get("win_rate") or 0)
    if win_rate < float(cfg.get("min_validation_win_rate") or 0.38):
        reasons.append(f"val_win_rate:{win_rate:.3f}<{cfg.get('min_validation_win_rate')}")

    recency_cfg = cfg.get("recency") or {}
    if recency_cfg.get("enabled", True):
        halves = val.get("recency_halves") or {}
        ok_rec, rec_reasons = recency_halves_stable(
            (halves.get("first") or {}, halves.get("second") or {}),
            min_trades_per_half=int(recency_cfg.get("min_trades_per_half") or 4),
            max_half_sharpe_gap=float(recency_cfg.get("max_half_sharpe_gap") or 2.0),
        )
        if not ok_rec:
            reasons.extend(rec_reasons)

    return {
        "passed": not reasons,
        "reasons": reasons,
        "required_validation_sharpe": required_sharpe,
        "validation_sharpe": val_sharpe,
        "selection_score": val_sharpe - 0.05 * int(val.get("param_count") or 0),
    }


def gate_test_confirmation(
    val: dict,
    test: dict,
    *,
    cfg: dict,
) -> dict:
    """Final gate: one-shot test evaluation for validation survivors only."""
    reasons: List[str] = []
    min_trades = cfg.get("min_trades") or {}
    min_bars = cfg.get("min_bars") or {}

    if int(test.get("bars") or 0) < int(min_bars.get("test") or 0):
        reasons.append(f"test_bars<{min_bars.get('test')}")
    if int(test.get("trades") or 0) < int(min_trades.get("test") or 0):
        reasons.append(f"test_trades<{min_trades.get('test')}")

    test_sharpe = float(test.get("sharpe_proxy") or 0)
    val_sharpe = float(val.get("sharpe_proxy") or 0)
    # Test may be noisier — require non-negative Sharpe and no catastrophic decay vs validation.
    if test_sharpe < 0:
        reasons.append(f"test_sharpe_negative:{test_sharpe:.3f}")
    decay = val_sharpe - test_sharpe
    max_decay = float(cfg.get("max_val_test_sharpe_decay") or 2.5)
    if decay > max_decay:
        reasons.append(f"val_test_sharpe_decay:{decay:.3f}>{max_decay}")

    mean_ret = float(test.get("mean_return") or 0)
    if int(test.get("trades") or 0) >= int(min_trades.get("test") or 0) and mean_ret <= 0:
        reasons.append("test_mean_return_non_positive")

    return {"passed": not reasons, "reasons": reasons}
