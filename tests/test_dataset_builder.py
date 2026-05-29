#!/usr/bin/env python3
"""Tests for research.dataset_builder."""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from research import dataset_builder as db  # noqa: E402


def candle(i, symbol="EURUSD", tf="M5", close=None):
    close = close if close is not None else 1.1000 + i * 0.001
    return {
        "symbol": symbol,
        "timeframe": tf,
        "ts_open": 1000 + i * 300,
        "ts_close": 1300 + i * 300,
        "open_price": close - 0.0002,
        "high": close + 0.0005,
        "low": close - 0.0005,
        "close": close,
        "tick_count": 5,
        "volume": 0.0001,
    }


def write_jsonl(path: Path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for row in rows:
            if isinstance(row, str):
                f.write(row + "\n")
            else:
                f.write(json.dumps(row, sort_keys=True) + "\n")


def make_lake(tmp_path: Path, *, rows=None):
    path = tmp_path / "data" / "lake" / "candles" / "symbol=EURUSD" / "timeframe=M5" / "candles.jsonl"
    write_jsonl(path, rows if rows is not None else [candle(i) for i in range(6)])
    return path.parent.parent.parent


def make_evals(tmp_path: Path):
    path = tmp_path / "memory" / "training" / "signal_evaluations.jsonl"
    write_jsonl(path, [{
        "symbol": "EURUSD",
        "timeframe": "M5",
        "ts_close": 1300,
        "status": "passed",
        "stage": "publish_signal",
        "reason": "signal_emitted",
        "strategy_id": "TEST",
        "intent": {"side": "BUY", "price": 1.1000, "confidence": 0.8, "order_id": "o1"},
    }])
    return path


def make_sell_evals(tmp_path: Path):
    path = tmp_path / "memory" / "training" / "signal_evaluations.jsonl"
    write_jsonl(path, [{
        "symbol": "EURUSD",
        "timeframe": "M5",
        "ts_close": 1300,
        "status": "passed",
        "stage": "publish_signal",
        "reason": "signal_emitted",
        "intent": {"side": "SELL", "price": 1.1000, "confidence": 0.8, "order_id": "o2"},
    }])
    return path


def test_discover_candle_files_from_partition_layout(tmp_path):
    lake_root = make_lake(tmp_path)
    files = db.discover_candle_files(lake_root, symbols={"EURUSD"}, timeframes={"M5"})
    assert len(files) == 1
    assert files[0].name == "candles.jsonl"
    print("[test] PASS: dataset builder discovers partitioned candle files")


def test_builder_computes_no_leakage_forward_labels(tmp_path):
    lake_root = make_lake(tmp_path)
    evals = make_evals(tmp_path)
    args = db.parse_args(["--lake-root", str(lake_root), "--evaluations", str(evals), "--horizons", "1,3", "--allow-empty"])
    rows, quality, manifest = db.build_dataset(args)
    assert len(rows) == 1
    row = rows[0]
    assert row["feature_cutoff_ts"] == row["ts_close"] == 1300
    assert row["close"] == candle(0)["close"]
    assert row["h1_complete"] is True
    assert row["h3_complete"] is True
    assert row["h1_exit_close"] == candle(1)["close"]
    assert row["h1_forward_return_signed"] == row["h1_forward_return"]
    assert quality["coverage"]["matched_decision_candle_rows"] == 1
    print("[test] PASS: dataset builder computes forward labels strictly after t0")


def test_builder_marks_incomplete_horizons(tmp_path):
    lake_root = make_lake(tmp_path, rows=[candle(0), candle(1)])
    evals = make_evals(tmp_path)
    args = db.parse_args(["--lake-root", str(lake_root), "--evaluations", str(evals), "--horizons", "1,3", "--allow-empty"])
    rows, quality, _ = db.build_dataset(args)
    assert rows[0]["h1_complete"] is True
    assert rows[0]["h3_complete"] is False
    assert rows[0]["h3_exit_close"] is None
    assert "h3_forward_return" in rows[0]
    assert quality["coverage"]["h3_complete_pct"] == 0.0
    print("[test] PASS: dataset builder marks incomplete horizons")


def test_sell_mfe_mae_use_entry_denominator(tmp_path):
    lake_root = make_lake(tmp_path, rows=[candle(0), candle(1, close=1.0980)])
    evals = make_sell_evals(tmp_path)
    args = db.parse_args(["--lake-root", str(lake_root), "--evaluations", str(evals), "--horizons", "1", "--allow-empty"])
    rows, _, _ = db.build_dataset(args)
    row = rows[0]
    assert row["h1_forward_return_signed"] > 0
    assert row["h1_mfe"] == (1.1000 - candle(1, close=1.0980)["low"]) / 1.1000
    assert row["h1_mae"] == (1.1000 - candle(1, close=1.0980)["high"]) / 1.1000
    print("[test] PASS: SELL MFE/MAE use entry-price denominator")


def test_gap_horizons_mark_incomplete(tmp_path):
    rows = [candle(0), candle(2), candle(3)]
    lake_root = make_lake(tmp_path, rows=rows)
    evals = make_evals(tmp_path)
    args = db.parse_args(["--lake-root", str(lake_root), "--evaluations", str(evals), "--horizons", "1", "--allow-empty"])
    out, quality, _ = db.build_dataset(args)
    assert out[0]["h1_complete"] is False
    assert quality["quality"]["missing_intervals"] == 1
    assert quality["leakage_checks"]["incomplete_due_to_gaps"] == 1
    print("[test] PASS: fixed horizons do not cross candle gaps")


def test_bad_candle_rows_are_counted_and_rejected(tmp_path):
    bad = candle(0)
    bad["high"] = bad["low"] - 1
    lake_root = make_lake(tmp_path, rows=[bad, "not-json", candle(1)])
    evals = make_evals(tmp_path)
    args = db.parse_args(["--lake-root", str(lake_root), "--evaluations", str(evals), "--allow-empty"])
    rows, quality, _ = db.build_dataset(args)
    assert rows == []
    assert quality["quality"]["schema_invalid_candles"] == 1
    assert quality["quality"]["bad_json_lines"] == 1
    print("[test] PASS: bad candle rows are counted and rejected")


def test_duplicate_candles_dedupe_deterministically(tmp_path):
    lake_root = make_lake(tmp_path, rows=[candle(0), candle(0), candle(1), candle(2)])
    evals = make_evals(tmp_path)
    args = db.parse_args(["--lake-root", str(lake_root), "--evaluations", str(evals), "--horizons", "1", "--allow-empty"])
    rows, quality, _ = db.build_dataset(args)
    assert len(rows) == 1
    assert quality["quality"]["duplicate_candle_keys"] == 1
    print("[test] PASS: duplicate candles dedupe deterministically")


def test_cli_writes_atomic_outputs_and_dry_run_writes_nothing(tmp_path):
    lake_root = make_lake(tmp_path)
    evals = make_evals(tmp_path)
    out = tmp_path / "out" / "dataset.jsonl"
    qout = tmp_path / "out" / "quality.json"
    mout = tmp_path / "out" / "manifest.json"
    rc = db.main(["--lake-root", str(lake_root), "--evaluations", str(evals), "--out", str(out), "--quality-out", str(qout), "--manifest-out", str(mout), "--horizons", "1"])
    assert rc == 0
    first = out.read_text()
    assert json.loads(qout.read_text())["outputs"]["dataset_rows"] == 1
    assert "dataset_sha256" in json.loads(mout.read_text())["outputs"]
    rc = db.main(["--lake-root", str(lake_root), "--evaluations", str(evals), "--out", str(out), "--quality-out", str(qout), "--manifest-out", str(mout), "--horizons", "1"])
    assert rc == 0
    assert out.read_text() == first
    dry = tmp_path / "dry" / "dataset.jsonl"
    rc = db.main(["--lake-root", str(lake_root), "--evaluations", str(evals), "--out", str(dry), "--horizons", "1", "--dry-run"])
    assert rc == 0
    assert not dry.exists()
    print("[test] PASS: dataset builder CLI writes atomically and dry-run writes nothing")


def test_cli_fails_empty_without_allow_empty(tmp_path):
    lake_root = make_lake(tmp_path)
    evals = tmp_path / "missing.jsonl"
    rc = db.main(["--lake-root", str(lake_root), "--evaluations", str(evals)])
    assert rc == 2
    print("[test] PASS: dataset builder refuses empty output by default")


def test_all():
    import tempfile
    tests = [
        test_discover_candle_files_from_partition_layout,
        test_builder_computes_no_leakage_forward_labels,
        test_builder_marks_incomplete_horizons,
        test_sell_mfe_mae_use_entry_denominator,
        test_gap_horizons_mark_incomplete,
        test_bad_candle_rows_are_counted_and_rejected,
        test_duplicate_candles_dedupe_deterministically,
        test_cli_writes_atomic_outputs_and_dry_run_writes_nothing,
        test_cli_fails_empty_without_allow_empty,
    ]
    for fn in tests:
        with tempfile.TemporaryDirectory(prefix="GROWTH[Test] ") as d:
            fn(Path(d))


if __name__ == "__main__":
    test_all()
