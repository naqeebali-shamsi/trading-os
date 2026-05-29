#!/usr/bin/env python3
"""Tests for configurable readiness / preflight policy."""
from __future__ import annotations

import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "nervous"))

from cortex.instrument_registry import InstrumentRegistry  # noqa: E402
from ops.readiness_eval import ReadinessOptions, evaluate_readiness  # noqa: E402
from ops.readiness_policy import load_readiness_policy  # noqa: E402


def _write_chart(ipc: Path, label: str, symbol: str, bid: float, ask: float) -> None:
    chart = ipc / label
    chart.mkdir(parents=True, exist_ok=True)
    now = int(time.time())
    (chart / "heartbeat.txt").write_text(f"{now}|alive\n", encoding="utf-8")
    (chart / "tick.txt").write_text(f"{symbol},{bid},{ask},{now}\n", encoding="utf-8")


def test_boot_required_resolution():
    registry = InstrumentRegistry(ROOT / "config" / "instruments.yaml")
    assert registry.boot_required("EURUSD") is True
    assert registry.boot_required("NVDA") is False
    print("[test] PASS: boot_required resolution from asset class config")


def test_per_asset_class_preflight_defers_stocks(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADING_OS_IPC", str(tmp_path / "ipc"))
    ipc = tmp_path / "ipc"
    for symbol, bid, ask in (
        ("EURUSD", 1.10000, 1.10010),
        ("GBPUSD", 1.25000, 1.25010),
        ("USDJPY", 150.100, 150.101),
        ("XAUUSD", 2300.00, 2300.40),
        ("NVDA", 120.00, 120.20),
    ):
        _write_chart(ipc, f"chart_{symbol}", symbol, bid, ask)
    _write_chart(ipc, "chart_IBM", "IBM", 200.0, 200.5)

    registry = InstrumentRegistry(ROOT / "config" / "instruments.yaml")
    closed_stocks = datetime(2026, 5, 7, 3, 30)
    ticks = {
        "EURUSD": {"bid": 1.10000, "ask": 1.10010, "quote_age_sec": 1.0},
        "GBPUSD": {"bid": 1.25000, "ask": 1.25010, "quote_age_sec": 1.0},
        "USDJPY": {"bid": 150.100, "ask": 150.101, "quote_age_sec": 1.0},
        "XAUUSD": {"bid": 2300.00, "ask": 2300.40, "quote_age_sec": 1.0},
        "NVDA": {"bid": 120.00, "ask": 120.20, "quote_age_sec": 1.0},
    }
    snap = registry.readiness_snapshot(
        [f"chart_{sym}" for sym in ticks],
        ticks,
        now=closed_stocks,
    )
    assert snap["EURUSD"]["ready"] is True
    assert snap["NVDA"]["ready"] is False

    result = evaluate_readiness(
        ROOT,
        ReadinessOptions(live=True, instrument_gate="per_asset_class", chart_gate="enabled_symbols"),
        ipc_dir=ipc,
        registry=registry,
        now=closed_stocks,
    )
    assert result.ok is True
    assert any("boot_deferred" in c["name"] for c in result.checks)
    assert any(c["name"] == "chart_IBM heartbeat (out_of_scope)" for c in result.checks)
    print("[test] PASS: per_asset_class preflight boots with deferred stocks")


def test_strict_instruments_legacy_all_enabled(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADING_OS_IPC", str(tmp_path / "ipc"))
    ipc = tmp_path / "ipc"
    _write_chart(ipc, "chart_EURUSD", "EURUSD", 1.1000, 1.1002)
    _write_chart(ipc, "chart_AAPL", "AAPL", 180.0, 180.2)

    registry = InstrumentRegistry(ROOT / "config" / "instruments.yaml")
    closed_stocks = datetime(2026, 5, 7, 3, 30)
    result = evaluate_readiness(
        ROOT,
        ReadinessOptions(live=True, strict_instruments=True, chart_gate="enabled_symbols"),
        ipc_dir=ipc,
        registry=registry,
        now=closed_stocks,
    )
    assert result.ok is False
    assert any(c["name"] == "instrument AAPL ready" and not c["ok"] for c in result.checks)
    print("[test] PASS: strict_instruments preserves legacy all-enabled gate")


def test_session_closed_defers_forex_when_bridge_up(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADING_OS_IPC", str(tmp_path / "ipc"))
    ipc = tmp_path / "ipc"
    for symbol, bid, ask in (
        ("EURUSD", 1.10000, 1.10010),
        ("GBPUSD", 1.25000, 1.25010),
        ("USDJPY", 150.100, 150.101),
        ("XAUUSD", 2300.00, 2300.40),
    ):
        _write_chart(ipc, f"chart_{symbol}", symbol, bid, ask)

    registry = InstrumentRegistry(ROOT / "config" / "instruments.yaml")
    weekend = datetime(2026, 5, 9, 12, 0)  # Saturday
    result = evaluate_readiness(
        ROOT,
        ReadinessOptions(live=True, instrument_gate="per_asset_class", chart_gate="enabled_symbols"),
        ipc_dir=ipc,
        registry=registry,
        now=weekend,
    )
    assert result.ok is True
    assert any("EURUSD ready (boot_deferred)" in c["name"] for c in result.checks)
    assert "EURUSD:BLOCKED_SESSION_CLOSED" in result.boot_deferred_instruments
    print("[test] PASS: session-closed forex defers instead of blocking LIVE boot")


def test_policy_loaded_from_defaults():
    registry = InstrumentRegistry(ROOT / "config" / "instruments.yaml")
    policy = load_readiness_policy(registry)
    assert policy.instrument_gate == "per_asset_class"
    assert policy.chart_gate == "enabled_symbols"
    print("[test] PASS: policy loaded from instruments defaults")
