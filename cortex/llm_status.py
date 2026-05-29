#!/usr/bin/env python3
"""Structured LLM status codes and operator-facing health summaries."""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
HEALTH_FILE = ROOT / "kernel" / "health.json"

HTTP_STATUS_CODES: Dict[int, str] = {
    401: "HTTP_401_UNAUTHORIZED",
    402: "HTTP_402_PAYMENT_REQUIRED",
    403: "HTTP_403_FORBIDDEN",
    404: "HTTP_404_NOT_FOUND",
    408: "HTTP_408_TIMEOUT",
    429: "HTTP_429_RATE_LIMIT",
    500: "HTTP_500_SERVER_ERROR",
    502: "HTTP_502_BAD_GATEWAY",
    503: "HTTP_503_UNAVAILABLE",
    504: "HTTP_504_GATEWAY_TIMEOUT",
}

OPERATOR_MESSAGES: Dict[str, str] = {
    "OK": "LLM reachable",
    "HTTP_402_PAYMENT_REQUIRED": "OpenRouter credits required — add billing at openrouter.ai/settings/credits",
    "HTTP_401_UNAUTHORIZED": "LLM API key rejected — check OPENROUTER_API_KEY or config/secrets.yaml",
    "HTTP_429_RATE_LIMIT": "LLM rate limited — calls will retry on transient errors",
    "HTTP_403_FORBIDDEN": "LLM access forbidden — check provider account permissions",
    "MISSING_API_KEY": "LLM API key missing — set OPENROUTER_API_KEY or config/secrets.yaml",
    "MISSING_ENDPOINT": "LLM provider endpoint not configured",
    "MOCK_LLM_FORBIDDEN_IN_LIVE": "Mock LLM blocked in LIVE mode",
    "TIMEOUT": "LLM request timed out",
    "ALL_MODELS_FAILED": "All configured LLM models failed",
    "UNKNOWN_PROVIDER": "Unknown LLM provider configured",
    "LLM_UNAVAILABLE": "LLM unavailable",
    "LLM_DISABLED": "LLM disabled — observe-only install (TRADING_OS_LLM_DISABLED=1)",
}


def classify_llm_error(error: Optional[str], http_code: Optional[int] = None) -> str:
    """Map adapter errors to stable operator codes."""
    if http_code is not None:
        return HTTP_STATUS_CODES.get(int(http_code), f"HTTP_{int(http_code)}")
    text = str(error or "").strip()
    if not text:
        return "LLM_UNAVAILABLE"
    if text == "missing_api_key":
        return "MISSING_API_KEY"
    if text == "llm_disabled":
        return "LLM_DISABLED"
    if text == "missing_endpoint":
        return "MISSING_ENDPOINT"
    if text == "mock_llm_forbidden_in_live":
        return "MOCK_LLM_FORBIDDEN_IN_LIVE"
    if text == "timeout":
        return "TIMEOUT"
    if text == "all_models_failed":
        return "ALL_MODELS_FAILED"
    if text.startswith("unknown_provider:"):
        return "UNKNOWN_PROVIDER"
    if text.startswith("http_error:"):
        try:
            code = int(text.split(":", 1)[1])
        except (IndexError, ValueError):
            return "LLM_UNAVAILABLE"
        return classify_llm_error(None, code)
    if text.startswith("url_error:"):
        return "URL_ERROR"
    if text.startswith("parse_error:"):
        return "PARSE_ERROR"
    return "LLM_UNAVAILABLE"


def operator_message(error_code: str) -> str:
    return OPERATOR_MESSAGES.get(error_code, OPERATOR_MESSAGES["LLM_UNAVAILABLE"])


def should_retry_http(http_code: int) -> bool:
    """Retry only transient provider failures — never billing/auth errors."""
    return int(http_code) in {408, 429, 500, 502, 503, 504}


def should_abort_model_fallback(http_code: int) -> bool:
    """Account/config failures won't be fixed by switching models."""
    return int(http_code) in {401, 402, 403}


def build_llm_status_payload(
    *,
    ok: bool,
    provider: str,
    model: str,
    layer: str = "agent_brain",
    error: Optional[str] = None,
    http_code: Optional[int] = None,
    error_code: Optional[str] = None,
    latency_ms: int = 0,
    correlation_id: str = "",
    trigger: Optional[str] = None,
    http_body: Optional[str] = None,
) -> Dict[str, Any]:
    code = error_code or ("OK" if ok else classify_llm_error(error, http_code))
    payload: Dict[str, Any] = {
        "layer": layer,
        "ok": bool(ok),
        "error_code": code,
        "error": error,
        "operator_message": operator_message(code),
        "provider": provider,
        "model": model,
        "latency_ms": latency_ms,
        "correlation_id": correlation_id,
        "evaluated_ts": time.time(),
    }
    if trigger:
        payload["trigger"] = trigger
    if http_body:
        payload["http_body"] = http_body[:500]
    return payload


