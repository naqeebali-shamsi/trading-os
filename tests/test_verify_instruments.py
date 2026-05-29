#!/usr/bin/env python3
"""Tests for read-only instrument verifier."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bridge.mt5_ipc_protocol import encode_command  # noqa: E402
from scripts import verify_instruments as verifier  # noqa: E402


class DummyPaths:
    root = Path("/tmp")


def test_broker_candidates_include_aliases_once():
    cfg = {"broker_symbol": "NAS100", "aliases": ["US100", "NAS100", "NAS100.cash"]}
    assert verifier.broker_candidates("NAS100", cfg) == ["NAS100", "US100", "NAS100.cash"]
    print("[test] PASS: broker candidates")


def test_no_trade_symbol_info_command_encoding():
    text = encode_command("GET_SYMBOL_INFO", "cid123", "EURUSD")
    assert text == "GET_SYMBOL_INFO,cid123,EURUSD\r\n"
    try:
        encode_command("ORDER", "cid123")
        assert False, "ORDER must remain forbidden in no-trade helper"
    except ValueError:
        pass
    print("[test] PASS: no-trade symbol info command encoding")


def test_verify_symbol_with_local_root_tick():
    bridge = {"ok": True, "root_tick": {"symbol": "EURUSD", "bid": 1.1, "ask": 1.1001}}
    row = verifier.verify_symbol(DummyPaths(), "EURUSD", {"broker_symbol": "EURUSD", "asset_class": "forex", "enabled": True}, timeout_sec=0.1, fetcher=lambda *_: {}, live_query=False, bridge=bridge)
    assert row["status"] == "verified"
    assert row["source"] == "root_tick"
    print("[test] PASS: local root tick verifies symbol")


def test_verify_symbol_with_broker_fetcher_alias_success():
    calls = []
    def fetch(paths, symbol, timeout):
        calls.append(symbol)
        if symbol == "US100":
            return {"type": "symbol_info", "ok": True, "symbol": symbol, "has_tick": True, "bid": 100, "ask": 101}
        return {"type": "symbol_info", "ok": False, "symbol": symbol, "selected": False, "error": "not_found"}
    cfg = {"broker_symbol": "NAS100", "aliases": ["US100"], "asset_class": "index_cfd", "enabled": False}
    row = verifier.verify_symbol(DummyPaths(), "NAS100", cfg, timeout_sec=0.1, fetcher=fetch, live_query=True, bridge={"ok": True, "root_tick": {}})
    assert calls == ["NAS100", "US100"]
    assert row["status"] == "verified"
    assert row["broker_symbol"] == "US100"
    print("[test] PASS: broker alias fetch verifies symbol")


def test_verify_symbol_with_chart_tick():
    bridge = {"ok": True, "root_tick": {}}
    chart_tick = {"symbol": "RELIANCE", "bid": 2500.0, "ask": 2500.5}
    evidence = {
        "root_tick_match": False,
        "chart_present": True,
        "chart_tick": chart_tick,
    }

    def fake_local_evidence(symbol, cfg, bridge_state):
        return evidence

    original = verifier.local_evidence
    verifier.local_evidence = fake_local_evidence
    try:
        row = verifier.verify_symbol(
            DummyPaths(),
            "RELIANCE",
            {"broker_symbol": "RELIANCE", "asset_class": "stock_cfd", "enabled": False},
            timeout_sec=0.1,
            fetcher=lambda *_: {},
            live_query=False,
            bridge=bridge,
        )
    finally:
        verifier.local_evidence = original
    assert row["status"] == "verified"
    assert row["source"] == "chart_tick"
    print("[test] PASS: local chart tick verifies symbol")


def test_resolve_no_trade_paths_prefers_fresh_chart(monkeypatch, tmp_path):
    ipc = tmp_path / "ipc"
    chart = ipc / "chart_EURUSD"
    chart.mkdir(parents=True)
    now = int(__import__("time").time())
    (chart / "heartbeat.txt").write_text(f"{now}|alive\n", encoding="utf-8")
    (chart / "tick.txt").write_text("EURUSD,1.08,1.0802,0\n", encoding="utf-8")

    monkeypatch.setattr(verifier.readiness_gate, "IPC", ipc)
    monkeypatch.setattr(verifier, "bridge_chart_dirs", lambda _ipc: [chart])
    paths, route = verifier.resolve_no_trade_paths(max_heartbeat_age=120.0)
    assert route == "chart_EURUSD"
    assert paths.root == chart
    print("[test] PASS: resolve_no_trade_paths chart fallback")


def test_all():
    print("=" * 60)
    print("  INSTRUMENT VERIFIER TESTS")
    print("=" * 60)
    test_broker_candidates_include_aliases_once()
    test_no_trade_symbol_info_command_encoding()
    test_verify_symbol_with_local_root_tick()
    test_verify_symbol_with_broker_fetcher_alias_success()
    test_verify_symbol_with_chart_tick()
    print("=" * 60)
    print("  ALL INSTRUMENT VERIFIER TESTS PASSED")
    print("=" * 60)


if __name__ == "__main__":
    test_all()
