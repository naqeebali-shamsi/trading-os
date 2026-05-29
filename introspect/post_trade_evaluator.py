#!/usr/bin/env python3
"""Passive post-trade evaluator.

Joins deterministic decision-eval records with realized trade outcomes and emits
append-only advisory reviews. This module is intentionally outside the live order
path: no order topics, no risk writes, no LLM dependency.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "nervous"))

from bus import publish  # noqa: E402
from introspect.io import append_jsonl, load_json_state, read_jsonl, save_json_state  # noqa: E402

STATE_FILE = ROOT / "introspect" / ".post_trade_eval_state.json"
DECISION_EVAL_FILE = ROOT / "memory" / "decision_evals.jsonl"
OUTCOME_FILE = ROOT / "memory" / "trade_outcomes.jsonl"
REVIEW_FILE = ROOT / "memory" / "post_trade_reviews.jsonl"
TRAINING_FILE = ROOT / "memory" / "training" / "post_trade_training.jsonl"


def load_state(path: Optional[Path] = None) -> Dict[str, Any]:
    return load_json_state(path or STATE_FILE, {"reviewed_ids": []})


def save_state(state: Dict[str, Any], path: Optional[Path] = None) -> None:
    save_json_state(path or STATE_FILE, state)


def review_id_for(outcome: Dict[str, Any], decision: Optional[Dict[str, Any]]) -> str:
    raw = "|".join(str(x or "") for x in [outcome.get("order_id"), outcome.get("symbol"), outcome.get("side"), outcome.get("ts"), (decision or {}).get("source_seq")])
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def match_decision(outcome: Dict[str, Any], decisions: Iterable[Dict[str, Any]]) -> tuple[Optional[Dict[str, Any]], str]:
    decisions = list(decisions)
    order_id = outcome.get("order_id")
    if order_id:
        for row in reversed(decisions):
            if row.get("order_id") == order_id:
                return row, "order_id"
    symbol = outcome.get("symbol")
    side = outcome.get("side")
    ts = float(outcome.get("ts") or time.time())
    candidates = [
        row for row in decisions
        if row.get("symbol") == symbol and (not side or row.get("side") == side)
    ]
    if candidates:
        nearest = min(candidates, key=lambda row: abs(float(row.get("event_ts") or row.get("ts") or 0) - ts))
        return nearest, "symbol_side_nearest_ts"
    return None, "unmatched"


def _confidence_bucket(confidence: Any, pnl: float) -> str:
    try:
        conf = float(confidence)
    except (TypeError, ValueError):
        conf = 0.0
    if conf >= 0.75 and pnl < 0:
        return "high_confidence_loss"
    if conf <= 0.4 and pnl > 0:
        return "low_confidence_win"
    if pnl > 0:
        return "win"
    if pnl < 0:
        return "loss"
    return "breakeven"


def build_review(outcome: Dict[str, Any], decision: Optional[Dict[str, Any]] = None, *, join_status: str = "unmatched") -> Dict[str, Any]:
    decision = decision or {}
    pnl = float(outcome.get("pnl", 0) or 0)
    factors = decision.get("factors") or {}
    penalties = list(factors.get("confidence_penalties") or [])
    boosts = list(factors.get("confidence_boosts") or [])
    bucket = _confidence_bucket(decision.get("confidence"), pnl)
    validated = penalties if pnl < 0 else boosts
    contradicted = boosts if pnl < 0 else penalties
    missing = []
    if not outcome.get("entry_price"):
        missing.append("entry_price")
    if not outcome.get("exit_price"):
        missing.append("exit_price")
    if not decision:
        missing.append("decision_eval")
    attribution = outcome.get("attribution") or {}
    lessons = []
    if bucket == "high_confidence_loss":
        lessons.append("High-confidence trade lost. Re-check boosted factors before promoting similar setups.")
    if "forecast_side_conflict" in penalties and pnl < 0:
        lessons.append("Forecast conflict coincided with loss. Penalize similar conflicts until enough counterexamples exist.")
    if not lessons:
        lessons.append("Record outcome for calibration; do not change live strategy from a single sample.")
    return {
        "ts": time.time(),
        "type": "post_trade_review",
        "review_id": review_id_for(outcome, decision),
        "order_id": outcome.get("order_id"),
        "symbol": outcome.get("symbol"),
        "side": outcome.get("side"),
        "strategy_id": outcome.get("strategy_id") or decision.get("strategy_id"),
        "join_status": join_status,
        "review_required": join_status == "unmatched" or bool(attribution.get("review_required")),
        "decision_eval_ref": {
            "source_topic": decision.get("source_topic"),
            "source_seq": decision.get("source_seq"),
            "event_ts": decision.get("event_ts"),
        },
        "outcome_ref": {
            "ts": outcome.get("ts"),
            "result": outcome.get("result"),
            "pnl": pnl,
        },
        "calibration": {
            "decision_confidence": decision.get("confidence"),
            "bucket": bucket,
            "score": 1 if pnl > 0 else -1 if pnl < 0 else 0,
        },
        "factor_review": {
            "penalties_present": penalties,
            "boosts_present": boosts,
            "validated_factors": validated,
            "contradicted_factors": contradicted,
            "missing_evidence": missing,
        },
        "attribution": {
            "primary": attribution.get("primary", "unknown"),
            "tags": attribution.get("tags", []),
            "confidence": attribution.get("confidence", 0),
        },
        "lessons": lessons,
        "execution_safe": True,
        "advisory_only": True,
    }


def training_example(review: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "ts": review["ts"],
        "type": "post_trade_training_example",
        "input": {
            "symbol": review.get("symbol"),
            "side": review.get("side"),
            "decision_eval_ref": review.get("decision_eval_ref"),
            "factor_review": review.get("factor_review"),
            "attribution": review.get("attribution"),
        },
        "target": {
            "calibration_bucket": review.get("calibration", {}).get("bucket"),
            "lessons": review.get("lessons", []),
            "label_confidence": review.get("attribution", {}).get("confidence", 0),
        },
    }


def run_once(state: Optional[Dict[str, Any]] = None, *, persist: bool = True) -> List[Dict[str, Any]]:
    state = state or load_state()
    reviewed = set(state.get("reviewed_ids") or [])
    decisions = read_jsonl(DECISION_EVAL_FILE)
    reviews = []
    for outcome in read_jsonl(OUTCOME_FILE):
        if outcome.get("type") != "trade_outcome":
            continue
        decision, join_status = match_decision(outcome, decisions)
        review = build_review(outcome, decision, join_status=join_status)
        if review["review_id"] in reviewed:
            continue
        reviews.append(review)
        reviewed.add(review["review_id"])
        if persist:
            append_jsonl(REVIEW_FILE, review)
            append_jsonl(TRAINING_FILE, training_example(review))
            publish("introspect.post_trade_review", review)
    state["reviewed_ids"] = sorted(reviewed)[-2000:]
    if persist:
        save_state(state)
    return reviews


def run(interval: float = 60.0) -> None:
    print(f"[post_trade_evaluator] started interval={interval}s", flush=True)
    while True:
        try:
            run_once(persist=True)
        except Exception as exc:
            publish("introspect.post_trade_eval.error", {"error": str(exc), "ts": time.time()})
        time.sleep(interval)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Passive post-trade evaluator")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--interval", type=float, default=60.0)
    args = parser.parse_args(argv)
    if args.once:
        print(json.dumps(run_once(persist=True), indent=2, sort_keys=True))
        return 0
    run(args.interval)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
