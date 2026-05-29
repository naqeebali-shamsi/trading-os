#!/usr/bin/env python3
"""Provider-agnostic LLM adapter.

Supports remote OpenAI-compatible APIs and local/edge OpenAI-compatible servers
(Ollama/vLLM/etc.) behind one safe interface. The adapter never executes model
instructions. It returns parsed JSON plus metadata/errors for guard layers.
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from cortex.llm_status import classify_llm_error, should_abort_model_fallback, should_retry_http

ROOT = Path(__file__).resolve().parent.parent
CONFIG_FILE = ROOT / "config" / "llm.yaml"
SECRETS_FILE = ROOT / "config" / "secrets.yaml"
RISK_FILE = ROOT / "immune" / "risk_limits.json"


def live_mode_active() -> bool:
    if os.getenv("TRADING_OS_MODE", "").strip().upper() == "LIVE":
        return True
    try:
        data = json.loads(RISK_FILE.read_text())
        return str(data.get("mode", "")).upper() == "LIVE"
    except Exception:
        return False


@dataclass
class LLMResult:
    ok: bool
    provider: str
    model: str
    content: str = ""
    parsed: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    latency_ms: int = 0
    raw_meta: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        error_code = None
        if self.raw_meta:
            error_code = self.raw_meta.get("error_code")
        if not error_code:
            error_code = "OK" if self.ok else classify_llm_error(self.error)
        return {
            "ok": self.ok,
            "provider": self.provider,
            "model": self.model,
            "content": self.content,
            "parsed": self.parsed,
            "error": self.error,
            "error_code": error_code,
            "latency_ms": self.latency_ms,
            "raw_meta": self.raw_meta,
        }


class LLMClient:
    def __init__(self, config_path: Path = CONFIG_FILE):
        self.config_path = Path(config_path)
        self.config = self._load_config()

    def _load_config(self) -> Dict[str, Any]:
        if not self.config_path.exists():
            return {"default_provider": None, "providers": {}}
        with self.config_path.open("r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}

    def provider_config(self, provider: Optional[str] = None) -> tuple[str, Dict[str, Any]]:
        providers = self.config.get("providers", {}) or {}
        name = provider or os.getenv("TRADING_OS_LLM_PROVIDER") or self.config.get("default_provider")
        if name not in providers:
            return str(name or "missing_provider"), {"type": "missing_provider", "model": "unknown", "error": f"unknown_provider:{name or 'unset'}"}
        return name, providers[name]

    @staticmethod
    def _read_secret_key(path: str) -> Optional[str]:
        if not SECRETS_FILE.exists() or not path:
            return None
        try:
            data = yaml.safe_load(SECRETS_FILE.read_text()) or {}
        except Exception:
            return None
        cur: Any = data
        for part in path.split("."):
            if isinstance(cur, dict) and part in cur:
                cur = cur[part]
            else:
                return None
        if cur:
            return str(cur).strip()
        return None

    def _api_key(self, cfg: Dict[str, Any]) -> Optional[str]:
        env_name = cfg.get("api_key_env")
        if env_name and os.getenv(env_name):
            return os.getenv(env_name)
        secret_path = cfg.get("api_key_secret_path")
        if secret_path:
            return self._read_secret_key(secret_path)
        return None

    @staticmethod
    def extract_json(content: str) -> Dict[str, Any]:
        """Extract a JSON object from raw model content.

        Handles plain JSON, markdown code fences, and surrounding prose. Rejects
        arrays/non-objects because trading decisions must be structured objects.
        """
        if content is None:
            raise ValueError("empty_content")
        text = str(content).strip()
        if not text:
            raise ValueError("empty_content")
        fence = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL | re.IGNORECASE)
        if fence:
            text = fence.group(1).strip()
        if text.startswith("["):
            raise ValueError("json_not_object")
        if not text.startswith("{"):
            start = text.find("{")
            end = text.rfind("}")
            if start >= 0 and end > start:
                text = text[start:end + 1]
        parsed = json.loads(text)
        if not isinstance(parsed, dict):
            raise ValueError("json_not_object")
        return parsed

    @staticmethod
    def _read_http_error_body(exc: urllib.error.HTTPError) -> str:
        try:
            return exc.read().decode("utf-8", errors="replace").strip()[:500]
        except Exception:
            return ""

    @staticmethod
    def _failure_result(
        provider_name: str,
        model_name: str,
        *,
        error: str,
        http_code: Optional[int] = None,
        http_body: str = "",
        latency_ms: int = 0,
        raw_meta: Optional[Dict[str, Any]] = None,
    ) -> LLMResult:
        meta = dict(raw_meta or {})
        meta["error_code"] = classify_llm_error(error, http_code)
        if http_body:
            meta["http_body"] = http_body
        if http_code is not None:
            meta["http_code"] = http_code
        return LLMResult(
            False,
            provider_name,
            model_name,
            error=error,
            latency_ms=latency_ms,
            raw_meta=meta,
        )

    @staticmethod
    def _messages(system: str, user: str) -> List[Dict[str, str]]:
        return [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]

    def complete_json(
        self,
        prompt: str,
        *,
        system: str = "Return valid JSON only.",
        provider: Optional[str] = None,
        model: Optional[str] = None,
        max_tokens: int = 512,
        temperature: float = 0.2,
        timeout: Optional[float] = None,
    ) -> LLMResult:
        provider_name, cfg = self.provider_config(provider)
        provider_type = cfg.get("type", "mock")
        model_name = model or os.getenv("TRADING_OS_LLM_MODEL") or cfg.get("model", "unknown")
        started = time.time()

        if os.getenv("TRADING_OS_LLM_DISABLED", "0").strip().lower() in {"1", "true", "yes"}:
            return LLMResult(False, provider_name, model_name, error="llm_disabled")

        if provider_type == "missing_provider":
            return LLMResult(False, provider_name, model_name, error=cfg.get("error", "missing_provider"))

        if provider_type == "mock":
            if live_mode_active() and os.getenv("TRADING_OS_ALLOW_MOCK_LLM", "0").strip().lower() not in {"1", "true", "yes"}:
                return LLMResult(False, provider_name, model_name, error="mock_llm_forbidden_in_live")
            content = cfg.get("response") or '{"action":"HOLD","reasoning":"mock provider","confidence":0.0}'
            try:
                parsed = self.extract_json(content)
                return LLMResult(True, provider_name, model_name, content, parsed, latency_ms=int((time.time() - started) * 1000))
            except Exception as exc:
                return LLMResult(False, provider_name, model_name, content, error=f"parse_error:{exc}", latency_ms=int((time.time() - started) * 1000))

        if provider_type != "openai_compatible":
            return LLMResult(False, provider_name, model_name, error=f"unsupported_provider_type:{provider_type}")

        endpoint = cfg.get("endpoint")
        if not endpoint:
            return LLMResult(False, provider_name, model_name, error="missing_endpoint")
        key = self._api_key(cfg)
        if not key and not cfg.get("api_key_optional", False):
            return LLMResult(False, provider_name, model_name, error="missing_api_key")

        headers = {"Content-Type": "application/json"}
        if key:
            headers["Authorization"] = f"Bearer {key}"
        headers.update(cfg.get("headers", {}) or {})

        fallback_models = [] if model or os.getenv("TRADING_OS_LLM_MODEL") else list(cfg.get("fallback_models") or [])
        models_to_try = [model_name] + [m for m in fallback_models if m and m != model_name]
        last_error = None
        last_http_code: Optional[int] = None
        last_http_body = ""
        for attempt_model in models_to_try:
            payload_dict: Dict[str, Any] = {
                "model": attempt_model,
                "messages": self._messages(system, prompt),
                "temperature": temperature,
                "max_tokens": max_tokens,
            }
            reasoning = cfg.get("reasoning")
            if isinstance(reasoning, dict) and reasoning:
                payload_dict["reasoning"] = reasoning
            extra_payload = cfg.get("payload") or cfg.get("request_payload") or {}
            if isinstance(extra_payload, dict):
                payload_dict.update(extra_payload)
            payload = json.dumps(payload_dict).encode("utf-8")
            req = urllib.request.Request(endpoint, data=payload, headers=headers, method="POST")
            try:
                with urllib.request.urlopen(req, timeout=timeout or self.config.get("request_timeout_sec", 30)) as resp:
                    raw = resp.read().decode("utf-8", errors="replace")
                if len(raw) > int(self.config.get("max_response_chars", 12000)):
                    last_error = "response_too_large"
                    continue
                data = json.loads(raw)
                content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
                parsed = self.extract_json(content)
                return LLMResult(
                    True,
                    provider_name,
                    attempt_model,
                    content,
                    parsed,
                    latency_ms=int((time.time() - started) * 1000),
                    raw_meta={
                        "response_id": data.get("id"),
                        "fallback_from": model_name if attempt_model != model_name else None,
                        "error_code": "OK",
                    },
                )
            except urllib.error.HTTPError as exc:
                last_http_code = int(exc.code)
                last_http_body = self._read_http_error_body(exc)
                last_error = f"http_error:{last_http_code}"
                if should_abort_model_fallback(last_http_code):
                    break
                if not should_retry_http(last_http_code):
                    continue
            except urllib.error.URLError as exc:
                last_error = f"url_error:{exc.reason}"
            except TimeoutError:
                last_error = "timeout"
            except (json.JSONDecodeError, KeyError, ValueError) as exc:
                last_error = f"parse_error:{exc}"
            except Exception as exc:
                last_error = f"error:{type(exc).__name__}:{exc}"
        return self._failure_result(
            provider_name,
            model_name,
            error=last_error or "all_models_failed",
            http_code=last_http_code,
            http_body=last_http_body,
            latency_ms=int((time.time() - started) * 1000),
            raw_meta={"attempted_models": models_to_try},
        )


_DEFAULT: Optional[LLMClient] = None


def get_client(force: bool = False) -> LLMClient:
    global _DEFAULT
    if force or _DEFAULT is None:
        _DEFAULT = LLMClient()
    return _DEFAULT


def complete_json(prompt: str, **kwargs) -> LLMResult:
    return get_client().complete_json(prompt, **kwargs)
