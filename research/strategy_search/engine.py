"""Orchestrate guarded strategy search on purged chronological splits."""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from data_lake import TRAINING_ROOT
from research import dataset_split_builder as sb
from research.dataset_builder import DATASET_VERSION, iter_jsonl
from research.strategy_search.backtest import backtest_spec, backtest_spec_recency_halves
from research.strategy_search.candles import candles_from_rows, load_candles_candle_lake
from research.strategy_search.config import ROOT, load_config
from research.strategy_search.guards import gate_test_confirmation, gate_train_validation
from research.strategy_search.specs import StrategySpec, iter_strategy_specs, spec_count


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
        if row.get("close") is None:
            continue
        rows.append(row)
    return rows


def _load_persisted_splits(out_base: Path) -> Optional[Tuple[Dict[str, List[dict]], dict]]:
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
        outputs[split] = sb.load_rows(path)
    return outputs, manifest


def _load_closes_candle_lake(symbol: str, timeframe: str, *, limit: int = 1200) -> List[float]:
    candles = load_candles_candle_lake(symbol, timeframe, limit=limit)
    return [float(c["close"]) for c in candles if c.get("close") is not None]


def _search_spec_options(cfg: dict) -> dict:
    patterns = cfg.get("patterns") or {}
    include_patterns = bool(patterns.get("enabled", True))
    families = patterns.get("families")
    hold_bars = patterns.get("hold_bars")
    pattern_families = tuple(str(f) for f in families) if families else None
    pattern_hold_bars = tuple(int(h) for h in hold_bars) if hold_bars else None
    return {
        "include_patterns": include_patterns,
        "pattern_families": pattern_families,
        "pattern_hold_bars": pattern_hold_bars,
    }


def load_split_series(
    *,
    symbol: str,
    timeframe: str,
    cfg: dict,
    dataset_path: Path | None = None,
    splits_base: Path | None = None,
) -> Tuple[Dict[str, List[float]], Dict[str, List[dict]], dict]:
    """Load train/validation/test closes and OHLC candles using purged splits."""
    split_cfg = cfg.get("splits") or {}
    train_pct = float(split_cfg.get("train_pct") or 0.70)
    validation_pct = float(split_cfg.get("validation_pct") or 0.15)
    embargo_steps = int(split_cfg.get("embargo_steps") or 1)
    defaults = cfg.get("defaults") or {}
    min_rows = int(defaults.get("min_split_rows") or 30)

    sym = symbol.upper()
    tf = timeframe.upper()
    dataset_path = dataset_path or (TRAINING_ROOT / "datasets" / f"{DATASET_VERSION}.jsonl")
    out_base = splits_base or (TRAINING_ROOT / "datasets" / DATASET_VERSION)

    meta: dict = {"source": None, "split_policy": sb.SPLIT_POLICY}

    persisted = _load_persisted_splits(out_base)
    if persisted:
        outputs, manifest = persisted
        meta["source"] = "persisted_splits"
        meta["manifest"] = manifest
    elif dataset_path.exists():
        rows = _load_dataset_rows(dataset_path, sym, tf)
        if len(rows) < min_rows:
            raise ValueError(f"insufficient_dataset_rows:{len(rows)}<{min_rows}")
        horizons = sb.infer_horizons(rows)
        outputs, manifest = sb.build_splits(
            rows,
            train_pct=train_pct,
            validation_pct=validation_pct,
            horizons=horizons,
            embargo_steps=embargo_steps,
        )
        meta["source"] = "dataset_builder"
        meta["manifest"] = manifest
    else:
        candles = load_candles_candle_lake(sym, tf)
        if len(candles) < min_rows:
            raise ValueError(f"insufficient_candle_lake_bars:{len(candles)}<{min_rows}")
        pseudo_rows = []
        for i, candle in enumerate(candles):
            pseudo_rows.append(
                {
                    "series_id": f"{sym}_{tf}",
                    "symbol": sym,
                    "timeframe": tf,
                    "ts_close": float(candle.get("ts_close") or i),
                    "open_price": candle.get("open_price"),
                    "high": candle.get("high"),
                    "low": candle.get("low"),
                    "close": candle.get("close"),
                    "h1_complete": True,
                    "h1_target_ts_close": float(candle.get("ts_close") or i) + 1.0,
                }
            )
        horizons = sb.infer_horizons(pseudo_rows)
        outputs, manifest = sb.build_splits(
            pseudo_rows,
            train_pct=train_pct,
            validation_pct=validation_pct,
            horizons=horizons,
            embargo_steps=embargo_steps,
        )
        meta["source"] = "candle_lake"
        meta["manifest"] = manifest

    closes_by_split = {split: _closes_from_rows(outputs[split]) for split in sb.SPLITS}
    candles_by_split = {split: candles_from_rows(outputs[split]) for split in sb.SPLITS}
    meta["split_counts"] = {split: len(closes_by_split[split]) for split in sb.SPLITS}
    meta["symbol"] = sym
    meta["timeframe"] = tf
    return closes_by_split, candles_by_split, meta


