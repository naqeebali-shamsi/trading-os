#!/usr/bin/env python3
"""Trading OS hook gateway.

This is the deterministic policy layer that gives us Claude-Agent-SDK-style
pre/post hooks without vendor lock-in. Hooks are synchronous, auditable, and
fail-closed for dangerous paths by default.
"""
from __future__ import annotations

import json
import os
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG_FILE = ROOT / "config" / "hooks.yaml"
AUDIT_FILE = ROOT / "logs" / "hooks.jsonl"
sys.path.insert(0, str(ROOT / "nervous"))
try:
    from bus import publish  # noqa: E402
except Exception:  # tests can still validate file-audit behavior
    publish = None

Permission = str
PolicyFn = Callable[["HookEvent", Dict[str, Any]], "HookResult"]


@dataclass
class HookEvent:
    hook: str
    actor: str
    payload: Dict[str, Any]
    risk_level: str = "medium"
    correlation_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    ts: float = field(default_factory=time.time)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "hook": self.hook,
            "actor": self.actor,
            "payload": self.payload,
            "risk_level": self.risk_level,
            "correlation_id": self.correlation_id,
            "ts": self.ts,
        }


@dataclass
class HookResult:
    allow: bool
    reason: str = "ok"
    payload_patch: Optional[Dict[str, Any]] = None
    policy: Optional[str] = None
    elapsed_ms: int = 0
    error: Optional[str] = None

    def as_dict(self) -> Dict[str, Any]:
        return {
            "allow": self.allow,
            "reason": self.reason,
            "payload_patch": self.payload_patch,
            "policy": self.policy,
            "elapsed_ms": self.elapsed_ms,
            "error": self.error,
        }


