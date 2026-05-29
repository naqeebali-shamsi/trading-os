#!/usr/bin/env python3
"""Deployment profile for Trading OS bootstrap defaults.

``TRADING_OS_PROFILE`` defaults to ``production`` for this install.
Explicit env vars always override profile defaults. Tests set profile to
``development`` via ``tests/conftest.py``.
"""
from __future__ import annotations

import os
from typing import Any, Dict, Iterable, List

_PROFILE = os.getenv("TRADING_OS_PROFILE", "production").strip().lower()
if _PROFILE in {"", "prod"}:
    _PROFILE = "production"
elif _PROFILE in {"dev", "test"}:
    _PROFILE = "development"


def profile_name() -> str:
    return _PROFILE


def is_production() -> bool:
    return _PROFILE == "production"


def is_development() -> bool:
    return _PROFILE in {"development", "observe"}


def env_bool(name: str, *, production: bool, development: bool | None = None) -> bool:
    raw = os.getenv(name)
    if raw is not None:
        return raw.strip().lower() in {"1", "true", "yes", "on"}
    if is_production():
        return production
    if development is not None:
        return development
    return not production


def env_str(name: str, *, production: str, development: str) -> str:
    raw = os.getenv(name)
    if raw is not None and str(raw).strip():
        return str(raw).strip()
    return production if is_production() else development


def env_csv(name: str, *, production: Iterable[str], development: Iterable[str]) -> List[str]:
    raw = os.getenv(name)
    if raw is not None and str(raw).strip():
        return [part.strip().upper() for part in raw.split(",") if part.strip()]
    source = production if is_production() else development
    return [str(part).strip().upper() for part in source if str(part).strip()]


def production_runtime_controls() -> Dict[str, Any]:
    """Bootstrap runtime controls when config/runtime_controls.json is absent."""
    return {
        "preset": "production",
        "signal_direct_intents": env_bool("TRADING_OS_SIGNAL_DIRECT_INTENTS", production=True, development=False),
        "stock_direct_intents": env_bool("TRADING_OS_STOCK_DIRECT_INTENTS", production=True, development=False),
        "signal_min_confidence": float(os.getenv("TRADING_OS_MIN_SIGNAL_CONFIDENCE", "0.75")),
        "signal_min_candles": int(os.getenv("TRADING_OS_SIGNAL_MIN_CANDLES", "10")),
        "signal_timeframes": env_csv(
            "TRADING_OS_SIGNAL_TIMEFRAMES",
            production=("M5", "M15", "H1"),
            development=("M5", "M15"),
        ),
        "signal_macro_gate": env_bool("TRADING_OS_SIGNAL_MACRO_GATE", production=True, development=True),
        "signal_macro_gate_max_age_sec": int(os.getenv("TRADING_OS_SIGNAL_MACRO_GATE_MAX_AGE_SEC", "900")),
        "llm_decision_mode": env_str(
            "TRADING_OS_LLM_DECISION_MODE",
            production="LIVE",
            development="ADVISORY",
        ).upper(),
        "description": "Production bootstrap: direct FX/stock intents, macro gate, LIVE brain mode.",
    }


def learner_auto_apply_enabled() -> bool:
    return env_bool("TRADING_OS_LEARNER_AUTO_APPLY", production=True, development=False)
