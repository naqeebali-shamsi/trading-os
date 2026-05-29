#!/usr/bin/env python3
"""Tests for MT5 chart bootstrap manifest and gap evaluation."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


class _FakeRegistry:
    def enabled_symbols(self):
        return ["EURUSD", "NVDA"]

    def get(self, symbol: str):
        if symbol == "EURUSD":
            return {"broker_symbol": "EURUSD", "asset_class": "forex"}
        return {"broker_symbol": "NVDA", "asset_class": "stock_cfd"}


def test_build_manifest_entries_sorted():
    from ops.chart_bootstrap import build_manifest_entries

    entries = build_manifest_entries(registry=_FakeRegistry())
    assert [row.symbol for row in entries] == ["EURUSD", "NVDA"]
    assert entries[0].chart_label == "chart_EURUSD"
    assert entries[0].mt5_period == 15
    assert entries[1].asset_class == "stock_cfd"


def test_write_manifest_and_generate(tmp_path):
    from ops.chart_bootstrap import build_manifest_entries, write_manifest

    csv_path = tmp_path / "ipc" / "chart_manifest.csv"
    json_path = tmp_path / "config" / "chart_manifest.json"
    entries = build_manifest_entries(registry=_FakeRegistry())
    payload = write_manifest(entries, csv_path=csv_path, json_path=json_path)

    assert csv_path.exists()
    raw = csv_path.read_bytes()
    assert raw.startswith(b"\xff\xfe"), "manifest CSV must be UTF-16 for MT5 FILE_COMMON reads"
    from ipc_text import read_ipc_text

    text = read_ipc_text(csv_path) or ""
    header, *rows = text.splitlines()
    assert header.startswith("symbol,broker_symbol")
    assert any("EURUSD" in row for row in rows)
    doc = json.loads(json_path.read_text(encoding="utf-8"))
    assert doc["ea_name"] == "FileBridgeEA_MultiSymbol"
    assert doc["template_name"] == "trading_os_bridge"
    assert payload["charts"][0]["symbol"] == "EURUSD"
    assert len(payload["charts"]) == 2


def test_evaluate_bootstrap_gaps(ipc_root, monkeypatch):
    monkeypatch.setenv("TRADING_OS_IPC", str(ipc_root))
    from ops.chart_bootstrap import evaluate_bootstrap_gaps

    report = evaluate_bootstrap_gaps(registry=_FakeRegistry(), max_heartbeat_age=120.0)
    summary = report["summary"]
    assert summary["enabled"] == 2
    assert summary["ready"] >= 1
    assert "NVDA" in report["missing_symbols"]
    assert report["ready_symbols"] == ["EURUSD"]

    chart_rows = {row["symbol"]: row for row in report["charts"]}
    assert chart_rows["EURUSD"]["fresh"] is True
    assert chart_rows["NVDA"]["result"] == "MISSING"


def test_evaluate_bootstrap_stale_chart(ipc_root, monkeypatch):
    monkeypatch.setenv("TRADING_OS_IPC", str(ipc_root))
    stale = ipc_root / "chart_EURUSD" / "heartbeat.txt"
    stale.write_text(f"{int(time.time()) - 500}|alive\n", encoding="utf-8")

    from ops.chart_bootstrap import evaluate_bootstrap_gaps

    report = evaluate_bootstrap_gaps(registry=_FakeRegistry(), max_heartbeat_age=60.0)
    assert "EURUSD" in report["stale_symbols"]
    row = next(row for row in report["charts"] if row["symbol"] == "EURUSD")
    assert row["result"] == "STALE"