class HookManager:
    def __init__(self, config_path: Path = CONFIG_FILE, audit_file: Path = AUDIT_FILE):
        self.config_path = Path(config_path)
        self.audit_file = Path(audit_file)
        self.config = self._load_config()
        self.policies: Dict[str, PolicyFn] = {
            "block_if_stop_trading": self._policy_block_if_stop_trading,
            "tool_permission_gate": self._policy_tool_permission_gate,
            "block_forbidden_tools": self._policy_block_forbidden_tools,
            "require_human_approval": self._policy_require_human_approval,
            "require_order_guard": self._policy_require_order_guard,
            "require_instrument_validation": self._policy_require_instrument_validation,
            "reject_non_json_object": self._policy_reject_non_json_object,
            "require_advisory_mode_for_live": self._policy_require_advisory_mode_for_live,
            "alert_only_without_human": self._policy_alert_only_without_human,
        }

    def _load_config(self) -> Dict[str, Any]:
        if not self.config_path.exists():
            return {"defaults": {"enabled": True, "fail_closed": True, "audit": True}, "hooks": {}, "tool_permissions": {}}
        with self.config_path.open("r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}

    def hook_cfg(self, hook: str) -> Dict[str, Any]:
        defaults = self.config.get("defaults", {}) or {}
        cfg = dict(defaults)
        cfg.update((self.config.get("hooks", {}) or {}).get(hook, {}) or {})
        return cfg

    def run(self, hook: str, payload: Optional[Dict[str, Any]] = None, *, actor: str = "system", risk_level: str = "medium", correlation_id: Optional[str] = None) -> HookResult:
        event = HookEvent(hook=hook, actor=actor, payload=payload or {}, risk_level=risk_level, correlation_id=correlation_id or uuid.uuid4().hex)
        cfg = self.hook_cfg(hook)
        started = time.time()
        if not cfg.get("enabled", True):
            result = HookResult(True, reason="hook_disabled")
            self.audit(event, result)
            return result

        fail_closed = bool(cfg.get("fail_closed", True))
        for policy_name in cfg.get("policies", []) or []:
            policy = self.policies.get(policy_name)
            if not policy:
                result = HookResult(not fail_closed, reason="unknown_policy", policy=policy_name, error=f"unknown policy {policy_name}")
                result.elapsed_ms = int((time.time() - started) * 1000)
                self.audit(event, result)
                if not result.allow:
                    return result
                continue
            try:
                result = policy(event, cfg)
                result.policy = policy_name
                result.elapsed_ms = int((time.time() - started) * 1000)
            except Exception as exc:
                result = HookResult(not fail_closed, reason="policy_exception", policy=policy_name, error=str(exc), elapsed_ms=int((time.time() - started) * 1000))
            self.audit(event, result)
            if not result.allow:
                return result
        result = HookResult(True, elapsed_ms=int((time.time() - started) * 1000))
        self.audit(event, result)
        return result

    def audit(self, event: HookEvent, result: HookResult):
        if not (self.config.get("defaults", {}) or {}).get("audit", True):
            return
        self.audit_file.parent.mkdir(parents=True, exist_ok=True)
        record = {"event": event.as_dict(), "result": result.as_dict()}
        with self.audit_file.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, sort_keys=True) + "\n")
        if publish:
            topic = "ops.hook.block" if not result.allow else "ops.hook.event"
            try:
                publish(topic, record)
            except Exception:
                pass

    def _policy_block_if_stop_trading(self, event: HookEvent, cfg: Dict[str, Any]) -> HookResult:
        if (ROOT / "STOP_TRADING").exists():
            return HookResult(False, "stop_trading_active")
        return HookResult(True)

    def _policy_tool_permission_gate(self, event: HookEvent, cfg: Dict[str, Any]) -> HookResult:
        tool = event.payload.get("tool") or event.payload.get("tool_name")
        permissions = self.config.get("tool_permissions", {}) or {}
        permission = permissions.get(tool, "forbidden")
        if permission == "forbidden":
            return HookResult(False, "tool_forbidden", {"permission": permission})
        if permission == "dangerous" and not event.payload.get("human_approved"):
            return HookResult(False, "dangerous_tool_requires_human", {"permission": permission})
        return HookResult(True, payload_patch={"permission": permission})

    def _policy_block_forbidden_tools(self, event: HookEvent, cfg: Dict[str, Any]) -> HookResult:
        tool = event.payload.get("tool") or event.payload.get("tool_name")
        permission = (self.config.get("tool_permissions", {}) or {}).get(tool, "forbidden")
        if permission == "forbidden":
            return HookResult(False, "tool_forbidden")
        return HookResult(True)

    def _policy_require_human_approval(self, event: HookEvent, cfg: Dict[str, Any]) -> HookResult:
        if not event.payload.get("human_approved"):
            return HookResult(False, "human_approval_required")
        return HookResult(True)

    def _policy_require_order_guard(self, event: HookEvent, cfg: Dict[str, Any]) -> HookResult:
        guard = event.payload.get("guard") or {}
        if not guard.get("ok"):
            return HookResult(False, "order_guard_not_ok")
        return HookResult(True)

    def _policy_require_instrument_validation(self, event: HookEvent, cfg: Dict[str, Any]) -> HookResult:
        inst = event.payload.get("instrument") or event.payload.get("instrument_validation") or {}
        if not inst.get("ok"):
            return HookResult(False, "instrument_validation_not_ok")
        return HookResult(True)

    def _policy_reject_non_json_object(self, event: HookEvent, cfg: Dict[str, Any]) -> HookResult:
        value = event.payload.get("parsed")
        if not isinstance(value, dict):
            return HookResult(False, "llm_output_not_json_object")
        return HookResult(True)

    def _policy_require_advisory_mode_for_live(self, event: HookEvent, cfg: Dict[str, Any]) -> HookResult:
        if os.getenv("TRADING_OS_MODE", "SIMULATION").upper() == "LIVE" and os.getenv("TRADING_OS_LLM_DECISION_MODE", "ADVISORY").upper() != "ADVISORY":
            if not event.payload.get("human_approved"):
                return HookResult(False, "live_llm_non_advisory_requires_human")
        return HookResult(True)

    def _policy_alert_only_without_human(self, event: HookEvent, cfg: Dict[str, Any]) -> HookResult:
        if not event.payload.get("human_approved"):
            return HookResult(True, "alert_only", {"alert_only": True})
        return HookResult(True)


_DEFAULT: Optional[HookManager] = None


def get_hook_manager(force: bool = False) -> HookManager:
    global _DEFAULT
    if force or _DEFAULT is None:
        _DEFAULT = HookManager()
    return _DEFAULT


def run_hook(hook: str, payload: Optional[Dict[str, Any]] = None, **kwargs) -> HookResult:
    return get_hook_manager().run(hook, payload, **kwargs)
