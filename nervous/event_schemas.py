#!/usr/bin/env python3
"""Minimal event schemas for critical Trading OS topics.

Known critical topics are validated at publish-time. Unknown topics remain
permissive for backwards compatibility while the OS evolves.
"""
from __future__ import annotations

from typing import Any, Callable


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _has_number(payload: dict, key: str) -> bool:
    return _is_number(payload.get(key))


def _has_string(payload: dict, key: str) -> bool:
    return isinstance(payload.get(key), str) and bool(payload.get(key).strip())


def _validate_tick(payload: dict) -> list[str]:
    errors: list[str] = []
    for key in ("symbol",):
        if not _has_string(payload, key):
            errors.append(f"{key}:required_string")
    for key in ("bid", "ask"):
        if not _has_number(payload, key):
            errors.append(f"{key}:required_number")
    if _has_number(payload, "bid") and _has_number(payload, "ask") and payload["ask"] < payload["bid"]:
        errors.append("ask_lt_bid")
    return errors


def _validate_candle(payload: dict) -> list[str]:
    errors: list[str] = []
    for key in ("symbol", "timeframe"):
        if not _has_string(payload, key):
            errors.append(f"{key}:required_string")
    for key in ("open_price", "high", "low", "close", "ts_close"):
        if not _has_number(payload, key):
            errors.append(f"{key}:required_number")
    if all(_has_number(payload, key) for key in ("high", "low")) and payload["high"] < payload["low"]:
        errors.append("high_lt_low")
    return errors


def _validate_signal_eval(payload: dict) -> list[str]:
    errors: list[str] = []
    for key in ("symbol", "timeframe", "status", "reason", "stage"):
        if not _has_string(payload, key):
            errors.append(f"{key}:required_string")
    if _has_string(payload, "status") and payload["status"] not in {"skipped", "blocked", "passed"}:
        errors.append("status:invalid")
    if "ts_close" in payload and not _has_number(payload, "ts_close"):
        errors.append("ts_close:required_number")
    return errors


def _validate_signal(payload: dict) -> list[str]:
    errors: list[str] = []
    for key in ("symbol", "side", "strategy_id"):
        if not _has_string(payload, key):
            errors.append(f"{key}:required_string")
    for key in ("qty", "confidence"):
        if not _has_number(payload, key):
            errors.append(f"{key}:required_number")
    return errors


def _validate_order_intent(payload: dict) -> list[str]:
    errors: list[str] = []
    for key in ("order_id", "symbol", "side"):
        if not _has_string(payload, key):
            errors.append(f"{key}:required_string")
    if not _has_number(payload, "qty"):
        errors.append("qty:required_number")
    return errors


def _validate_immune_pass(payload: dict) -> list[str]:
    errors: list[str] = []
    if not _has_string(payload, "type"):
        errors.append("type:required_string")
    intent = payload.get("intent")
    if not isinstance(intent, dict):
        errors.append("intent:required_object")
    elif not _has_string(intent, "order_id"):
        errors.append("intent.order_id:required_string")
    return errors


def _validate_order_filled(payload: dict) -> list[str]:
    errors: list[str] = []
    if not _has_string(payload, "order_id"):
        errors.append("order_id:required_string")
    return errors


def _validate_immune_block(payload: dict) -> list[str]:
    errors: list[str] = []
    if "reason" in payload and not isinstance(payload.get("reason"), str):
        errors.append("reason:optional_string")
    return errors


def _validate_llm_status(payload: dict) -> list[str]:
    errors: list[str] = []
    for key in ("layer", "error_code"):
        if not _has_string(payload, key):
            errors.append(f"{key}:required_string")
    if "ok" in payload and not isinstance(payload.get("ok"), bool):
        errors.append("ok:required_bool")
    return errors


SCHEMAS: dict[str, Callable[[dict], list[str]]] = {
    "market.tick": _validate_tick,
    "candle.close": _validate_candle,
    "market.signal": _validate_signal,
    "market.signal.evaluation": _validate_signal_eval,
    "muscle.order.intent": _validate_order_intent,
    "immune.pass": _validate_immune_pass,
    "muscle.order.filled": _validate_order_filled,
    "immune.block": _validate_immune_block,
    "cortex.llm.status": _validate_llm_status,
}


def base_topic(topic: str) -> str:
    """Map per-symbol topics to their canonical schema topic."""
    for prefix in ("market.tick.", "candle.close."):
        if topic.startswith(prefix):
            return prefix[:-1]
    return topic


def validate_event(topic: str, payload: Any) -> tuple[bool, list[str]]:
    validator = SCHEMAS.get(base_topic(topic))
    if validator is None:
        return True, []
    if not isinstance(payload, dict):
        return False, ["payload:not_object"]
    errors = validator(payload)
    return not errors, errors
