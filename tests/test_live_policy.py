#!/usr/bin/env python3
"""Tests for cortex/live_policy approved overlay."""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from cortex import live_policy as lp  # noqa: E402


def test_apply_promotion_and_calibrate():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        policy_file = tmp_path / "live_policy.json"
        history_file = tmp_path / "live_policy_history.jsonl"
        orig_policy = lp.LIVE_POLICY_FILE
        orig_history = lp.POLICY_HISTORY_FILE
        lp.LIVE_POLICY_FILE = policy_file
        lp.POLICY_HISTORY_FILE = history_file
        try:
            policy = lp.apply_promotion_patch(
                {"type": "strategy_weight", "strategy_id": "RSI_MEAN_REVERSION", "weight": 0.4, "active": True},
                promotion_id="promo_test_1",
            )
            assert policy["version"] == 1
            overlay = lp.strategy_overlay("RSI_MEAN_REVERSION")
            assert overlay["weight"] == 0.4

            policy = lp.apply_promotion_patch(
                {
                    "type": "confidence_calibration",
                    "mapping": {"confidence_offset": -0.03, "confidence_scale": 1.0, "per_pattern_bonus": 0.01},
                    "signal_min_confidence": 0.72,
                },
                promotion_id="promo_test_2",
            )
            assert lp.effective_signal_min_confidence(0.70) == 0.72
            calibrated = lp.calibrate_confidence(0.80, pattern_count=2)
            assert calibrated == round(min(1.0, max(0.0, 0.80 - 0.03 + 0.02)), 2)

            restored = lp.rollback(version=1)
            assert restored is not None
            assert restored["version"] == 1
        finally:
            lp.LIVE_POLICY_FILE = orig_policy
            lp.POLICY_HISTORY_FILE = orig_history


def test_research_tier_boost_overlay():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        policy_file = tmp_path / "live_policy.json"
        history_file = tmp_path / "live_policy_history.jsonl"
        orig_policy = lp.LIVE_POLICY_FILE
        orig_history = lp.POLICY_HISTORY_FILE
        lp.LIVE_POLICY_FILE = policy_file
        lp.POLICY_HISTORY_FILE = history_file
        try:
            lp.apply_promotion_patch(
                {"type": "research_tier_boost", "tier": "high_conviction", "boost": 0.08},
                promotion_id="promo_boost",
            )
            merged = lp.effective_research_tier_boost({"high_conviction": 0.05, "watch": 0.0})
            assert merged["high_conviction"] == 0.08
            assert merged["watch"] == 0.0
        finally:
            lp.LIVE_POLICY_FILE = orig_policy
            lp.POLICY_HISTORY_FILE = orig_history


def test_macro_lexicon_live_overlay():
    from cortex import macro_lexicon as mx

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        policy_file = tmp_path / "live_policy.json"
        history_file = tmp_path / "live_policy_history.jsonl"
        orig_policy = lp.LIVE_POLICY_FILE
        orig_history = lp.POLICY_HISTORY_FILE
        lp.LIVE_POLICY_FILE = policy_file
        lp.POLICY_HISTORY_FILE = history_file
        mx._cache = {"impact_keywords": {"rate hike": 1.0}}
        try:
            lp.apply_promotion_patch(
                {"type": "macro_lexicon_weight", "keyword": "rate hike", "weight": 1.5},
                promotion_id="promo_lex",
            )
            merged = mx.get_impact_keywords()
            assert merged["rate hike"] == 1.5
        finally:
            lp.LIVE_POLICY_FILE = orig_policy
            lp.POLICY_HISTORY_FILE = orig_history
            mx._cache = None
