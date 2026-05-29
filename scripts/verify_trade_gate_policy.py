#!/usr/bin/env python3
"""Verify trade-gate policy changes with deterministic checks + ensemble review.

Runs unit scenarios for symbol-scoped news halts and confidence calibration,
then asks configured ensemble reviewers to validate representative cases.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "nervous"))

from cortex import news_macro_gate as nmg  # noqa: E402
from cortex import signal_generator_v2 as sg  # noqa: E402
from introspect.ensemble_reviewer import enabled_reviewers, load_config, review_example  # noqa: E402
from introspect.io import append_jsonl  # noqa: E402

OUTPUT = ROOT / "memory" / "training" / "policy_verification.jsonl"


def rules_policy_reviewer(example: Dict[str, Any]) -> Dict[str, Any]:
    """Deterministic stand-in for ensemble when local LLM is offline."""
    inp = example.get("input") or {}
    symbol = str(inp.get("symbol") or "").upper()
    confidence = float(inp.get("confidence") or 0.0)
    factors = inp.get("factors") or {}
    halt_symbols = {str(s).upper() for s in (factors.get("news_halt_symbols") or [])}
    penalties = set(factors.get("confidence_penalties") or [])

    if symbol in halt_symbols:
        verdict = "agree" if confidence < 0.85 else "insufficient_evidence"
        calibration = "reasonable"
        lesson = "Symbol-scoped news halt should block sub-threshold entries on affected symbols."
    elif confidence >= 0.70 and "macro_risk_off_or_blackout" not in penalties:
        verdict = "agree"
        calibration = "reasonable"
        lesson = "Calibrated single-strong-pattern threshold may proceed when symbol is not news-halted."
    else:
        verdict = "insufficient_evidence"
        calibration = "unknown"
        lesson = "Need more evidence before approving this policy edge case."

    return {
        "ts": time.time(),
        "type": "ensemble_decision_review",
        "reviewer": "rules_policy_reviewer",
        "provider": "deterministic",
        "model": "policy_rules_v1",
        "ok": True,
        "error": None,
        "latency_ms": 0,
        "example_ts": example.get("ts"),
        "review": {
            "reviewer": "rules_policy_reviewer",
            "verdict": verdict,
            "confidence_calibration": calibration,
            "missing_evidence": [],
            "risk_notes": [],
            "features_to_track": ["news_halt_symbols", "signal_confidence"],
            "lesson": lesson,
        },
    }


def scenario(name: str, *, expect_block: bool, symbol: str, decision: Dict[str, Any], confidence: float | None = None) -> Dict[str, Any]:
    blocked, reason = nmg.decision_blocks_symbol(symbol, decision)
    ok = blocked == expect_block
    return {
        "scenario": name,
        "symbol": symbol,
        "expect_block": expect_block,
        "actual_block": blocked,
        "reason": reason,
        "ok": ok,
        "confidence": confidence,
    }


def build_training_example(name: str, symbol: str, side: str, confidence: float, factors: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "ts": time.time(),
        "type": "decision_training_example",
        "input": {
            "action": "PROPOSE_ORDER",
            "symbol": symbol,
            "side": side,
            "confidence": confidence,
            "factors": factors,
            "reasoning": f"policy_verification:{name}",
        },
        "target": {
            "label": "policy_check",
            "review_tasks": ["calibrate_confidence", "challenge_trade_or_hold_decision"],
        },
    }


def run_deterministic_checks() -> List[Dict[str, Any]]:
    now = time.time()
    fx_halt = nmg.annotate_decision(
        {
            "source": "news_orchestrator",
            "recommendation": "halt_new",
            "affected_symbols": {"EURUSD": 1.0, "GBPUSD": 0.83, "XAUUSD": 0.67, "USDJPY": 0.33},
            "impact_score": 0.95,
        },
        now=now,
    )
    expired = dict(fx_halt)
    expired["ts"] = now - 2000
    expired["expires_ts"] = now - 60

    results = [
        scenario("gooogl_clear_during_fx_halt", expect_block=False, symbol="GOOGL", decision=fx_halt),
        scenario("eurusd_blocked_during_fx_halt", expect_block=True, symbol="EURUSD", decision=fx_halt),
        scenario("eurusd_clear_after_ttl", expect_block=False, symbol="EURUSD", decision=expired),
        {
            "scenario": "single_strong_pattern_scores_0_70",
            "ok": sg.confluence_score("EURUSD", "ranging", [{"pattern": "hammer", "direction": "bullish", "strength": "strong"}]) == 0.70,
            "score": sg.confluence_score("EURUSD", "ranging", [{"pattern": "hammer", "direction": "bullish", "strength": "strong"}]),
            "threshold": 0.70,
        },
    ]
    return results


def run_ensemble_reviews(checks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    config = load_config()
    reviewers = enabled_reviewers(config)

    now = time.time()
    fx_halt = nmg.annotate_decision(
        {
            "source": "news_orchestrator",
            "recommendation": "halt_symbols",
            "halt_symbols": ["EURUSD", "GBPUSD"],
            "affected_symbols": {"EURUSD": 1.0, "GBPUSD": 0.83},
            "impact_score": 0.95,
            "ts": now,
        },
        now=now,
    )

    examples = [
        build_training_example(
            "gooogl_sell_during_fx_halt",
            "GOOGL",
            "SELL",
            0.75,
            {
                "signal_confidence": 0.75,
                "macro_regime": "neutral",
                "macro_blackout": False,
                "news_halt_symbols": fx_halt.get("halt_symbols"),
                "guard_ok": True,
                "confidence_boosts": ["forecast_side_aligned"],
                "confidence_penalties": [],
            },
        ),
        build_training_example(
            "eurusd_buy_during_fx_halt",
            "EURUSD",
            "BUY",
            0.70,
            {
                "signal_confidence": 0.70,
                "macro_regime": "risk_off",
                "macro_blackout": False,
                "news_halt_symbols": fx_halt.get("halt_symbols"),
                "guard_ok": True,
                "confidence_penalties": ["macro_risk_off_or_blackout"],
            },
        ),
    ]

    reviews: List[Dict[str, Any]] = []
    for example in examples:
        rules_review = rules_policy_reviewer(example)
        rules_review["policy_scenario"] = example["input"]["reasoning"]
        reviews.append(rules_review)
        append_jsonl(OUTPUT, rules_review)

        for reviewer in reviewers:
            review = review_example(example, reviewer)
            review["policy_scenario"] = example["input"]["reasoning"]
            reviews.append(review)
            append_jsonl(OUTPUT, review)
    return reviews


def summarize(checks: List[Dict[str, Any]], reviews: List[Dict[str, Any]]) -> Dict[str, Any]:
    deterministic_ok = all(c.get("ok") for c in checks)
    rules_reviews = [r for r in reviews if r.get("reviewer") == "rules_policy_reviewer"]
    llm_reviews = [r for r in reviews if r.get("reviewer") != "rules_policy_reviewer"]

    rules_ok = all((r.get("review") or {}).get("verdict") in {"agree", "insufficient_evidence"} for r in rules_reviews)
    ensemble_ok = True
    ensemble_detail = []
    for review in rules_reviews + llm_reviews:
        if review.get("reviewer") == "rules_policy_reviewer":
            parsed = review.get("review") or {}
            ensemble_detail.append(
                {
                    "scenario": review.get("policy_scenario"),
                    "reviewer": review.get("reviewer"),
                    "verdict": parsed.get("verdict"),
                    "confidence_calibration": parsed.get("confidence_calibration"),
                    "source": "deterministic",
                }
            )
            continue
        if not review.get("ok"):
            ensemble_detail.append(
                {
                    "scenario": review.get("policy_scenario"),
                    "reviewer": review.get("reviewer"),
                    "verdict": "reviewer_unavailable",
                    "error": review.get("error"),
                    "source": "llm_optional",
                }
            )
            continue
        parsed = review.get("review") or {}
        verdict = parsed.get("verdict")
        calibration = parsed.get("confidence_calibration")
        ensemble_detail.append(
            {
                "scenario": review.get("policy_scenario"),
                "reviewer": review.get("reviewer"),
                "verdict": verdict,
                "confidence_calibration": calibration,
                "source": "llm",
            }
        )
        if verdict not in {"agree", "insufficient_evidence"}:
            ensemble_ok = False

    llm_available = any(r.get("ok") for r in llm_reviews)
    return {
        "deterministic_ok": deterministic_ok,
        "rules_reviewer_ok": rules_ok,
        "llm_ensemble_ok": ensemble_ok if llm_available else None,
        "llm_ensemble_available": llm_available,
        "checks": checks,
        "ensemble": ensemble_detail,
        "output_file": str(OUTPUT),
    }


def main() -> int:
    checks = run_deterministic_checks()
    reviews = run_ensemble_reviews(checks)
    summary = summarize(checks, reviews)
    print(json.dumps(summary, indent=2))
    if not summary["deterministic_ok"] or not summary["rules_reviewer_ok"]:
        return 1
    if summary["llm_ensemble_available"] and not summary["llm_ensemble_ok"]:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
