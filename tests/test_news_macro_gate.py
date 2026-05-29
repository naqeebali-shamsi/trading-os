#!/usr/bin/env python3
"""Tests for symbol-scoped news halt + TTL decay."""
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from cortex import news_macro_gate as nmg  # noqa: E402


def _fx_halt_decision(**overrides):
    now = time.time()
    base = {
        "source": "news_orchestrator",
        "recommendation": "halt_symbols",
        "halt_symbols": ["EURUSD", "GBPUSD", "XAUUSD"],
        "affected_symbols": {"EURUSD": 1.0, "GBPUSD": 0.83, "USDJPY": 0.33, "XAUUSD": 0.67},
        "impact_score": 0.95,
        "ts": now,
        "ttl_sec": 900,
        "expires_ts": now + 900,
    }
    base.update(overrides)
    return base


def test_gooogl_not_blocked_by_fx_halt():
    decision = _fx_halt_decision()
    blocked, reason = nmg.decision_blocks_symbol("GOOGL", decision)
    assert not blocked, reason
    blocked, reason = nmg.decision_blocks_symbol("EURUSD", decision)
    assert blocked and reason == "news_halt_symbol"
    print("[test] PASS: GOOGL not blocked by FX-only halt")


def test_expired_halt_does_not_block():
    decision = _fx_halt_decision(ts=time.time() - 2000, expires_ts=time.time() - 100)
    blocked, _ = nmg.decision_blocks_symbol("EURUSD", decision)
    assert not blocked
    print("[test] PASS: expired halt does not block")


def test_legacy_halt_new_scoped_to_impacted_symbols():
    now = time.time()
    decision = nmg.annotate_decision(
        {
            "source": "news_orchestrator",
            "recommendation": "halt_new",
            "affected_symbols": {"EURUSD": 1.0, "GBPUSD": 0.8, "GOOGL": 0.1},
            "impact_score": 0.95,
        },
        now=now,
    )
    assert decision["recommendation"] == "halt_symbols"
    assert "EURUSD" in decision["halt_symbols"]
    assert "GOOGL" not in decision["halt_symbols"]
    assert not nmg.decision_blocks_symbol("GOOGL", decision)[0]
    assert nmg.decision_blocks_symbol("EURUSD", decision)[0]
    print("[test] PASS: legacy halt_new scoped via annotate_decision")


def test_global_halt_downgraded_when_no_symbols():
    decision = nmg.annotate_decision(
        {
            "source": "news_orchestrator",
            "recommendation": "halt_new",
            "affected_symbols": {},
            "impact_score": 0.95,
        }
    )
    assert decision["recommendation"] == "reduce_size"
    assert decision.get("halt_symbols") == []
    print("[test] PASS: global halt_new downgraded to reduce_size")


def test_single_strong_pattern_reaches_seventy():
    from cortex import signal_generator_v2 as sg  # noqa: E402

    patterns = [{"pattern": "hammer", "direction": "bullish", "strength": "strong"}]
    score = sg.confluence_score("EURUSD", "ranging", patterns)
    assert abs(score - 0.70) < 0.001
    print("[test] PASS: single strong pattern scores 0.70")


def test_all():
    print("=" * 60)
    print("  NEWS MACRO GATE TESTS")
    print("=" * 60)
    test_gooogl_not_blocked_by_fx_halt()
    test_expired_halt_does_not_block()
    test_legacy_halt_new_scoped_to_impacted_symbols()
    test_global_halt_downgraded_when_no_symbols()
    test_single_strong_pattern_reaches_seventy()
    print("=" * 60)
    print("  ALL NEWS MACRO GATE TESTS PASSED")
    print("=" * 60)


if __name__ == "__main__":
    test_all()
