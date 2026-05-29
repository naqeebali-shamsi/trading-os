#!/usr/bin/env python3
"""Tests for structured LLM status helpers."""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from cortex.llm_status import (  # noqa: E402
    build_llm_status_payload,
    classify_llm_error,
    latest_llm_summary,
    merge_llm_into_health,
    operator_message,
    should_abort_model_fallback,
)


def test_classify_llm_error():
    assert classify_llm_error("http_error:402") == "HTTP_402_PAYMENT_REQUIRED"
    assert classify_llm_error("missing_api_key") == "MISSING_API_KEY"
    assert classify_llm_error(None, 429) == "HTTP_429_RATE_LIMIT"
    assert "credits" in operator_message("HTTP_402_PAYMENT_REQUIRED").lower()


def test_should_abort_model_fallback():
    assert should_abort_model_fallback(402)
    assert should_abort_model_fallback(401)
    assert not should_abort_model_fallback(429)


def test_build_llm_status_payload():
    payload = build_llm_status_payload(
        ok=False,
        provider="openrouter",
        model="google/gemini-2.5-flash",
        error="http_error:402",
        http_code=402,
        correlation_id="test-1",
        trigger="volatility_spike",
    )
    assert payload["error_code"] == "HTTP_402_PAYMENT_REQUIRED"
    assert payload["layer"] == "agent_brain"
    assert payload["operator_message"]


def test_latest_llm_summary_prefers_status_event():
    events = [
        {
            "topic": "cortex.brain.result",
            "payload": {
                "ok": False,
                "llm": {"ok": False, "error": "http_error:402", "raw_meta": {"error_code": "HTTP_402_PAYMENT_REQUIRED"}},
                "decision": {"proposal": {"action": "HOLD", "reasoning": "old"}},
            },
        },
        {
            "topic": "cortex.llm.status",
            "payload": {
                "ok": True,
                "error_code": "OK",
                "operator_message": "LLM reachable",
                "provider": "openrouter",
                "model": "google/gemini-2.5-flash",
            },
        },
    ]
    summary = latest_llm_summary(events)
    assert summary["llm_ok"] is True
    assert summary["error_code"] == "OK"
    assert summary["action"] == "HOLD"


def test_merge_llm_into_health_preserves_preflight():
    with tempfile.TemporaryDirectory() as tmp:
        health_path = Path(tmp) / "health.json"
        health_path.write_text(json.dumps({"preflight_ok": True, "trading_mode": "LIVE"}), encoding="utf-8")
        merge_llm_into_health(
            ok=True,
            provider="openrouter",
            model="google/gemini-2.5-flash",
            error_code="OK",
            health_path=health_path,
        )
        data = json.loads(health_path.read_text(encoding="utf-8"))
        assert data["preflight_ok"] is True
        assert data["llm"]["last_ok"] is True
        assert data["llm"]["last_error_code"] == "OK"


def test_all():
    test_classify_llm_error()
    test_should_abort_model_fallback()
    test_build_llm_status_payload()
    test_latest_llm_summary_prefers_status_event()
    test_merge_llm_into_health_preserves_preflight()
    print("ALL LLM STATUS TESTS PASSED")


if __name__ == "__main__":
    test_all()