def publish_llm_status(payload: Dict[str, Any]) -> None:
    try:
        import sys

        nervous = str(ROOT / "nervous")
        if nervous not in sys.path:
            sys.path.insert(0, nervous)
        from bus import publish  # noqa: WPS433

        publish("cortex.llm.status", payload)
    except Exception:
        return


def summarize_llm_from_brain(brain: Dict[str, Any]) -> Dict[str, Any]:
    """Flatten latest cortex.brain.result payload for dashboards."""
    llm = brain.get("llm") or {}
    ok = bool(brain.get("ok")) and bool(llm.get("ok", brain.get("ok")))
    error = llm.get("error") or brain.get("error")
    raw_meta = llm.get("raw_meta") or {}
    error_code = raw_meta.get("error_code") or ("OK" if ok else classify_llm_error(error))
    return {
        "available": bool(brain),
        "brain_ok": bool(brain.get("ok")),
        "llm_ok": bool(llm.get("ok")) if llm else None,
        "ok": ok,
        "error": error,
        "error_code": error_code,
        "operator_message": operator_message(error_code if not ok else "OK"),
        "provider": llm.get("provider"),
        "model": llm.get("model"),
        "latency_ms": llm.get("latency_ms"),
        "blocked_by_hook": brain.get("blocked_by_hook"),
        "trigger": brain.get("trigger"),
    }


def summarize_llm_status_event(status: Dict[str, Any]) -> Dict[str, Any]:
    error_code = str(status.get("error_code") or ("OK" if status.get("ok") else "LLM_UNAVAILABLE"))
    return {
        "available": True,
        "brain_ok": None,
        "llm_ok": bool(status.get("ok")),
        "ok": bool(status.get("ok")),
        "error": status.get("error"),
        "error_code": error_code,
        "operator_message": status.get("operator_message") or operator_message(error_code),
        "provider": status.get("provider"),
        "model": status.get("model"),
        "latency_ms": status.get("latency_ms"),
        "layer": status.get("layer"),
        "correlation_id": status.get("correlation_id"),
        "trigger": status.get("trigger"),
    }


def latest_llm_summary(events: List[dict]) -> Dict[str, Any]:
    """Prefer dedicated status events; fall back to brain.result."""
    status = None
    brain = None
    for ev in reversed(events):
        topic = ev.get("topic")
        if topic == "cortex.llm.status" and status is None:
            status = ev.get("payload") or {}
        if topic == "cortex.brain.result" and brain is None:
            brain = ev.get("payload") or {}
        if status is not None and brain is not None:
            break
    if status:
        summary = summarize_llm_status_event(status)
    elif brain:
        summary = summarize_llm_from_brain(brain)
    else:
        summary = {"available": False, "ok": None, "llm_ok": None, "error_code": None, "operator_message": "No LLM status yet"}
    if brain:
        decision = brain.get("decision") or {}
        proposal = decision.get("proposal") or brain.get("proposal") or {}
        macro = decision.get("macro") or {}
        guard = brain.get("guard") or {}
        summary.update(
            {
                "action": proposal.get("action"),
                "symbol": proposal.get("symbol"),
                "confidence": proposal.get("confidence"),
                "reasoning": proposal.get("reasoning"),
                "macro_regime": macro.get("risk_regime"),
                "macro_confidence": macro.get("confidence"),
                "guard_ok": guard.get("ok"),
                "guard_reason": guard.get("reason"),
            }
        )
    return summary


def merge_llm_into_health(
    *,
    ok: bool,
    provider: str,
    model: str,
    error: Optional[str] = None,
    error_code: Optional[str] = None,
    latency_ms: int = 0,
    correlation_id: str = "",
    trigger: Optional[str] = None,
    health_path: Path = HEALTH_FILE,
) -> None:
    """Merge LLM rollup into kernel/health.json without dropping preflight fields."""
    code = error_code or ("OK" if ok else classify_llm_error(error))
    rollup = {
        "last_ok": bool(ok),
        "last_error_code": code,
        "last_error": error,
        "operator_message": operator_message(code if not ok else "OK"),
        "provider": provider,
        "model": model,
        "latency_ms": latency_ms,
        "correlation_id": correlation_id,
        "last_ts": time.time(),
    }
    if trigger:
        rollup["last_trigger"] = trigger

    health: Dict[str, Any] = {}
    if health_path.exists():
        try:
            health = json.loads(health_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            health = {}
    health["llm"] = rollup
    health_path.parent.mkdir(parents=True, exist_ok=True)
    health_path.write_text(json.dumps(health, indent=2), encoding="utf-8")


def count_llm_errors_since(events: List[dict], since: float) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for ev in events:
        if ev.get("ts", 0) < since:
            continue
        if ev.get("topic") != "cortex.llm.status":
            continue
        payload = ev.get("payload") or {}
        if payload.get("ok"):
            continue
        code = str(payload.get("error_code") or classify_llm_error(payload.get("error")))
        counts[code] = counts.get(code, 0) + 1
    return counts
