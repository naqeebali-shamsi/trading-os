#!/usr/bin/env python3
"""Tests for Dream Lab promotion queue."""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from rd import promotions  # noqa: E402
from cortex import live_policy as lp  # noqa: E402


def test_propose_approve_reject_flow():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        queue_file = tmp_path / "promotion_queue.jsonl"
        policy_file = tmp_path / "live_policy.json"
        history_file = tmp_path / "live_policy_history.jsonl"
        orig_queue = promotions.QUEUE_FILE
        orig_policy = lp.LIVE_POLICY_FILE
        orig_history = lp.POLICY_HISTORY_FILE
        promotions.QUEUE_FILE = queue_file
        lp.LIVE_POLICY_FILE = policy_file
        lp.POLICY_HISTORY_FILE = history_file
        try:
            row = promotions.propose(
                ptype="strategy_weight",
                summary="Test promote MA cross",
                patch={"strategy_id": "MA_CROSS_SMA9_21", "weight": 1.2, "active": True},
                evidence={"sharpe": 1.1},
                agent="test",
            )
            assert row["status"] == "pending"
            pending = promotions.list_promotions(status="pending")
            assert len(pending) == 1

            result = promotions.approve(row["id"], actor="tester")
            assert result["policy"]["strategies"]["MA_CROSS_SMA9_21"]["weight"] == 1.2
            assert promotions.list_promotions(status="pending") == []

            row2 = promotions.propose(
                ptype="signal_min_confidence",
                summary="Raise min confidence",
                patch={"value": 0.75},
                agent="test",
            )
            rejected = promotions.reject(row2["id"], reason="need more data")
            assert rejected["status"] == "rejected"
            assert rejected["reject_reason"] == "need more data"

            row3 = promotions.propose(
                ptype="strategy_weight",
                summary="Unique summary for dedupe",
                patch={"strategy_id": "MA_CROSS_SMA9_21", "weight": 1.3, "active": True},
                agent="test",
                dedupe_window_sec=86400,
            )
            assert promotions.propose(
                ptype="strategy_weight",
                summary="Another summary",
                patch={"strategy_id": "MA_CROSS_SMA9_21", "weight": 1.4, "active": True},
                agent="test",
                dedupe_window_sec=86400,
            )["id"] == row3["id"]
        finally:
            promotions.QUEUE_FILE = orig_queue
            lp.LIVE_POLICY_FILE = orig_policy
            lp.POLICY_HISTORY_FILE = orig_history
