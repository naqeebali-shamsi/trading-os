#!/usr/bin/env python3
"""Tests for the edge candidate ledger: ingest, label, and gate report."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from research import edge_ledger as el  # noqa: E402
from research import edge_validation as ev  # noqa: E402
from research import edge_ledger_ops as ops  # noqa: E402


def _forecast(symbol="EURUSD", timeframe="M5", direction="up", last_close=100.0, ts=0.0):
    return {
        "topic": "market.forecast",
        "ts": ts,
        "payload": {
            "symbol": symbol,
            "timeframe": timeframe,
            "direction": direction,
            "last_close": last_close,
        },
    }


def test_ingest_events_dedups_on_repeat(tmp_path):
    cpath = tmp_path / "edge_candidates.jsonl"
    events = [_forecast(ts=0.0), _forecast(ts=0.0)]

    appended = el.ingest_events(events, path=cpath)
    assert appended == 1
    assert len(el.load_candidates(cpath)) == 1

    # Re-ingesting the same event appends nothing.
    assert el.ingest_events(events, path=cpath) == 0
    assert len(el.load_candidates(cpath)) == 1


def test_ingest_skips_events_missing_fields(tmp_path):
    cpath = tmp_path / "edge_candidates.jsonl"
    events = [
        {"topic": "market.forecast", "ts": 0.0, "payload": {"symbol": "EURUSD"}},
        {"topic": "other.topic", "ts": 0.0, "payload": {"symbol": "EURUSD", "timeframe": "M5", "direction": "up", "last_close": 1.0}},
        _forecast(ts=0.0),
    ]
    assert el.ingest_events(events, path=cpath) == 1


def test_label_candidates_with_fake_lookup(tmp_path):
    cpath = tmp_path / "edge_candidates.jsonl"
    lpath = tmp_path / "edge_labels.jsonl"
    el.ingest_events([_forecast(ts=0.0, last_close=100.0)], path=cpath)

    def lookup(symbol, ts):
        return 110.0 if ts is not None and float(ts) == 3600.0 else None

    # Not yet labelable before the horizon closes.
    assert el.label_candidates(lookup, candidate_path=cpath, label_path=lpath, now=100.0) == 0

    labeled = el.label_candidates(lookup, candidate_path=cpath, label_path=lpath, now=4000.0)
    assert labeled == 1
    labels = el.load_labels(lpath)
    assert len(labels) == 1
    assert labels[0]["win"] is True
    assert abs(labels[0]["signed_return"] - 0.1) < 1e-9

    # Idempotent re-label.
    assert el.label_candidates(lookup, candidate_path=cpath, label_path=lpath, now=4000.0) == 0


def test_label_down_direction_win_on_drop(tmp_path):
    cpath = tmp_path / "edge_candidates.jsonl"
    lpath = tmp_path / "edge_labels.jsonl"
    el.ingest_events([_forecast(direction="down", last_close=100.0, ts=0.0)], path=cpath)

    def lookup(symbol, ts):
        return 90.0 if float(ts) == 3600.0 else None

    el.label_candidates(lookup, candidate_path=cpath, label_path=lpath, now=4000.0)
    labels = el.load_labels(lpath)
    assert labels[0]["win"] is True  # price dropped, "down" forecast wins


def test_gate_report_groups_and_promotion():
    labels = [
        {"symbol": "EURUSD", "timeframe": "M5", "signed_return": 0.01, "win": True}
        for _ in range(30)
    ]
    report = ev.gate_report([], labels, now=0.0, cost_per_trade=0.0)
    assert report["group_count"] == 1
    group = report["groups"][0]
    assert group["symbol"] == "EURUSD"
    assert group["samples"] == 30
    assert group["promotable"] is True
    assert report["promotable_count"] == 1


def test_gate_report_blocks_small_sample():
    labels = [{"symbol": "EURUSD", "timeframe": "M5", "signed_return": 0.01, "win": True}]
    report = ev.gate_report([], labels, now=0.0)
    group = report["groups"][0]
    assert group["promotable"] is False
    assert any("samples" in r for r in group["reasons"])


def test_ops_run_once_ingests_labels_and_reports(tmp_path):
    cpath = tmp_path / "edge_candidates.jsonl"
    lpath = tmp_path / "edge_labels.jsonl"
    rpath = tmp_path / "edge_gate_report.json"
    events = [
        {"topic": "market.forecast", "ts": 0.0, "payload": {"symbol": "EURUSD", "timeframe": "M5", "direction": "up", "last_close": 100.0}},
    ]

    def lookup(symbol, ts):
        return 110.0 if ts is not None and float(ts) == 3600.0 else None

    result = ops.run_once(
        events=events,
        price_lookup=lookup,
        candidate_path=cpath,
        label_path=lpath,
        report_path=rpath,
        now=4000.0,
    )

    assert result["appended"] == 1
    assert result["candidates"] == 1
    assert result["labeled"] == 1
    assert result["labels"] == 1
    assert result["groups"] == 1
    assert rpath.exists()

    # Idempotent: a second pass over the same window adds nothing new.
    again = ops.run_once(
        events=events,
        price_lookup=lookup,
        candidate_path=cpath,
        label_path=lpath,
        report_path=rpath,
        now=4000.0,
    )
    assert again["appended"] == 0
    assert again["labeled"] == 0


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-q"]))
