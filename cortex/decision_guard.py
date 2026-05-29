#!/usr/bin/env python3
"""Guardrails for LLM-generated trading decisions.

LLMs are advisory by default. This module validates shape, allowed actions,
instrument safety, numeric bounds, and execution mode before any LLM output can
be translated into bus events that may eventually reach the order router.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

from cortex.instrument_registry import load_registry
from trading_profile import env_str

ROOT = Path(__file__).resolve().parent.parent
AUDIT_LOG = ROOT / "logs" / "llm_decisions.jsonl"

ALLOWED_ACTIONS = {"HOLD", "NEW_ORDER", "ADJUST_RISK", "DEPLOY_STRATEGY", "REQUEST_BACKTEST", "EMERGENCY_HALT"}
EXECUTION_MODES = {"ADVISORY", "PAPER", "LIVE"}
DEFAULT_MODE = env_str("TRADING_OS_LLM_DECISION_MODE", production="LIVE", development="ADVISORY").upper()
MIN_ORDER_CONFIDENCE = float(os.getenv("TRADING_OS_LLM_MIN_ORDER_CONFIDENCE", "0.75"))
MAX_REASONING_CHARS = 1000


@dataclass
class GuardResult:
    ok: bool
    mode: str
    decision: Dict[str, Any] = field(default_factory=dict)
    reason: str = "ok"
    details: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self):
        return {"ok": self.ok, "mode": self.mode, "reason": self.reason, "decision": self.decision, "details": self.details}


def _num(value, default=None):
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _clean_str(value, max_len=MAX_REASONING_CHARS):
    if value is None:
        return ""
    return str(value).replace("\x00", "")[:max_len]


def normalize_decision(raw: Dict[str, Any]) -> Dict[str, Any]:
    action = str(raw.get("action", "HOLD")).upper().strip()
    return {
        "action": action,
        "symbol": str(raw.get("symbol") or "").upper() or None,
        "side": str(raw.get("side") or "").upper() or None,
        "qty": _num(raw.get("qty")),
        "sl": _num(raw.get("sl")),
        "tp": _num(raw.get("tp")),
        "reasoning": _clean_str(raw.get("reasoning")),
        "confidence": _num(raw.get("confidence"), 0.0) or 0.0,
        "target_strategy_id": raw.get("target_strategy_id"),
        "strategy_id": raw.get("strategy_id") or raw.get("target_strategy_id"),
    }


def guard_decision(raw: Dict[str, Any], mode: Optional[str] = None, market_snapshot: Optional[Dict[str, Any]] = None) -> GuardResult:
    mode = (mode or DEFAULT_MODE).upper()
    if mode not in EXECUTION_MODES:
        mode = "ADVISORY"
    if not isinstance(raw, dict):
        return GuardResult(False, mode, reason="decision_not_object")

    decision = normalize_decision(raw)
    action = decision["action"]
    if action not in ALLOWED_ACTIONS:
        return GuardResult(False, mode, decision, "action_not_allowed")

    if decision["confidence"] < 0 or decision["confidence"] > 1:
        return GuardResult(False, mode, decision, "confidence_out_of_range")

    # Emergency halt is allowed to alert in all modes, but never directly kills processes here.
    if action == "EMERGENCY_HALT":
        return GuardResult(True, mode, decision, "ok_alert_only", {"alert_only": True})

    # Non-order operational decisions are advisory unless an executor explicitly handles them.
    if action in {"HOLD", "ADJUST_RISK", "DEPLOY_STRATEGY", "REQUEST_BACKTEST"}:
        return GuardResult(True, mode, decision)

    if action == "NEW_ORDER":
        if mode == "ADVISORY":
            return GuardResult(False, mode, decision, "advisory_mode_blocks_orders")
        if decision["confidence"] < MIN_ORDER_CONFIDENCE:
            return GuardResult(False, mode, decision, "confidence_below_order_threshold", {"min_confidence": MIN_ORDER_CONFIDENCE})
        if decision["side"] not in {"BUY", "SELL"}:
            return GuardResult(False, mode, decision, "invalid_side")
        if decision["sl"] is None or decision["sl"] <= 0:
            return GuardResult(False, mode, decision, "missing_stop_loss")
        registry = load_registry()
        inst = registry.validate_order({
            "symbol": decision["symbol"],
            "side": decision["side"],
            "qty": decision["qty"],
            "strategy_id": decision.get("strategy_id"),
        }, market_snapshot=market_snapshot, require_enabled=True)
        if not inst.ok:
            return GuardResult(False, mode, decision, f"instrument_{inst.reason}", inst.as_dict())
        decision["symbol"] = inst.symbol
        decision["qty"] = inst.details.get("rounded_qty", decision["qty"])
        return GuardResult(True, mode, decision, details={"broker_symbol": inst.details.get("broker_symbol")})

    return GuardResult(False, mode, decision, "unhandled_action")


def audit_decision(raw: Dict[str, Any], result: GuardResult, trigger: str = "unknown"):
    AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "ts": time.time(),
        "trigger": trigger,
        "raw": raw,
        "guard": result.as_dict(),
    }
    with AUDIT_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, sort_keys=True) + "\n")


def guarded(raw: Dict[str, Any], mode: Optional[str] = None, trigger: str = "unknown", market_snapshot: Optional[Dict[str, Any]] = None) -> GuardResult:
    result = guard_decision(raw, mode=mode, market_snapshot=market_snapshot)
    audit_decision(raw, result, trigger=trigger)
    return result
