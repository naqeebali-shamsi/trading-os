#!/usr/bin/env python3
"""Tests for research.dataset_split_builder."""
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from research import dataset_split_builder as sb  # noqa: E402


def row(i, series="EURUSD_M1", complete=True):
    ts = 1000.0 + i * 60.0
    return {
        "dataset_version": "signal_outcomes_v0",
        "series_id": series,
        "symbol": series.split("_")[0],
        "timeframe": series.split("_")[1],
        "ts_close": ts,
        "feature_cutoff_ts": ts,
        "stage": "publish_signal",
        "reason": "signal_emitted",
        "status": "passed",
        "order_id": f"o{i}",
        "h1_complete": complete,
        "h1_target_ts_close": ts + 60.0,
        "h1_forward_return_signed": 0.001,
        "h3_complete": complete,
        "h3_target_ts_close": ts + 180.0,
        "h3_forward_return_signed": 0.002,
    }


def write_jsonl(path: Path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in rows), encoding="utf-8")


def test_split_is_per_series_and_purged():
    rows = [row(i, "EURUSD_M1") for i in range(12)] + [row(i, "GBPUSD_M1") for i in range(12)]
    outputs, manifest = sb.build_splits(rows, train_pct=0.5, validation_pct=0.25, horizons=(3,))
    assert manifest["leakage_checks"]["duplicate_row_across_splits"] == 0
    assert manifest["leakage_checks"]["cross_split_label_overlap"] == 0
    assert set(outputs) == {"train", "validation", "test"}
    assert all(r["split"] in outputs for split in outputs for r in outputs[split])
    assert manifest["series"]["EURUSD_M1"]["purged_rows"]["train_validation_boundary"] > 0
    print("[test] PASS: split builder purges cross-split label overlap per series")


def test_tiny_series_goes_to_train_with_warning():
    outputs, manifest = sb.build_splits([row(0), row(1)], train_pct=0.7, validation_pct=0.15, horizons=(1,))
    assert len(outputs["train"]) == 2
    assert len(outputs["validation"]) == 0
    assert len(outputs["test"]) == 0
    assert manifest["series"]["EURUSD_M1"]["warnings"] == ["tiny_series_all_train"]
    print("[test] PASS: tiny series is explicit and train-only")


def test_split_order_independent_of_input_order():
    rows = [row(i) for i in range(20)]
    shuffled = list(rows)
    random.Random(7).shuffle(shuffled)
    out1, man1 = sb.build_splits(rows, train_pct=0.6, validation_pct=0.2, horizons=(1,))
    out2, man2 = sb.build_splits(shuffled, train_pct=0.6, validation_pct=0.2, horizons=(1,))
    assert {k: sb.canonical_jsonl(v) for k, v in out1.items()} == {k: sb.canonical_jsonl(v) for k, v in out2.items()}
    assert man1 == man2
    print("[test] PASS: split output is independent of input order")


def test_endpoint_label_overlap_is_purged():
    rows = [row(i) for i in range(6)]
    outputs, manifest = sb.build_splits(rows, train_pct=0.5, validation_pct=0.2, horizons=(1,))
    assert manifest["leakage_checks"]["cross_split_label_overlap"] == 0
    # The raw train boundary row has h1 target exactly equal to validation start,
    # so strict purging should remove it.
    assert manifest["series"]["EURUSD_M1"]["purged_rows"]["train_validation_boundary"] >= 1
    print("[test] PASS: endpoint label overlap is purged")


def test_cli_writes_manifest_last_and_hashes_outputs(tmp_path):
    dataset = tmp_path / "GROWTH[Test]" / "dataset.jsonl"
    write_jsonl(dataset, [row(i) for i in range(20)])
    out_base = tmp_path / "GROWTH[Test]" / "splits" / "signal_outcomes_v0"
    rc = sb.main(["--dataset", str(dataset), "--out-base", str(out_base), "--horizons", "1,3"])
    assert rc == 0
    manifest_path = out_base.with_suffix(".splits.manifest.json")
    manifest = json.loads(manifest_path.read_text())
    assert manifest["complete"] is True
    for split in sb.SPLITS:
        info = manifest["outputs"][split]
        text = Path(info["path"]).read_text()
        assert sb.sha_text(text) == info["sha256"]
    print("[test] PASS: split CLI writes manifest with matching output hashes")


def test_cli_dry_run_writes_nothing(tmp_path):
    dataset = tmp_path / "dataset.jsonl"
    write_jsonl(dataset, [row(i) for i in range(10)])
    out_base = tmp_path / "out" / "splits"
    rc = sb.main(["--dataset", str(dataset), "--out-base", str(out_base), "--dry-run"])
    assert rc == 0
    assert not out_base.parent.exists()
    print("[test] PASS: split dry-run writes nothing")


def test_empty_input_refused_without_allow_empty(tmp_path):
    rc = sb.main(["--dataset", str(tmp_path / "missing.jsonl"), "--out-base", str(tmp_path / "x")])
    assert rc == 2
    print("[test] PASS: split builder refuses empty input by default")


def test_all():
    import tempfile
    test_split_is_per_series_and_purged()
    test_tiny_series_goes_to_train_with_warning()
    test_split_order_independent_of_input_order()
    test_endpoint_label_overlap_is_purged()
    with tempfile.TemporaryDirectory() as d:
        test_cli_writes_manifest_last_and_hashes_outputs(Path(d))
    with tempfile.TemporaryDirectory() as d:
        test_cli_dry_run_writes_nothing(Path(d))
    with tempfile.TemporaryDirectory() as d:
        test_empty_input_refused_without_allow_empty(Path(d))


if __name__ == "__main__":
    test_all()
