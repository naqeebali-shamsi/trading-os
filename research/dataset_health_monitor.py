#!/usr/bin/env python3
"""Read-only dataset health monitor for research/baseline readiness."""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from data_lake import DATA_ROOT, TRAINING_ROOT
from research.dataset_builder import DATASET_VERSION, discover_candle_files, iter_jsonl

STATUS_READY = "READY"
STATUS_DEGRADED = "DEGRADED"
STATUS_NOT_READY = "NOT_READY"
SPLITS = ("train", "validation", "test")


def count_jsonl(path: Path) -> tuple[int, int]:
    if not path.exists():
        return 0, 0
    rows = bad = 0
    for _, row, err in iter_jsonl(path):
        if err:
            bad += 1
        elif row is not None:
            rows += 1
    return rows, bad


def mtime(path: Path) -> float | None:
    try:
        return path.stat().st_mtime
    except FileNotFoundError:
        return None


def load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
        return obj if isinstance(obj, dict) else {}
    except Exception:
        return {}


def latest_candle_ts(lake_root: Path) -> float | None:
    latest = None
    for path in discover_candle_files(lake_root):
        for _, row, err in iter_jsonl(path):
            if err or not row:
                continue
            ts = row.get("ts_close")
            if isinstance(ts, (int, float)):
                latest = ts if latest is None else max(latest, float(ts))
    return latest


def _status_from_blockers(blockers: list[str], warnings: list[str]) -> str:
    if blockers:
        return STATUS_NOT_READY
    if warnings:
        return STATUS_DEGRADED
    return STATUS_READY


