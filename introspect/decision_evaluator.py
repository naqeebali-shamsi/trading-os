#!/usr/bin/env python3
"""Decision evaluation and training-data loop.

This module turns live cortex/strategy decisions into explicit evaluation records:
- what evidence was available
- what confidence factors were present
- what guard/risk gate did
- what an ensemble reviewer should learn from later

It is read-only/advisory. It never publishes order intents or changes risk limits.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "nervous"))

from bus import publish, subscribe  # noqa: E402
from introspect.factors import (  # noqa: E402
    _proposal_from_brain,
    confidence_factors,
    latest_payload,
)
from introspect.io import append_jsonl, load_json_state, save_json_state  # noqa: E402

STATE_FILE = ROOT / "introspect" / ".decision_eval_state.json"
EVAL_FILE = ROOT / "memory" / "decision_evals.jsonl"
TRAINING_FILE = ROOT / "memory" / "training" / "decision_training.jsonl"
ENSEMBLE_FILE = ROOT / "config" / "llm_ensemble.yaml"

TOPIC_STATE_KEYS = {
    "cortex.brain.result": "last_seq_brain",
    "cortex.decision_guard": "last_seq_guard",
    "cortex.decision": "last_seq_decision",
    "market.signal": "last_seq_signal",
    "muscle.order.intent": "last_seq_intent",
    "muscle.order.filled": "last_seq_filled",
    "muscle.order.rejected": "last_seq_rejected",
    "muscle.order.timeout": "last_seq_timeout",
}


def load_state(path: Path = STATE_FILE) -> Dict[str, Any]:
    return load_json_state(path, {key: 0 for key in TOPIC_STATE_KEYS.values()})


def save_state(state: Dict[str, Any], path: Path = STATE_FILE) -> None:
    save_json_state(path, state)


def build_eval_record(topic: str, event: Dict[str, Any], *, latest_guard: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    payload = event.get("payload", {})
    is_brain = topic == "cortex.brain.result"
    is_signal = topic == "market.signal"
    brain = payload if is_brain else latest_payload("cortex.brain.result")
    guard = latest_guard or latest_payload("cortex.decision_guard")
    signal = payload if is_signal else {}
    proposal = _proposal_from_brain(brain) if brain else signal
    action = proposal.get("action") or payload.get("action") or signal.get("action") or "UNKNOWN"
    record = {
        "ts": time.time(),
        "event_ts": event.get("ts"),
        "source_topic": topic,
        "source_seq": event.get("seq"),
        "type": "decision_eval",
        "action": action,
        "symbol": proposal.get("symbol") or signal.get("symbol") or payload.get("symbol"),
        "side": proposal.get("side") or signal.get("side") or payload.get("side"),
        "strategy_id": proposal.get("strategy_id") or signal.get("strategy_id") or payload.get("strategy_id"),
        "confidence": proposal.get("confidence") or signal.get("confidence"),
        "reasoning": proposal.get("reasoning") or payload.get("reasoning") or signal.get("reason"),
        "factors": confidence_factors(brain=brain, signal=signal, guard=guard),
        "outcome_pending": True,
    }
    return record


def training_example(eval_record: Dict[str, Any]) -> Dict[str, Any]:
    """Convert eval record to supervised preference/eval JSONL row.

    Label is intentionally pending until post-trade outcome joins it. This lets us
    train reviewers first on calibration and later on realized PnL.
    """
    return {
        "ts": eval_record["ts"],
        "type": "decision_training_example",
        "input": {
            "action": eval_record.get("action"),
            "symbol": eval_record.get("symbol"),
            "side": eval_record.get("side"),
            "confidence": eval_record.get("confidence"),
            "factors": eval_record.get("factors"),
            "reasoning": eval_record.get("reasoning"),
        },
        "target": {
            "label": "pending_outcome",
            "review_tasks": [
                "calibrate_confidence",
                "identify_missing_evidence",
                "challenge_trade_or_hold_decision",
                "propose_better_features_not_orders",
            ],
        },
    }


def poll_events(state: Dict[str, Any]) -> List[tuple[str, Dict[str, Any]]]:
    gathered: List[tuple[str, Dict[str, Any]]] = []
    for topic, key in TOPIC_STATE_KEYS.items():
        events = subscribe(topic, since_seq=int(state.get(key, 0)), limit=200)
        for ev in events:
            gathered.append((topic, ev))
            state[key] = max(int(state.get(key, 0)), int(ev.get("seq", 0) or 0))
    return sorted(gathered, key=lambda item: item[1].get("seq", 0))


def run_once(state: Optional[Dict[str, Any]] = None, *, persist: bool = True) -> List[Dict[str, Any]]:
    state = state or load_state()
    records = []
    latest_guard = latest_payload("cortex.decision_guard")
    for topic, ev in poll_events(state):
        if topic not in {"cortex.brain.result", "market.signal"}:
            continue
        record = build_eval_record(topic, ev, latest_guard=latest_guard)
        records.append(record)
        if persist:
            append_jsonl(EVAL_FILE, record)
            append_jsonl(TRAINING_FILE, training_example(record))
            publish("introspect.decision_eval", record)
    if persist:
        save_state(state)
    return records


def run(interval: float) -> None:
    print(f"[decision_eval] running interval={interval}s eval_file={EVAL_FILE}", flush=True)
    while True:
        try:
            records = run_once()
            if records:
                print(f"[decision_eval] wrote {len(records)} eval records", flush=True)
        except Exception as exc:
            publish("cortex.fallback", {"layer": "decision_eval", "error": str(exc), "action": "crash"})
        time.sleep(interval)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Decision eval/training data loop")
    parser.add_argument("--once", action="store_true", help="Run one polling cycle")
    parser.add_argument("--interval", type=float, default=60.0)
    args = parser.parse_args(argv)
    if args.once:
        rows = run_once()
        print(json.dumps({"records": len(rows), "eval_file": str(EVAL_FILE), "training_file": str(TRAINING_FILE)}, indent=2))
        return 0
    run(args.interval)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
