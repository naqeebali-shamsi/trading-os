#!/usr/bin/env python3
"""Advisory LLM ensemble reviewer for strategist training data.

Consumes memory/training/decision_training.jsonl and writes critique labels to
memory/training/ensemble_reviews.jsonl. Reviewers are configured in
config/llm_ensemble.yaml and must never publish orders or mutate runtime state.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "nervous"))

from bus import publish  # noqa: E402
from cortex.llm_client import LLMClient  # noqa: E402
from introspect.decision_evaluator import TRAINING_FILE  # noqa: E402
from introspect.io import (  # noqa: E402
    append_jsonl,
    load_json_state,
    read_jsonl_from_offset,
    save_json_state,
)

CONFIG_FILE = ROOT / "config" / "llm_ensemble.yaml"
STATE_FILE = ROOT / "introspect" / ".ensemble_reviewer_state.json"
DEFAULT_OUTPUT = ROOT / "memory" / "training" / "ensemble_reviews.jsonl"

SYSTEM_PROMPT = """You are an advisory quant review agent.
Return JSON only. You cannot place orders, request execution, or change risk.
Review the provided decision-training example for calibration and missing evidence.
Schema:
{
  "reviewer": "name",
  "verdict": "agree|disagree|insufficient_evidence",
  "confidence_calibration": "too_low|reasonable|too_high|unknown",
  "missing_evidence": [],
  "risk_notes": [],
  "features_to_track": [],
  "lesson": "short reusable quant lesson"
}
"""


def load_config(path: Path = CONFIG_FILE) -> Dict[str, Any]:
    if not path.exists():
        return {"reviewers": [], "consensus": {"output_file": str(DEFAULT_OUTPUT)}}
    return yaml.safe_load(path.read_text()) or {}


def load_state(path: Path = STATE_FILE) -> Dict[str, Any]:
    return load_json_state(path, {"training_offset": 0})


def save_state(state: Dict[str, Any], path: Path = STATE_FILE) -> None:
    save_json_state(path, state)


def enabled_reviewers(config: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [r for r in config.get("reviewers", []) if r.get("enabled")]


def review_example(example: Dict[str, Any], reviewer: Dict[str, Any], *, client: Optional[LLMClient] = None) -> Dict[str, Any]:
    client = client or LLMClient()
    prompt = json.dumps({"reviewer": reviewer, "example": example}, sort_keys=True, default=str)
    result = client.complete_json(
        prompt,
        system=SYSTEM_PROMPT.replace('"reviewer": "name"', f'"reviewer": "{reviewer.get("name", "reviewer")}"'),
        provider=reviewer.get("provider"),
        model=reviewer.get("model"),
        max_tokens=700,
        temperature=0.1,
        timeout=20,
    )
    parsed = result.parsed if result.ok and isinstance(result.parsed, dict) else None
    return {
        "ts": time.time(),
        "type": "ensemble_decision_review",
        "reviewer": reviewer.get("name"),
        "provider": result.provider,
        "model": result.model,
        "ok": bool(result.ok and parsed),
        "error": result.error,
        "latency_ms": result.latency_ms,
        "example_ts": example.get("ts"),
        "review": parsed or {
            "verdict": "insufficient_evidence",
            "confidence_calibration": "unknown",
            "missing_evidence": ["reviewer_unavailable_or_invalid_json"],
            "risk_notes": [result.error or "review_failed"],
            "features_to_track": [],
            "lesson": "Do not train on unavailable reviewer output as truth.",
        },
    }


def run_once(*, state: Optional[Dict[str, Any]] = None, config: Optional[Dict[str, Any]] = None, client: Optional[LLMClient] = None, persist: bool = True, max_examples: Optional[int] = None) -> List[Dict[str, Any]]:
    state = state or load_state()
    config = config or load_config()
    rows, new_offset = read_jsonl_from_offset(TRAINING_FILE, int(state.get("training_offset", 0)))
    if max_examples is not None:
        rows = rows[: max(0, int(max_examples))]
    reviewers = enabled_reviewers(config)
    output = ROOT / (config.get("consensus", {}).get("output_file") or str(DEFAULT_OUTPUT))
    reviews: List[Dict[str, Any]] = []
    for example in rows:
        for reviewer in reviewers:
            review = review_example(example, reviewer, client=client)
            reviews.append(review)
            if persist:
                append_jsonl(output, review)
                publish("introspect.ensemble_review", review)
    state["training_offset"] = new_offset
    if persist:
        save_state(state)
    return reviews


def run(interval: float) -> None:
    print(f"[ensemble_reviewer] running interval={interval}s config={CONFIG_FILE}", flush=True)
    while True:
        try:
            reviews = run_once()
            if reviews:
                print(f"[ensemble_reviewer] wrote {len(reviews)} reviews", flush=True)
        except Exception as exc:
            publish("cortex.fallback", {"layer": "ensemble_reviewer", "error": str(exc), "action": "crash"})
        time.sleep(interval)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Advisory ensemble reviewer")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--interval", type=float, default=300.0)
    args = parser.parse_args(argv)
    if args.once:
        reviews = run_once()
        print(json.dumps({"reviews": len(reviews), "config": str(CONFIG_FILE)}, indent=2))
        return 0
    run(args.interval)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