def load_split_closes(
    *,
    symbol: str,
    timeframe: str,
    cfg: dict,
    dataset_path: Path | None = None,
    splits_base: Path | None = None,
) -> Tuple[Dict[str, List[float]], dict]:
    closes, _, meta = load_split_series(
        symbol=symbol,
        timeframe=timeframe,
        cfg=cfg,
        dataset_path=dataset_path,
        splits_base=splits_base,
    )
    return closes, meta


def _evaluate_train_validation(
    spec: StrategySpec,
    closes_by_split: Dict[str, List[float]],
    candles_by_split: Dict[str, List[dict]],
    *,
    cost_per_trade: float,
    trials: int,
    cfg: dict,
) -> dict:
    candles_train = candles_by_split.get("train") or None
    candles_val = candles_by_split.get("validation") or None
    train_m = backtest_spec(
        closes_by_split["train"],
        spec,
        cost_per_trade=cost_per_trade,
        candles=candles_train if spec.family == "candle_pattern" else None,
    )
    val_m = backtest_spec(
        closes_by_split["validation"],
        spec,
        cost_per_trade=cost_per_trade,
        candles=candles_val if spec.family == "candle_pattern" else None,
    )
    val_m["recency_halves"] = backtest_spec_recency_halves(
        closes_by_split["validation"],
        spec,
        cost_per_trade=cost_per_trade,
        candles=candles_val if spec.family == "candle_pattern" else None,
    )
    gate = gate_train_validation(train_m, val_m, trials=trials, cfg=cfg)
    return {
        "spec": spec.as_dict(),
        "train": train_m,
        "validation": val_m,
        "train_validation_gate": gate,
        "test": None,
        "test_gate": None,
        "test_skipped": True,
        "survivor": False,
    }


def _confirm_on_test(
    candidate: dict,
    closes_by_split: Dict[str, List[float]],
    candles_by_split: Dict[str, List[dict]],
    *,
    cost_per_trade: float,
    cfg: dict,
) -> dict:
    spec = StrategySpec(
        strategy_id=candidate["spec"]["strategy_id"],
        family=candidate["spec"]["family"],
        params=dict(candidate["spec"]["params"]),
        param_count=int(candidate["spec"]["param_count"]),
    )
    val_m = candidate["validation"]
    candles_test = candles_by_split.get("test") or None
    test_m = backtest_spec(
        closes_by_split["test"],
        spec,
        cost_per_trade=cost_per_trade,
        candles=candles_test if spec.family == "candle_pattern" else None,
    )
    test_gate = gate_test_confirmation(val_m, test_m, cfg=cfg)
    candidate = dict(candidate)
    candidate["test"] = test_m
    candidate["test_gate"] = test_gate
    candidate["test_skipped"] = False
    candidate["survivor"] = bool(test_gate["passed"])
    return candidate


