#!/usr/bin/env python3
"""Unit tests for signal engine research wiring."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from research.signal_research import apply_stock_research, resolve_signal_research_settings  # noqa: E402


def _intent(conf=0.72):
    return {
        "symbol": "NVDA",
        "side": "BUY",
        "confidence": conf,
        "strategy_id": "MA_CROSS",
    }


def _snapshot():
    return {
        "available": True,
        "top_picks": [
            {
                "symbol": "NVDA",
                "tier": "multibagger_candidate",
                "confidence": 0.88,
                "composite_score": 0.82,
                "thesis_tags": ["superior_growth"],
            }
        ],
    }


def test_fx_unchanged():
    intent, reason = apply_stock_research(_intent(), "EURUSD", asset_class="fx", snapshot=_snapshot())
    assert reason is None
    assert intent["confidence"] == 0.72
    assert "research" not in intent
    print("[test] PASS: FX intents skip research overlay")


def test_stock_boost_applied():
    intent, reason = apply_stock_research(_intent(), "NVDA", asset_class="stock_cfd", snapshot=_snapshot())
    assert reason is None
    assert intent["pattern_confidence"] == 0.72
    assert intent["confidence"] == 0.77
    assert intent["research"]["tier"] == "multibagger_candidate"
    print("[test] PASS: stock CFD gets tier confidence boost")


def test_hard_gate_blocks_low_tier():
    snapshot = {
        "available": True,
        "top_picks": [
            {
                "symbol": "NVDA",
                "tier": "watch",
                "confidence": 0.55,
                "composite_score": 0.50,
            }
        ],
    }
    controls = {
        "signal_research_block_below_tier": True,
        "signal_research_min_tier": "accumulate",
    }
    intent, reason = apply_stock_research(
        _intent(),
        "NVDA",
        asset_class="stock_cfd",
        controls=controls,
        snapshot=snapshot,
    )
    assert intent is None
    assert reason == "research_gate_blocked"
    print("[test] PASS: hard gate blocks below min tier")


def test_settings_merge_runtime_controls():
    settings = resolve_signal_research_settings({"signal_research_min_confidence": 0.8})
    assert settings["min_confidence"] == 0.8
    assert settings["tier_confidence_boost"]["multibagger_candidate"] == 0.05
    print("[test] PASS: runtime controls override yaml defaults")


def test_all():
    print("=" * 60)
    print("  SIGNAL RESEARCH UNIT TESTS")
    print("=" * 60)
    test_fx_unchanged()
    test_stock_boost_applied()
    test_hard_gate_blocks_low_tier()
    test_settings_merge_runtime_controls()
    print("=" * 60)
    print("  ALL SIGNAL RESEARCH TESTS PASSED")
    print("=" * 60)


if __name__ == "__main__":
    test_all()
