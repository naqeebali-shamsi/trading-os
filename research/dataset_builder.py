#!/usr/bin/env python3
"""Build no-leakage research datasets from the candle lake and signal evaluations.

Step 2 scope is deliberately conservative:
- input: completed candle JSONL lake + signal evaluation JSONL
- output: deterministic snapshot JSONL plus quality JSON
- labels: forward close return, side-signed return, MFE/MAE for fixed horizons
- no model training, no indicators, no random splits
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import uuid
import statistics
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable, Iterator

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from data_lake import DATA_ROOT, TRAINING_ROOT  # noqa: E402
from sensory.ohlc_engine import TF_SECONDS  # noqa: E402

DATASET_VERSION = "signal_outcomes_v0"
DEFAULT_HORIZONS = (1, 3, 6, 12)
PRICE_FIELDS = ("open_price", "high", "low", "close")


def _float(value: Any, default: float | None = None) -> float | None:
    try:
        if value is None or value == "":
            return default
        out = float(value)
        return out if math.isfinite(out) else default
    except (TypeError, ValueError):
        return default


def _int(value: Any, default: int | None = None) -> int | None:
    try:
        if value is None or value == "":
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def iter_jsonl(path: Path) -> Iterator[tuple[int, dict | None, str | None]]:
    with open(path, "r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, 1):
            raw = line.strip()
            if not raw:
                continue
            try:
                value = json.loads(raw)
            except json.JSONDecodeError as exc:
                yield line_no, None, f"json_decode:{exc.msg}"
                continue
            if not isinstance(value, dict):
                yield line_no, None, "not_object"
                continue
            yield line_no, value, None


def discover_candle_files(lake_root: Path, *, symbols: set[str] | None = None, timeframes: set[str] | None = None) -> list[Path]:
    if not lake_root.exists():
        return []
    files: list[Path] = []
    for path in lake_root.glob("symbol=*/timeframe=*/candles.jsonl"):
        symbol = path.parent.parent.name.split("=", 1)[-1].upper()
        timeframe = path.parent.name.split("=", 1)[-1].upper()
        if symbols and symbol not in symbols:
            continue
        if timeframes and timeframe not in timeframes:
            continue
        files.append(path)
    return sorted(files)


def normalize_candle(row: dict) -> tuple[dict | None, list[str]]:
    errors: list[str] = []
    symbol = str(row.get("symbol") or "").upper().strip()
    timeframe = str(row.get("timeframe") or "").upper().strip()
    ts_close = _float(row.get("ts_close"))
    tf_sec = TF_SECONDS.get(timeframe)
    ts_open = _float(row.get("ts_open"), ts_close - tf_sec if ts_close is not None and tf_sec else None)
    if not symbol:
        errors.append("symbol:required")
    if not timeframe:
        errors.append("timeframe:required")
    if ts_close is None:
        errors.append("ts_close:required_number")
    if tf_sec is None:
        errors.append("timeframe:unknown")
    prices = {k: _float(row.get(k)) for k in PRICE_FIELDS}
    for key, value in prices.items():
        if value is None or value <= 0:
            errors.append(f"{key}:positive_number_required")
    if ts_open is not None and ts_close is not None and ts_close <= ts_open:
        errors.append("ts_close_lte_ts_open")
    if all(prices[k] is not None for k in PRICE_FIELDS):
        if prices["high"] < prices["low"]:
            errors.append("high_lt_low")
        if prices["high"] < max(prices["open_price"], prices["close"]):
            errors.append("high_lt_open_or_close")
        if prices["low"] > min(prices["open_price"], prices["close"]):
            errors.append("low_gt_open_or_close")
    if errors:
        return None, errors
    candle = {
        "symbol": symbol,
        "timeframe": timeframe,
        "ts_open": ts_open,
        "ts_close": ts_close,
        "open_price": prices["open_price"],
        "high": prices["high"],
        "low": prices["low"],
        "close": prices["close"],
        "tick_count": _int(row.get("tick_count"), 0) or 0,
        "volume": _float(row.get("volume"), 0.0) or 0.0,
        "body_size": abs(prices["close"] - prices["open_price"]),
        "range": prices["high"] - prices["low"],
        "upper_shadow": prices["high"] - max(prices["open_price"], prices["close"]),
        "lower_shadow": min(prices["open_price"], prices["close"]) - prices["low"],
        "is_bullish": prices["close"] >= prices["open_price"],
        "source_topic": row.get("source_topic"),
        "bus_seq": row.get("bus_seq"),
        "persisted_ts": row.get("persisted_ts"),
    }
    return candle, []


def input_inventory(paths: list[Path]) -> list[dict]:
    out = []
    for path in paths:
        try:
            st = path.stat()
            out.append({"path": str(path), "size": st.st_size, "mtime": st.st_mtime})
        except FileNotFoundError:
            out.append({"path": str(path), "missing": True})
    return out


def load_candles(lake_root: Path, *, symbols: set[str] | None, timeframes: set[str] | None, quality: dict, files: list[Path] | None = None) -> dict[tuple[str, str], list[dict]]:
    candles: dict[tuple[str, str], dict[float, dict]] = defaultdict(dict)
    last_ts: dict[tuple[str, str], float] = {}
    for path in (files if files is not None else discover_candle_files(lake_root, symbols=symbols, timeframes=timeframes)):
        quality["inputs"]["candle_files"] += 1
        for line_no, row, err in iter_jsonl(path):
            quality["inputs"]["candle_rows"] += 1
            if err:
                quality["quality"]["bad_json_lines"] += 1
                continue
            candle, errors = normalize_candle(row or {})
            if errors:
                quality["quality"]["schema_invalid_candles"] += 1
                continue
            key = (candle["symbol"], candle["timeframe"])
            ts = candle["ts_close"]
            if ts in candles[key]:
                quality["quality"]["duplicate_candle_keys"] += 1
                continue
            if key in last_ts:
                if ts <= last_ts[key]:
                    quality["quality"]["non_monotonic_candles"] += 1
                expected = last_ts[key] + TF_SECONDS.get(candle["timeframe"], 0)
                if expected and ts > expected:
                    observed_steps = int(round((ts - last_ts[key]) / TF_SECONDS[candle["timeframe"]]))
                    quality["quality"]["missing_intervals"] += max(1, observed_steps - 1)
            last_ts[key] = ts
            candles[key][ts] = candle
    return {key: [items[ts] for ts in sorted(items)] for key, items in candles.items()}


def load_evaluations(path: Path, *, symbols: set[str] | None, timeframes: set[str] | None, quality: dict) -> list[dict]:
    if not path.exists():
        quality["warnings"].append(f"evaluation_file_missing:{path}")
        return []
    rows: list[dict] = []
    seen = set()
    for line_no, row, err in iter_jsonl(path):
        quality["inputs"]["evaluation_rows"] += 1
        if err:
            quality["quality"]["bad_evaluation_json_lines"] += 1
            continue
        symbol = str((row or {}).get("symbol") or "").upper().strip()
        timeframe = str((row or {}).get("timeframe") or "").upper().strip()
        ts_close = _float((row or {}).get("ts_close"))
        if symbols and symbol not in symbols:
            continue
        if timeframes and timeframe not in timeframes:
            continue
        if not symbol or not timeframe or ts_close is None:
            quality["quality"]["schema_invalid_evaluations"] += 1
            continue
        dedupe = (symbol, timeframe, ts_close, row.get("stage"), row.get("reason"), row.get("order_id") or (row.get("intent") or {}).get("order_id"))
        if dedupe in seen:
            quality["quality"]["duplicate_evaluation_keys"] += 1
            continue
        seen.add(dedupe)
        rows.append(row)
    return rows


def _side(evaluation: dict) -> str | None:
    side = evaluation.get("side") or (evaluation.get("intent") or {}).get("side")
    side = str(side or "").upper().strip()
    return side if side in {"BUY", "SELL"} else None


def _entry_price(evaluation: dict, candle: dict) -> float:
    intent = evaluation.get("intent") or {}
    return _float(intent.get("price"), candle["close"]) or candle["close"]


def empty_label(prefix: str, target_ts_close: float | None = None) -> dict:
    return {
        f"{prefix}_complete": False,
        f"{prefix}_target_ts_close": target_ts_close,
        f"{prefix}_exit_close": None,
        f"{prefix}_max_high": None,
        f"{prefix}_min_low": None,
        f"{prefix}_forward_return": None,
        f"{prefix}_forward_return_signed": None,
        f"{prefix}_mfe": None,
        f"{prefix}_mae": None,
    }


def label_for_horizon(future: list[dict], entry_price: float, side: str | None, horizon: int, *, t0: float, timeframe: str, quality: dict | None = None) -> dict:
    prefix = f"h{horizon}"
    tf_sec = TF_SECONDS.get(timeframe)
    target_ts = t0 + horizon * tf_sec if tf_sec else None
    if len(future) < horizon:
        return empty_label(prefix, target_ts)
    if tf_sec:
        for offset, candle in enumerate(future[:horizon], 1):
            expected_ts = t0 + offset * tf_sec
            if candle.get("ts_close") != expected_ts:
                if quality is not None:
                    quality["leakage_checks"]["incomplete_due_to_gaps"] = quality["leakage_checks"].get("incomplete_due_to_gaps", 0) + 1
                return empty_label(prefix, target_ts)
    window = future[:horizon]
    exit_close = window[-1]["close"]
    max_high = max(c["high"] for c in window)
    min_low = min(c["low"] for c in window)
    forward_return = exit_close / entry_price - 1.0
    row = {
        f"{prefix}_complete": True,
        f"{prefix}_target_ts_close": target_ts,
        f"{prefix}_exit_close": exit_close,
        f"{prefix}_max_high": max_high,
        f"{prefix}_min_low": min_low,
        f"{prefix}_forward_return": forward_return,
        f"{prefix}_forward_return_signed": None,
        f"{prefix}_mfe": None,
        f"{prefix}_mae": None,
    }
    if side == "BUY":
        row[f"{prefix}_forward_return_signed"] = forward_return
        row[f"{prefix}_mfe"] = max_high / entry_price - 1.0
        row[f"{prefix}_mae"] = min_low / entry_price - 1.0
    elif side == "SELL":
        row[f"{prefix}_forward_return_signed"] = -forward_return
        row[f"{prefix}_mfe"] = (entry_price - min_low) / entry_price
        row[f"{prefix}_mae"] = (entry_price - max_high) / entry_price
    return row


def build_rows(candles_by_key: dict[tuple[str, str], list[dict]], evaluations: Iterable[dict], *, horizons: tuple[int, ...], quality: dict) -> list[dict]:
    candle_index = {(sym, tf): {c["ts_close"]: i for i, c in enumerate(rows)} for (sym, tf), rows in candles_by_key.items()}
    output: list[dict] = []
    for ev in evaluations:
        symbol = str(ev.get("symbol") or "").upper().strip()
        timeframe = str(ev.get("timeframe") or "").upper().strip()
        ts_close = _float(ev.get("ts_close"))
        key = (symbol, timeframe)
        idx = candle_index.get(key, {}).get(ts_close)
        if idx is None:
            quality["coverage"]["missing_decision_candle_rows"] += 1
            continue
        quality["coverage"]["matched_decision_candle_rows"] += 1
        candles = candles_by_key[key]
        candle = candles[idx]
        side = _side(ev)
        intent = ev.get("intent") or {}
        patterns = ev.get("patterns") or intent.get("patterns") or []
        if isinstance(patterns, list):
            pattern_names = [p.get("pattern") if isinstance(p, dict) else str(p) for p in patterns]
        else:
            pattern_names = [str(patterns)]
        entry = _entry_price(ev, candle)
        row = {
            "dataset_version": DATASET_VERSION,
            "symbol": symbol,
            "timeframe": timeframe,
            "series_id": f"{symbol}_{timeframe}",
            "ts_open": candle["ts_open"],
            "ts_close": candle["ts_close"],
            "feature_cutoff_ts": candle["ts_close"],
            "status": ev.get("status"),
            "stage": ev.get("stage"),
            "reason": ev.get("reason"),
            "order_id": ev.get("order_id") or intent.get("order_id"),
            "strategy_id": ev.get("strategy_id") or intent.get("strategy_id"),
            "side": side,
            "confidence": _float(ev.get("confidence"), _float(intent.get("confidence"))),
            "regime": ev.get("regime") or intent.get("regime"),
            "patterns": pattern_names,
            "entry_price": entry,
            "open_price": candle["open_price"],
            "high": candle["high"],
            "low": candle["low"],
            "close": candle["close"],
            "range": candle["range"],
            "body_size": candle["body_size"],
            "upper_shadow": candle["upper_shadow"],
            "lower_shadow": candle["lower_shadow"],
            "is_bullish": candle["is_bullish"],
            "tick_count": candle["tick_count"],
            "volume": candle["volume"],
            "source": {
                "evaluation_bus_seq": ev.get("bus_seq"),
                "evaluation_persisted_ts": ev.get("persisted_ts"),
                "candle_bus_seq": candle.get("bus_seq"),
                "candle_persisted_ts": candle.get("persisted_ts"),
            },
        }
        future = candles[idx + 1:]
        for horizon in horizons:
            row.update(label_for_horizon(future, entry, side, horizon, t0=candle["ts_close"], timeframe=timeframe, quality=quality))
        output.append(row)
    return output


def _stats(values: list[float]) -> dict:
    if not values:
        return {"count": 0, "mean": None, "median": None, "p05": None, "p95": None}
    ordered = sorted(values)
    def pct(p: float) -> float:
        if len(ordered) == 1:
            return ordered[0]
        pos = (len(ordered) - 1) * p
        lo = math.floor(pos)
        hi = math.ceil(pos)
        if lo == hi:
            return ordered[lo]
        return ordered[lo] + (ordered[hi] - ordered[lo]) * (pos - lo)
    return {"count": len(values), "mean": statistics.fmean(values), "median": statistics.median(values), "p05": pct(0.05), "p95": pct(0.95)}


def finalize_quality(quality: dict, rows: list[dict], horizons: tuple[int, ...]) -> dict:
    quality["outputs"]["dataset_rows"] = len(rows)
    for field in ("symbol", "timeframe", "stage", "status", "reason", "side"):
        quality["distributions"][f"by_{field}"] = dict(Counter(str(row.get(field)) for row in rows))
    for horizon in horizons:
        complete = sum(1 for row in rows if row.get(f"h{horizon}_complete"))
        quality["coverage"][f"h{horizon}_complete_pct"] = (complete / len(rows) * 100.0) if rows else 0.0
        vals = [row[f"h{horizon}_forward_return_signed"] for row in rows if isinstance(row.get(f"h{horizon}_forward_return_signed"), (int, float))]
        quality["label_stats"][f"h{horizon}_forward_return_signed"] = _stats(vals)
    quality["leakage_checks"].setdefault("future_features_detected", 0)
    quality["leakage_checks"].setdefault("labels_using_t0_candle", 0)
    for row in rows:
        cutoff = row.get("feature_cutoff_ts")
        for horizon in horizons:
            target = row.get(f"h{horizon}_target_ts_close")
            if row.get(f"h{horizon}_complete") and target is not None and cutoff is not None and target <= cutoff:
                quality["leakage_checks"]["labels_using_t0_candle"] += 1
    return quality


def initial_quality(config: dict) -> dict:
    return {
        "dataset_version": DATASET_VERSION,
        "build_ts": time.time(),
        "config": config,
        "inputs": {"candle_files": 0, "candle_rows": 0, "evaluation_rows": 0},
        "outputs": {"dataset_rows": 0},
        "coverage": {"matched_decision_candle_rows": 0, "missing_decision_candle_rows": 0},
        "quality": {
            "bad_json_lines": 0,
            "bad_evaluation_json_lines": 0,
            "schema_invalid_candles": 0,
            "schema_invalid_evaluations": 0,
            "duplicate_candle_keys": 0,
            "duplicate_evaluation_keys": 0,
            "non_monotonic_candles": 0,
            "missing_intervals": 0,
        },
        "distributions": {},
        "label_stats": {},
        "leakage_checks": {},
        "warnings": [],
    }


def _atomic_write_text(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".tmp.{os.getpid()}.{uuid.uuid4().hex}")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(str(tmp), str(path))
    if os.name != "nt":
        try:
            dir_fd = os.open(str(path.parent), os.O_DIRECTORY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except Exception:
            pass


def write_jsonl_atomic(path: Path, rows: list[dict]) -> str:
    text = "".join(json.dumps(row, sort_keys=True, default=str) + "\n" for row in rows)
    _atomic_write_text(path, text)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def write_json_atomic(path: Path, payload: dict) -> str:
    text = json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n"
    _atomic_write_text(path, text)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def parse_csv_set(raw: str | None) -> set[str] | None:
    if not raw:
        return None
    values = {x.strip().upper() for x in raw.split(",") if x.strip()}
    return values or None


def parse_horizons(raw: str) -> tuple[int, ...]:
    values = tuple(sorted({int(x.strip()) for x in raw.split(",") if x.strip()}))
    if not values or any(x <= 0 for x in values):
        raise argparse.ArgumentTypeError("horizons must be positive integers")
    return values


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Build research dataset from Trading OS candle/evaluation lake")
    parser.add_argument("--lake-root", default=str(DATA_ROOT / "lake" / "candles"))
    parser.add_argument("--evaluations", default=str(TRAINING_ROOT / "signal_evaluations.jsonl"))
    parser.add_argument("--out", default=str(TRAINING_ROOT / "datasets" / f"{DATASET_VERSION}.jsonl"))
    parser.add_argument("--quality-out", default=str(TRAINING_ROOT / "datasets" / f"{DATASET_VERSION}.quality.json"))
    parser.add_argument("--manifest-out", default=str(TRAINING_ROOT / "datasets" / f"{DATASET_VERSION}.manifest.json"))
    parser.add_argument("--symbols", default=None)
    parser.add_argument("--timeframes", default=None)
    parser.add_argument("--horizons", type=parse_horizons, default=DEFAULT_HORIZONS)
    parser.add_argument("--allow-empty", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args(argv)


def build_dataset(args) -> tuple[list[dict], dict, dict]:
    symbols = parse_csv_set(args.symbols)
    timeframes = parse_csv_set(args.timeframes)
    config = {
        "lake_root": str(Path(args.lake_root)),
        "evaluations": str(Path(args.evaluations)),
        "symbols": sorted(symbols) if symbols else None,
        "timeframes": sorted(timeframes) if timeframes else None,
        "horizons": list(args.horizons),
    }
    quality = initial_quality(config)
    candle_files = discover_candle_files(Path(args.lake_root), symbols=symbols, timeframes=timeframes)
    eval_path = Path(args.evaluations)
    input_files = candle_files + ([eval_path] if eval_path.exists() else [])
    before_inventory = input_inventory(input_files)
    candles = load_candles(Path(args.lake_root), symbols=symbols, timeframes=timeframes, quality=quality, files=candle_files)
    evaluations = load_evaluations(eval_path, symbols=symbols, timeframes=timeframes, quality=quality)
    after_inventory = input_inventory(input_files)
    if before_inventory != after_inventory:
        quality["warnings"].append("input_files_changed_during_build")
    rows = build_rows(candles, evaluations, horizons=args.horizons, quality=quality)
    quality = finalize_quality(quality, rows, args.horizons)
    manifest = {
        "dataset_version": DATASET_VERSION,
        "build_ts": time.time(),
        "config": config,
        "outputs": {"dataset_rows": len(rows), "out": str(Path(args.out)), "quality_out": str(Path(args.quality_out))},
        "inputs": quality["inputs"],
        "input_inventory_before": before_inventory,
        "input_inventory_after": after_inventory,
        "warnings": list(quality.get("warnings", [])),
    }
    return rows, quality, manifest


def main(argv=None) -> int:
    args = parse_args(argv)
    rows, quality, manifest = build_dataset(args)
    if not rows and not args.allow_empty:
        print("dataset_builder: no rows produced; use --allow-empty to write empty outputs", file=sys.stderr)
        return 2
    if args.dry_run:
        print(json.dumps({"rows": len(rows), "quality": quality}, indent=2, sort_keys=True, default=str))
        return 0
    dataset_sha = write_jsonl_atomic(Path(args.out), rows)
    quality_sha = write_json_atomic(Path(args.quality_out), quality)
    manifest["outputs"]["dataset_sha256"] = dataset_sha
    manifest["outputs"]["quality_sha256"] = quality_sha
    write_json_atomic(Path(args.manifest_out), manifest)
    print(json.dumps({"rows": len(rows), "out": str(Path(args.out)), "quality_out": str(Path(args.quality_out))}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