def run_strategy_search(
    *,
    symbol: str | None = None,
    timeframe: str | None = None,
    config: dict | None = None,
    report_path: Path | None = None,
    dataset_path: Path | None = None,
    splits_base: Path | None = None,
) -> Dict[str, Any]:
    """Search a bounded strategy grid with anti-overfit protocol.

    Protocol:
    1. Evaluate all specs on train + validation (rank by validation only).
    2. Apply deflated Sharpe + overfit gap + recency stability gates.
    3. Run test split once for top-K validation survivors.
    """
    cfg = config or load_config()
    if not cfg.get("enabled", True):
        return {"ok": True, "skipped": True, "reason": "disabled"}

    defaults = cfg.get("defaults") or {}
    sym = (symbol or defaults.get("symbol") or "EURUSD").upper()
    tf = (timeframe or defaults.get("timeframe") or "M15").upper()
    cost_bps = float(cfg.get("cost_per_trade_bps") or 1.0)
    cost_per_trade = cost_bps / 10000.0

    try:
        closes_by_split, candles_by_split, split_meta = load_split_series(
            symbol=sym,
            timeframe=tf,
            cfg=cfg,
            dataset_path=dataset_path,
            splits_base=splits_base,
        )
    except ValueError as exc:
        return {"ok": False, "error": str(exc), "symbol": sym, "timeframe": tf}

    spec_opts = _search_spec_options(cfg)
    trials = spec_count(**spec_opts)
    top_k = int(cfg.get("top_k_validation") or 3)

    candidates: List[dict] = []
    for spec in iter_strategy_specs(**spec_opts):
        candidates.append(
            _evaluate_train_validation(
                spec,
                closes_by_split,
                candles_by_split,
                cost_per_trade=cost_per_trade,
                trials=trials,
                cfg=cfg,
            )
        )

    validation_passed = [c for c in candidates if c["train_validation_gate"]["passed"]]
    validation_passed.sort(
        key=lambda c: float(c["train_validation_gate"].get("selection_score") or 0),
        reverse=True,
    )

    # One-shot test for top-K validation survivors only (limits test-set mining).
    tested_ids: set[str] = set()
    for idx, cand in enumerate(validation_passed[:top_k]):
        sid = cand["spec"]["strategy_id"]
        tested_ids.add(sid)
        confirmed = _confirm_on_test(
            cand,
            closes_by_split,
            candles_by_split,
            cost_per_trade=cost_per_trade,
            cfg=cfg,
        )
        for i, c in enumerate(candidates):
            if c["spec"]["strategy_id"] == sid:
                candidates[i] = confirmed
                break

    survivors = [c for c in candidates if c.get("survivor")]
    survivors.sort(
        key=lambda c: float(c["validation"].get("sharpe_proxy") or 0),
        reverse=True,
    )

    report = {
        "ok": True,
        "ts": time.time(),
        "symbol": sym,
        "timeframe": tf,
        "protocol": {
            "rank_split": "validation",
            "test_usage": "one_shot_top_k",
            "trials": trials,
            "top_k_tested": top_k,
            "split_policy": sb.SPLIT_POLICY,
            "pattern_search": spec_opts.get("include_patterns"),
        },
        "split_meta": split_meta,
        "cost_per_trade_bps": cost_bps,
        "trials_run": trials,
        "candidates_evaluated": len(candidates),
        "validation_passed_count": len(validation_passed),
        "survivor_count": len(survivors),
        "survivors": survivors[:5],
        "best_survivor": survivors[0] if survivors else None,
        "rejection_summary": _summarize_rejections(candidates),
    }

    out_path = report_path or (ROOT / str(cfg.get("report_path") or "intel/strategy_search_report.json"))
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    report["report_path"] = str(out_path)
    return report


def _summarize_rejections(candidates: List[dict]) -> dict:
    counts: Dict[str, int] = {}
    for c in candidates:
        gate = c.get("train_validation_gate") or {}
        if gate.get("passed"):
            tg = c.get("test_gate") or {}
            for reason in tg.get("reasons") or []:
                key = reason.split(":", 1)[0]
                counts[key] = counts.get(key, 0) + 1
            continue
        for reason in gate.get("reasons") or []:
            key = reason.split(":", 1)[0]
            counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items(), key=lambda x: (-x[1], x[0])))