def build_health(*, lake_root: Path, training_root: Path, dataset_base: str = DATASET_VERSION, min_split_rows: int = 20) -> dict:
    dataset_dir = training_root / "datasets"
    eval_path = training_root / "signal_evaluations.jsonl"
    dataset_path = dataset_dir / f"{dataset_base}.jsonl"
    quality_path = dataset_dir / f"{dataset_base}.quality.json"
    manifest_path = dataset_dir / f"{dataset_base}.manifest.json"
    split_manifest_path = dataset_dir / f"{dataset_base}.splits.manifest.json"
    split_paths = {split: dataset_dir / f"{dataset_base}.{split}.jsonl" for split in SPLITS}

    warnings: list[str] = []
    blockers: list[str] = []

    candle_files = discover_candle_files(lake_root)
    candle_rows = 0
    candle_bad = 0
    for path in candle_files:
        rows, bad = count_jsonl(path)
        candle_rows += rows
        candle_bad += bad
    evaluation_rows, evaluation_bad = count_jsonl(eval_path)
    dataset_rows, dataset_bad = count_jsonl(dataset_path)
    split_counts = {split: count_jsonl(path)[0] for split, path in split_paths.items()}

    quality = load_json(quality_path)
    manifest = load_json(manifest_path)
    split_manifest = load_json(split_manifest_path)

    coverage = dict(quality.get("coverage") or {})
    q = dict(quality.get("quality") or {})
    leakage = dict(quality.get("leakage_checks") or {})
    leakage.update((split_manifest.get("leakage_checks") or {}))

    matched = float(coverage.get("matched_decision_candle_rows") or 0)
    missing = float(coverage.get("missing_decision_candle_rows") or 0)
    match_pct = (matched / (matched + missing) * 100.0) if (matched + missing) else 0.0
    coverage["match_pct"] = match_pct

    if not candle_files or candle_rows <= 0:
        blockers.append("candle_lake_missing_or_empty")
    if not eval_path.exists() or evaluation_rows <= 0:
        blockers.append("evaluation_file_missing_or_empty")
    if not dataset_path.exists() or dataset_rows <= 0:
        blockers.append("dataset_file_missing_or_empty")
    if not quality_path.exists():
        blockers.append("dataset_quality_missing")
    if not split_manifest_path.exists() or not split_manifest.get("complete"):
        blockers.append("split_manifest_missing_or_incomplete")
    for split, rows in split_counts.items():
        if not split_paths[split].exists():
            blockers.append(f"split_file_missing:{split}")
        elif rows == 0:
            blockers.append(f"split_empty:{split}")
        elif rows < min_split_rows and split in {"validation", "test"}:
            warnings.append(f"tiny_split:{split}:{rows}<{min_split_rows}")

    for name, value in q.items():
        if isinstance(value, (int, float)) and value > 0:
            warnings.append(f"quality_issue:{name}:{value}")
    if candle_bad:
        warnings.append(f"bad_candle_json_lines:{candle_bad}")
    if evaluation_bad:
        warnings.append(f"bad_evaluation_json_lines:{evaluation_bad}")
    if dataset_bad:
        warnings.append(f"bad_dataset_json_lines:{dataset_bad}")
    for name, value in leakage.items():
        if isinstance(value, (int, float)) and value > 0:
            blockers.append(f"leakage_check_failed:{name}:{value}")
    if match_pct and match_pct < 95.0:
        warnings.append(f"low_eval_to_candle_match_pct:{match_pct:.1f}")
    for key, value in coverage.items():
        if key.endswith("_complete_pct") and isinstance(value, (int, float)) and value < 80.0:
            warnings.append(f"low_horizon_completion:{key}:{value:.1f}")

    dataset_mt = mtime(dataset_path)
    split_mt = mtime(split_manifest_path)
    eval_mt = mtime(eval_path)
    newest_input_mt = max([x for x in [eval_mt, *[mtime(p) for p in candle_files]] if x is not None], default=None)
    if dataset_mt and newest_input_mt and dataset_mt < newest_input_mt:
        warnings.append("dataset_stale_vs_inputs")
    if split_mt and dataset_mt and split_mt < dataset_mt:
        warnings.append("splits_stale_vs_dataset")

    split_series_warnings = []
    for series, meta in (split_manifest.get("series") or {}).items():
        for warning in meta.get("warnings") or []:
            split_series_warnings.append(f"{series}:{warning}")
            warnings.append(f"split_series_warning:{warning}")

    readiness = {
        "candles": _status_from_blockers([b for b in blockers if b.startswith("candle")], [w for w in warnings if "candle" in w or "missing_intervals" in w or "non_monotonic" in w]),
        "evaluations": _status_from_blockers([b for b in blockers if b.startswith("evaluation")], [w for w in warnings if "evaluation" in w or "eval" in w]),
        "dataset": _status_from_blockers([b for b in blockers if b.startswith("dataset")], [w for w in warnings if "dataset" in w or "quality" in w or "horizon" in w]),
        "splits": _status_from_blockers([b for b in blockers if b.startswith("split")], [w for w in warnings if "split" in w]),
        "dashboard": STATUS_READY if dataset_path.exists() or quality_path.exists() or split_manifest_path.exists() else STATUS_NOT_READY,
        "baselines": STATUS_NOT_READY,
    }
    baseline_blockers = list(blockers)
    if not baseline_blockers and split_counts.get("validation", 0) >= min_split_rows and split_counts.get("test", 0) >= min_split_rows:
        readiness["baselines"] = STATUS_READY if not warnings else STATUS_DEGRADED

    status = STATUS_NOT_READY if blockers else (STATUS_DEGRADED if warnings else STATUS_READY)
    return {
        "status": status,
        "generated_ts": time.time(),
        "dataset_version": dataset_base,
        "readiness": readiness,
        "counts": {
            "candle_files": len(candle_files),
            "candle_rows": candle_rows,
            "evaluation_rows": evaluation_rows,
            "dataset_rows": dataset_rows,
            "train_rows": split_counts.get("train", 0),
            "validation_rows": split_counts.get("validation", 0),
            "test_rows": split_counts.get("test", 0),
            "series": len(split_manifest.get("series") or {}),
        },
        "coverage": coverage,
        "quality": q,
        "leakage": leakage,
        "freshness": {
            "latest_candle_ts_close": latest_candle_ts(lake_root),
            "latest_evaluation_mtime": eval_mt,
            "dataset_mtime": dataset_mt,
            "splits_manifest_mtime": split_mt,
        },
        "warnings": sorted(set(warnings)),
        "blockers": sorted(set(blockers)),
        "split_series_warnings": split_series_warnings,
    }


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Report dataset/split health for baseline readiness")
    parser.add_argument("--lake-root", default=str(DATA_ROOT / "lake" / "candles"))
    parser.add_argument("--training-root", default=str(TRAINING_ROOT))
    parser.add_argument("--dataset-base", default=DATASET_VERSION)
    parser.add_argument("--min-split-rows", type=int, default=20)
    parser.add_argument("--out", default=None, help="Optional JSON output path")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    health = build_health(lake_root=Path(args.lake_root), training_root=Path(args.training_root), dataset_base=args.dataset_base, min_split_rows=args.min_split_rows)
    text = json.dumps(health, indent=2, sort_keys=True, default=str) + "\n"
    if args.out:
        path = Path(args.out)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    print(text, end="")
    return 0 if health["status"] != STATUS_NOT_READY else 1


if __name__ == "__main__":
    raise SystemExit(main())
