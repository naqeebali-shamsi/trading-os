#!/usr/bin/env python3
"""QA tests for provider-agnostic LLM adapter."""
import json
import os
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from cortex.llm_client import LLMClient  # noqa: E402


def write_config(text: str) -> Path:
    tmp = tempfile.NamedTemporaryFile("w", delete=False, suffix=".yaml")
    tmp.write(text)
    tmp.close()
    return Path(tmp.name)


class FakeResp:
    def __init__(self, payload):
        self.payload = payload
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False
    def read(self):
        return self.payload.encode()


def test_extract_json_variants():
    c = LLMClient(write_config("default_provider: mock\nproviders:\n  mock:\n    type: mock\n    model: m\n"))
    assert c.extract_json('{"action":"HOLD"}')["action"] == "HOLD"
    assert c.extract_json('```json\n{"action":"HOLD"}\n```')["action"] == "HOLD"
    assert c.extract_json('Here: {"action":"HOLD","confidence":0}')['action'] == "HOLD"
    try:
        c.extract_json('[{"action":"HOLD"}]')
        assert False, "array should be rejected"
    except ValueError as exc:
        assert "json_not_object" in str(exc)
    print("[test] PASS: JSON extraction variants")


def test_mock_provider_success_and_bad_json():
    old_allow = os.environ.get("TRADING_OS_ALLOW_MOCK_LLM")
    os.environ["TRADING_OS_ALLOW_MOCK_LLM"] = "1"
    good = LLMClient(write_config('''
default_provider: mock
providers:
  mock:
    type: mock
    model: m
    response: '{"action":"HOLD","confidence":0}'
'''))
    assert good.complete_json("x").ok
    bad = LLMClient(write_config('''
default_provider: mock
providers:
  mock:
    type: mock
    model: m
    response: 'not json'
'''))
    result = bad.complete_json("x")
    assert not result.ok and result.error.startswith("parse_error")
    if old_allow is None:
        os.environ.pop("TRADING_OS_ALLOW_MOCK_LLM", None)
    else:
        os.environ["TRADING_OS_ALLOW_MOCK_LLM"] = old_allow
    print("[test] PASS: mock provider success/bad JSON")


def test_live_mode_forbids_mock_and_unknown_provider():
    old_allow = os.environ.pop("TRADING_OS_ALLOW_MOCK_LLM", None)
    try:
        mock_result = LLMClient(write_config('''
default_provider: mock
providers:
  mock:
    type: mock
    model: m
''')).complete_json("x")
        assert not mock_result.ok and mock_result.error == "mock_llm_forbidden_in_live"

        unknown_result = LLMClient(write_config('''
default_provider: typo_provider
providers:
  openrouter:
    type: openai_compatible
    endpoint: https://example.invalid/chat
    api_key_optional: true
    model: m
''')).complete_json("x")
        assert not unknown_result.ok and unknown_result.error == "unknown_provider:typo_provider"
    finally:
        if old_allow is not None:
            os.environ["TRADING_OS_ALLOW_MOCK_LLM"] = old_allow
    print("[test] PASS: live mode forbids mock and unknown provider fallback")


def test_openai_compatible_request_and_markdown_response():
    cfg = write_config('''
default_provider: local
request_timeout_sec: 1
providers:
  local:
    type: openai_compatible
    endpoint: http://127.0.0.1:9999/v1/chat/completions
    api_key_optional: true
    model: local-test
''')
    original = urllib.request.urlopen
    captured = {}
    def fake_urlopen(req, timeout=None):
        captured["timeout"] = timeout
        captured["body"] = json.loads(req.data.decode())
        payload = json.dumps({"id":"abc","choices":[{"message":{"content":"```json\n{\"action\":\"HOLD\",\"confidence\":0}\n```"}}]})
        return FakeResp(payload)
    urllib.request.urlopen = fake_urlopen
    try:
        result = LLMClient(cfg).complete_json("prompt")
    finally:
        urllib.request.urlopen = original
    assert result.ok, result.as_dict()
    assert result.parsed["action"] == "HOLD"
    assert captured["body"]["messages"][0]["role"] == "system"
    print("[test] PASS: OpenAI-compatible request and markdown response")


def test_missing_key_and_provider_errors():
    missing = LLMClient(write_config('''
default_provider: remote
providers:
  remote:
    type: openai_compatible
    endpoint: https://example.invalid/chat
    api_key_env: DEFINITELY_MISSING_LLM_KEY
    model: m
''')).complete_json("x")
    assert not missing.ok and missing.error == "missing_api_key"

    cfg = write_config('''
default_provider: local
providers:
  local:
    type: openai_compatible
    endpoint: http://127.0.0.1:9999/v1/chat/completions
    api_key_optional: true
    model: local-test
''')
    original = urllib.request.urlopen
    def fake_urlopen(req, timeout=None):
        raise urllib.error.URLError("connection refused")
    urllib.request.urlopen = fake_urlopen
    try:
        result = LLMClient(cfg).complete_json("x")
    finally:
        urllib.request.urlopen = original
    assert not result.ok and result.error.startswith("url_error")
    print("[test] PASS: missing key and provider error handling")


def test_http_402_aborts_model_fallback():
    cfg = write_config('''
default_provider: remote
providers:
  remote:
    type: openai_compatible
    endpoint: http://127.0.0.1:9999/v1/chat/completions
    api_key_optional: true
    model: primary-model
    fallback_models:
      - fallback-model
''')
    original = urllib.request.urlopen
    calls = {"count": 0}

    def fake_urlopen(req, timeout=None):
        calls["count"] += 1
        raise urllib.error.HTTPError(req.full_url, 402, "Payment Required", hdrs=None, fp=None)

    urllib.request.urlopen = fake_urlopen
    try:
        result = LLMClient(cfg).complete_json("x")
    finally:
        urllib.request.urlopen = original
    assert not result.ok
    assert result.error == "http_error:402"
    assert result.as_dict()["error_code"] == "HTTP_402_PAYMENT_REQUIRED"
    assert calls["count"] == 1
    print("[test] PASS: HTTP 402 aborts model fallback")


def test_all():
    print("=" * 60)
    print("  LLM ADAPTER QA TESTS")
    print("=" * 60)
    test_extract_json_variants()
    test_mock_provider_success_and_bad_json()
    test_live_mode_forbids_mock_and_unknown_provider()
    test_openai_compatible_request_and_markdown_response()
    test_missing_key_and_provider_errors()
    test_http_402_aborts_model_fallback()
    print("=" * 60)
    print("  ALL LLM ADAPTER QA TESTS PASSED")
    print("=" * 60)


if __name__ == "__main__":
    test_all()
