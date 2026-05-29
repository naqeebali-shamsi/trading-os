#!/usr/bin/env python3
"""Audit whether demo/live runtime is using real integrations, not mocks.

This is intentionally read-only. It does not place trades and does not print
secret values. In strict mode it exits non-zero if live/demo configuration would
silently use mock intelligence.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

RISK_FILE = ROOT / "immune" / "risk_limits.json"
LLM_FILE = ROOT / "config" / "llm.yaml"
SECRETS_FILE = ROOT / "config" / "secrets.yaml"


def _load_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return yaml.safe_load(path.read_text()) or {}
    except Exception:
        return {}


def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text()) or {}
    except Exception:
        return {}


def _secret_present(path_expr: str) -> bool:
    data = _load_yaml(SECRETS_FILE)
    cur = data
    for part in (path_expr or "").split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return False
    return bool(str(cur or "").strip())


def audit() -> dict:
    risk = _load_json(RISK_FILE)
    llm = _load_yaml(LLM_FILE)
    providers = llm.get("providers", {}) or {}
    selected_provider = os.getenv("TRADING_OS_LLM_PROVIDER") or llm.get("default_provider")
    selected_cfg = providers.get(selected_provider, {}) if selected_provider else {}
    provider_type = selected_cfg.get("type")
    api_key_env = selected_cfg.get("api_key_env")
    api_key_secret_path = selected_cfg.get("api_key_secret_path")
    api_key_optional = bool(selected_cfg.get("api_key_optional", False))
    key_configured = bool(api_key_optional or (api_key_env and os.getenv(api_key_env)) or (api_key_secret_path and _secret_present(api_key_secret_path)))

    mode = (os.getenv("TRADING_OS_MODE") or risk.get("mode") or "SIMULATION").upper()
    allow_mock_forecasts = os.getenv("TRADING_OS_ALLOW_MOCK_FORECASTS", "0").strip().lower() in {"1", "true", "yes"}
    timesfm_provider = os.getenv("TRADING_OS_TIMESFM_PROVIDER", "").strip()
    timesfm_provider_norm = timesfm_provider.lower()
    timesfm_package_available = importlib.util.find_spec("timesfm") is not None

    checks = []
    checks.append({
        "name": "runtime_mode",
        "ok": mode == "LIVE",
        "detail": mode,
        "severity": "warn" if mode != "LIVE" else "info",
    })
    checks.append({
        "name": "llm_provider_known",
        "ok": bool(selected_provider and selected_provider in providers),
        "detail": {"provider": selected_provider, "known_providers": sorted(providers.keys())},
        "severity": "critical",
    })
    checks.append({
        "name": "llm_provider_not_mock",
        "ok": provider_type != "mock" and selected_provider not in {None, "", "mock"},
        "detail": {"provider": selected_provider, "type": provider_type},
        "severity": "critical",
    })
    checks.append({
        "name": "llm_key_or_local_configured",
        "ok": key_configured,
        "detail": {"provider": selected_provider, "api_key_env_set": bool(api_key_env and os.getenv(api_key_env)), "secret_path_set": bool(api_key_secret_path and _secret_present(api_key_secret_path)), "api_key_optional": api_key_optional},
        "severity": "critical",
    })
    checks.append({
        "name": "timesfm_not_mock_in_live",
        "ok": timesfm_provider_norm != "mock" and (bool(timesfm_provider) or not allow_mock_forecasts),
        "detail": {"provider": timesfm_provider or None, "mock_forecasts_allowed": allow_mock_forecasts, "behavior": "no forecast published unless real adapter configured" if not timesfm_provider and not allow_mock_forecasts else "configured_or_mock_allowed"},
        "severity": "critical" if allow_mock_forecasts else "warn",
    })
    checks.append({
        "name": "timesfm_real_package_available_when_enabled",
        "ok": timesfm_provider_norm not in {"timesfm", "real", "local"} or timesfm_package_available,
        "detail": {"provider": timesfm_provider or None, "timesfm_package_available": timesfm_package_available},
        "severity": "critical" if timesfm_provider_norm in {"timesfm", "real", "local"} else "info",
    })
    return {
        "ok": all(c["ok"] or c.get("severity") == "warn" for c in checks),
        "mode": mode,
        "checks": checks,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description="Audit real integrations for demo/live use")
    parser.add_argument("--strict", action="store_true", help="Exit non-zero on critical mock/missing-real-provider checks")
    args = parser.parse_args(argv)
    report = audit()
    print(json.dumps(report, indent=2, sort_keys=True))
    if args.strict:
        critical_failures = [c for c in report["checks"] if not c["ok"] and c.get("severity") == "critical"]
        return 1 if critical_failures else 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
