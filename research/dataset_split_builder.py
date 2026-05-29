#!/usr/bin/env python3
"""Deterministic chronological split builder for Trading OS research datasets.

Creates train/validation/test JSONL snapshots plus a manifest. Splits are per
series_id and purged so earlier split label windows do not overlap later split
feature cutoffs.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable, Iterator

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from data_lake import TRAINING_ROOT  # noqa: E402
from research.dataset_builder import DATASET_VERSION, _atomic_write_text, iter_jsonl  # noqa: E402

SPLIT_POLICY = "chronological_per_series_purged_v0"
SPLITS = ("train", "validation", "test")


def row_identity(row: dict) -> str:
    base = {
        "series_id": row.get("series_id"),
        "ts_close": row.get("ts_close"),
        "order_id": row.get("order_id"),
        "stage": row.get("stage"),
        "reason": row.get("reason"),
        "status": row.get("status"),
    }
    return hashlib.sha256(json.dumps(base, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def stable_row_key(row: dict) -> tuple:
    return (
        str(row.get("series_id") or f"{row.get('symbol','')}_{row.get('timeframe','')}"),
        float(row.get("ts_close") or 0.0),
        str(row.get("order_id") or ""),
        str(row.get("stage") or ""),
        str(row.get("reason") or ""),
        json.dumps(row, sort_keys=True, separators=(",", ":"), default=str),
    )


def load_rows(path: Path) -> list[dict]:
    rows: list[dict] = []
    for line_no, row, err in iter_jsonl(path):
        if err:
            raise ValueError(f"bad dataset JSONL at {path}:{line_no}:{err}")
        if not row.get("series_id") or row.get("ts_close") is None:
            raise ValueError(f"dataset row missing series_id/ts_close at {path}:{line_no}")
        rows.append(row)
    return rows


def max_label_ts(row: dict, horizons: Iterable[int]) -> float:
    values = []
    for h in horizons:
        if row.get(f"h{h}_complete") and row.get(f"h{h}_target_ts_close") is not None:
            values.append(float(row[f"h{h}_target_ts_close"]))
    return max(values) if values else float(row.get("ts_close") or 0.0)


def infer_horizons(rows: list[dict]) -> tuple[int, ...]:
    horizons = set()
    for row in rows:
        for key in row:
            if key.startswith("h") and key.endswith("_target_ts_close"):
                raw = key[1:].split("_", 1)[0]
                if raw.isdigit():
                    horizons.add(int(raw))
    return tuple(sorted(horizons)) or (1,)


def split_counts(n: int, train_pct: float, validation_pct: float) -> tuple[int, int, int]:
    if n < 3:
        return n, 0, 0
    train_n = max(1, int(math.floor(n * train_pct)))
    val_n = max(1, int(math.floor(n * validation_pct)))
    if train_n + val_n >= n:
        val_n = 1
        train_n = max(1, n - 2)
    test_n = n - train_n - val_n
    return train_n, val_n, test_n


def assign_series(rows: list[dict], *, train_pct: float, validation_pct: float, horizons: tuple[int, ...], embargo_steps: int = 0) -> tuple[list[dict], dict]:
    ordered = sorted(rows, key=stable_row_key)
    n = len(ordered)
    train_n, val_n, test_n = split_counts(n, train_pct, validation_pct)
    raw_train = ordered[:train_n]
    raw_val = ordered[train_n:train_n + val_n]
    raw_test = ordered[train_n + val_n:]
    warnings: list[str] = []
    if n < 3:
        warnings.append("tiny_series_all_train")

    val_start = float(raw_val[0]["ts_close"]) if raw_val else None
    test_start = float(raw_test[0]["ts_close"]) if raw_test else None
    tf_seconds = None
    if len(ordered) >= 2:
        diffs = [float(ordered[i]["ts_close"]) - float(ordered[i-1]["ts_close"]) for i in range(1, len(ordered))]
        positives = [d for d in diffs if d > 0]
        tf_seconds = min(positives) if positives else None
    embargo_sec = (tf_seconds or 0.0) * max(0, embargo_steps)

    purged_train = []
    kept_train = []
    for row in raw_train:
        if val_start is not None and max_label_ts(row, horizons) >= val_start - embargo_sec:
            purged_train.append(row)
        else:
            kept_train.append(row)
    purged_val = []
    kept_val = []
    for row in raw_val:
        if test_start is not None and max_label_ts(row, horizons) >= test_start - embargo_sec:
            purged_val.append(row)
        else:
            kept_val.append(row)
    if raw_val and not kept_val:
        warnings.append("validation_fully_purged")
    if raw_train and not kept_train:
        warnings.append("train_fully_purged")

    # Preserve test rows. If validation is fully purged on tiny data, make that explicit.
    assigned = []
    for split, group in (("train", kept_train), ("validation", kept_val), ("test", raw_test)):
        for idx, row in enumerate(group):
            out = dict(row)
            out["split"] = split
            out["split_index"] = idx
            out["split_policy"] = SPLIT_POLICY
            out["max_label_ts_close"] = max_label_ts(row, horizons)
            out["row_id"] = row_identity(row)
            assigned.append(out)
    meta = {
        "total": n,
        "train": len(kept_train),
        "validation": len(kept_val),
        "test": len(raw_test),
        "cutoffs": {
            "train_end_ts_close": float(kept_train[-1]["ts_close"]) if kept_train else None,
            "validation_start_ts_close": float(kept_val[0]["ts_close"]) if kept_val else None,
            "validation_end_ts_close": float(kept_val[-1]["ts_close"]) if kept_val else None,
            "test_start_ts_close": float(raw_test[0]["ts_close"]) if raw_test else None,
        },
        "purged_rows": {
            "train_validation_boundary": len(purged_train),
            "validation_test_boundary": len(purged_val),
        },
        "warnings": warnings,
    }
    return assigned, meta


def build_splits(rows: list[dict], *, train_pct: float, validation_pct: float, horizons: tuple[int, ...], embargo_steps: int = 0) -> tuple[dict[str, list[dict]], dict]:
    by_series: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        by_series[str(row.get("series_id"))].append(row)
    outputs = {k: [] for k in SPLITS}
    series_meta = {}
    for series_id in sorted(by_series):
        assigned, meta = assign_series(by_series[series_id], train_pct=train_pct, validation_pct=validation_pct, horizons=horizons, embargo_steps=embargo_steps)
        series_meta[series_id] = meta
        for row in assigned:
            outputs[row["split"]].append(row)
    for split in SPLITS:
        outputs[split] = sorted(outputs[split], key=stable_row_key)
    manifest = build_manifest(rows, outputs, series_meta, train_pct=train_pct, validation_pct=validation_pct, horizons=horizons, embargo_steps=embargo_steps)
    return outputs, manifest


def leakage_checks(outputs: dict[str, list[dict]]) -> dict:
    overlaps = 0
    non_chrono = 0
    ids_by_split = {split: {row["row_id"] for row in rows} for split, rows in outputs.items()}
    order = {"train": 0, "validation": 1, "test": 2}
    for a in SPLITS:
        for b in SPLITS:
            if order[a] < order[b]:
                overlaps += len(ids_by_split[a] & ids_by_split[b])
    by_series = defaultdict(list)
    for split, rows in outputs.items():
        for row in rows:
            by_series[row["series_id"]].append((order[split], float(row["ts_close"]), float(row.get("max_label_ts_close") or row["ts_close"])))
    cross_label_overlap = 0
    for vals in by_series.values():
        for split_a, ts_a, label_a in vals:
            for split_b, ts_b, _ in vals:
                if split_a < split_b and label_a >= ts_b:
                    cross_label_overlap += 1
                if split_a > split_b and ts_a < ts_b:
                    non_chrono += 1
    return {"duplicate_row_across_splits": overlaps, "cross_split_label_overlap": cross_label_overlap, "non_chronological_split_rows": non_chrono}


def build_manifest(source_rows: list[dict], outputs: dict[str, list[dict]], series_meta: dict, *, train_pct: float, validation_pct: float, horizons: tuple[int, ...], embargo_steps: int) -> dict:
    checks = leakage_checks(outputs)
    return {
        "dataset_version": DATASET_VERSION,
        "split_policy": SPLIT_POLICY,
        "random_split": False,
        "shuffle": False,
        "config": {"train_pct": train_pct, "validation_pct": validation_pct, "test_pct": max(0.0, 1.0 - train_pct - validation_pct), "horizons": list(horizons), "embargo_steps": embargo_steps, "purge_by_max_label_ts": True},
        "counts": {"total": sum(len(v) for v in outputs.values()), **{split: len(outputs[split]) for split in SPLITS}, "source_rows": len(source_rows)},
        "series": series_meta,
        "distributions": {split: {"by_series_id": dict(Counter(row["series_id"] for row in outputs[split])), "by_status": dict(Counter(str(row.get("status")) for row in outputs[split]))} for split in SPLITS},
        "leakage_checks": checks,
        "complete": False,
    }


def canonical_jsonl(rows: list[dict]) -> str:
    return "".join(json.dumps(row, sort_keys=True, default=str) + "\n" for row in rows)


def sha_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def write_outputs(base_out: Path, outputs: dict[str, list[dict]], manifest: dict) -> dict[str, str]:
    texts = {split: canonical_jsonl(outputs[split]) for split in SPLITS}
    shas = {split: sha_text(texts[split]) for split in SPLITS}
    paths = {split: base_out.with_suffix(f".{split}.jsonl") for split in SPLITS}
    for split in SPLITS:
        _atomic_write_text(paths[split], texts[split])
    manifest = dict(manifest)
    manifest["outputs"] = {split: {"path": str(paths[split]), "rows": len(outputs[split]), "sha256": shas[split]} for split in SPLITS}
    manifest["complete"] = True
    manifest_text = json.dumps(manifest, indent=2, sort_keys=True, default=str) + "\n"
    manifest_path = base_out.with_suffix(".splits.manifest.json")
    _atomic_write_text(manifest_path, manifest_text)
    shas["manifest"] = sha_text(manifest_text)
    return shas


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Build chronological purged train/validation/test splits")
    parser.add_argument("--dataset", default=str(TRAINING_ROOT / "datasets" / f"{DATASET_VERSION}.jsonl"))
    parser.add_argument("--out-base", default=str(TRAINING_ROOT / "datasets" / DATASET_VERSION))
    parser.add_argument("--train-pct", type=float, default=0.70)
    parser.add_argument("--validation-pct", type=float, default=0.15)
    parser.add_argument("--horizons", default="auto", help="Comma-separated horizons or auto from h*_target_ts_close columns")
    parser.add_argument("--embargo-steps", type=int, default=0)
    parser.add_argument("--allow-empty", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args(argv)


def parse_horizons(raw: str, rows: list[dict]) -> tuple[int, ...]:
    if str(raw).lower() == "auto":
        return infer_horizons(rows)
    vals = tuple(sorted({int(x.strip()) for x in raw.split(",") if x.strip()}))
    if not vals or any(v <= 0 for v in vals):
        raise ValueError("horizons must be positive integers")
    return vals


def main(argv=None) -> int:
    args = parse_args(argv)
    rows = load_rows(Path(args.dataset)) if Path(args.dataset).exists() else []
    if not rows and not args.allow_empty:
        print("dataset_split_builder: no rows; use --allow-empty to write empty splits", file=sys.stderr)
        return 2
    horizons = parse_horizons(args.horizons, rows)
    outputs, manifest = build_splits(rows, train_pct=args.train_pct, validation_pct=args.validation_pct, horizons=horizons, embargo_steps=args.embargo_steps)
    if args.dry_run:
        print(json.dumps(manifest, indent=2, sort_keys=True, default=str))
        return 0
    write_outputs(Path(args.out_base), outputs, manifest)
    print(json.dumps({"counts": manifest["counts"], "out_base": str(Path(args.out_base))}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
