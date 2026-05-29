#!/usr/bin/env python3
"""BacktesterAgent: walk-forward split replay with candle-lake MA fallback."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from rd.agents.base import DreamAgent  # noqa: E402
from rd.config import load_config  # noqa: E402
from rd import promotions  # noqa: E402

DATA_ROOT = ROOT / "data_lake"


def _load_closes(symbol: str = "EURUSD", timeframe: str = "M15", limit: int = 800) -> List[float]:
    path = DATA_ROOT / f"symbol={symbol.upper()}" / f"timeframe={timeframe.upper()}" / "candles.jsonl"
    if not path.exists():
        return []
    closes = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        close = row.get("close")
        if close is not None:
            try:
                closes.append(float(close))
            except (TypeError, ValueError):
                continue
    return closes[-limit:]


def _backtest_ma(prices: List[float], fast: int = 9, slow: int = 21) -> Dict[str, Any]:
    trades = []
    equity = 10000.0
    entry = side = None
    for i in range(slow + 1, len(prices)):
        ma_fast = sum(prices[i - fast : i]) / fast
        ma_slow = sum(prices[i - slow : i]) / slow
        if entry is None:
            if ma_fast > ma_slow:
                entry, side = prices[i], "BUY"
            elif ma_fast < ma_slow:
                entry, side = prices[i], "SELL"
        else:
            exit_price = prices[i]
            if side == "BUY":
                pnl = (exit_price - entry) / entry * equity * 0.01
            else:
                pnl = (entry - exit_price) / entry * equity * 0.01
            equity += pnl
            trades.append(pnl)
            entry = side = None
    if not trades:
        return {"trades": 0, "win_rate": 0.0, "total_pnl": 0.0, "sharpe_proxy": 0.0}
    wins = sum(1 for p in trades if p > 0)
    mean = sum(trades) / len(trades)
    var = sum((p - mean) ** 2 for p in trades) / max(len(trades) - 1, 1)
    std = var ** 0.5
    sharpe = (mean / std) * (252 ** 0.5) if std > 0 else 0.0
    return {
        "trades": len(trades),
        "win_rate": round(wins / len(trades), 4),
        "total_pnl": round(sum(trades), 2),
        "sharpe_proxy": round(sharpe, 4),
    }


def _closes_from_rows(rows: List[dict]) -> List[float]:
    closes: List[float] = []
    for row in sorted(rows, key=lambda r: float(r.get("ts_close") or 0.0)):
        close = row.get("close")
        if close is None:
            continue
        try:
            closes.append(float(close))
        except (TypeError, ValueError):
            continue
    return closes


def _load_dataset_rows(path: Path, symbol: str, timeframe: str) -> List[dict]:
    from research.dataset_builder import iter_jsonl  # noqa: WPS433

    rows: List[dict] = []
    sym = symbol.upper()
    tf = timeframe.upper()
    for _, row, err in iter_jsonl(path):
        if err or not row:
            continue
        if str(row.get("symbol") or "").upper() != sym:
            continue
        if str(row.get("timeframe") or "").upper() != tf:
            continue
        if not row.get("series_id") or row.get("ts_close") is None:
            continue
        rows.append(row)
    return rows


def _load_persisted_splits(out_base: Path) -> Optional[Tuple[Dict[str, List[dict]], dict]]:
    from research import dataset_split_builder as sb  # noqa: WPS433

    manifest_path = out_base.with_suffix(".splits.manifest.json")
    if not manifest_path.exists():
        return None
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if not manifest.get("complete"):
        return None
    outputs: Dict[str, List[dict]] = {}
    for split in sb.SPLITS:
        path = out_base.with_suffix(f".{split}.jsonl")
        if not path.exists():
            return None
        try:
            outputs[split] = sb.load_rows(path)
        except ValueError:
            return None
    return outputs, manifest


def _primary_split_metrics(split_results: Dict[str, dict]) -> Tuple[dict, str]:
    for name in ("validation", "test", "train"):
        metrics = split_results.get(name)
        if metrics and int(metrics.get("trades") or 0) > 0:
            return metrics, name
    for name in ("validation", "test", "train"):
        metrics = split_results.get(name)
        if metrics:
            return metrics, name
    return {"trades": 0, "win_rate": 0.0, "total_pnl": 0.0, "sharpe_proxy": 0.0}, "validation"


def _try_walk_forward_backtest(
    *,
    symbol: str,
    task: Dict[str, Any],
    cfg: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    if task.get("force_candle_lake"):
        return None
    try:
        from data_lake import TRAINING_ROOT  # noqa: WPS433
        from research.dataset_builder import DATASET_VERSION  # noqa: WPS433
        from research import dataset_split_builder as sb  # noqa: WPS433
    except ImportError:
        return None

    timeframe = str(task.get("timeframe") or cfg.get("timeframe") or "M15").upper()
    dataset_path = Path(task["dataset"]) if task.get("dataset") else TRAINING_ROOT / "datasets" / f"{DATASET_VERSION}.jsonl"
    out_base = Path(task["splits_base"]) if task.get("splits_base") else TRAINING_ROOT / "datasets" / DATASET_VERSION
    min_rows = int(task.get("min_split_rows") or cfg.get("min_split_rows", 10))

    outputs: Optional[Dict[str, List[dict]]] = None
    manifest: Optional[dict] = None

    persisted = _load_persisted_splits(out_base)
    if persisted:
        outputs, manifest = persisted
        outputs = {
            split: [
                row
                for row in rows
                if str(row.get("symbol") or "").upper() == symbol.upper()
                and str(row.get("timeframe") or "").upper() == timeframe
            ]
            for split, rows in outputs.items()
        }
    elif dataset_path.exists():
        rows = _load_dataset_rows(dataset_path, symbol, timeframe)
        if len(rows) < min_rows:
            return None
        horizons = sb.infer_horizons(rows)
        train_pct = float(task.get("train_pct") or cfg.get("train_pct", 0.70))
        validation_pct = float(task.get("validation_pct") or cfg.get("validation_pct", 0.15))
        outputs, manifest = sb.build_splits(
            rows,
            train_pct=train_pct,
            validation_pct=validation_pct,
            horizons=horizons,
        )
    else:
        return None

    if not outputs or sum(len(rows) for rows in outputs.values()) == 0:
        return None

    split_results: Dict[str, dict] = {}
    for split in sb.SPLITS:
        closes = _closes_from_rows(outputs[split])
        if len(closes) >= 22:
            split_results[split] = _backtest_ma(closes)
        else:
            split_results[split] = {
                "trades": 0,
                "win_rate": 0.0,
                "total_pnl": 0.0,
                "sharpe_proxy": 0.0,
                "bars": len(closes),
            }
        split_results[split]["bars"] = len(closes)

    primary, primary_split = _primary_split_metrics(split_results)
    return {
        **primary,
        "source": "walk_forward_splits",
        "primary_split": primary_split,
        "split_policy": (manifest or {}).get("split_policy", sb.SPLIT_POLICY),
        "splits": split_results,
        "split_counts": {split: len(outputs[split]) for split in sb.SPLITS},
    }


class BacktesterAgent(DreamAgent):
    name = "backtester"

    def run(self, task: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        task = task or {}
        cfg = load_config()
        symbol = str(task.get("symbol") or "EURUSD")
        strategy_id = str(task.get("strategy_id") or "MA_CROSS_SMA9_21")
        bars = int(task.get("bars") or (cfg.get("backtester") or {}).get("default_bars", 500))
        bt_cfg = cfg.get("backtester") or {}

        result = _try_walk_forward_backtest(symbol=symbol, task=task, cfg=bt_cfg)
        if result is None:
            prices = _load_closes(symbol=symbol, limit=bars)
            if len(prices) < 50:
                prices = self._fallback_prices(bars)

            result = _backtest_ma(prices)
            result["source"] = "candle_lake" if len(prices) >= 50 else "synthetic_fallback"

        result["strategy_id"] = strategy_id
        result["symbol"] = symbol

        proposals: List[Dict[str, Any]] = []
        min_trades = int(bt_cfg.get("min_trades", 5))
        if result["trades"] >= min_trades:
            proposal = self._maybe_propose(strategy_id, result, task)
            if proposal:
                proposals.append(proposal)

        return self.envelope({"ok": True, "backtest": result, "proposals": proposals})

    def _fallback_prices(self, n: int) -> List[float]:
        import random

        prices = [1.1]
        for _ in range(n - 1):
            prices.append(prices[-1] * (1 + random.uniform(-0.001, 0.001)))
        return prices

    def _maybe_propose(self, strategy_id: str, result: dict, task: dict) -> Optional[Dict[str, Any]]:
        sharpe = float(result.get("sharpe_proxy") or 0)
        action = None
        patch = {"strategy_id": strategy_id}
        if sharpe >= 1.0:
            action = "promote"
            patch.update({"weight": 1.5, "active": True})
        elif sharpe < -0.3:
            action = "demote"
            patch.update({"weight": 0.2, "active": False})
        else:
            return None

        ptype = "strategy_weight" if action == "promote" else "strategy_active"
        summary = f"{action.title()} {strategy_id} after backtest Sharpe {sharpe:.2f} on {result.get('symbol')}"
        return promotions.propose(
            ptype=ptype,
            summary=summary,
            patch=patch,
            evidence={"backtest": result, "task": task},
            risk="medium" if action == "demote" else "low",
            agent=self.name,
        )


if __name__ == "__main__":
    print(json.dumps(BacktesterAgent().run(), indent=2))
