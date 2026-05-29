#!/usr/bin/env python3
"""Runtime controls for Trading OS.

Small dependency-free helper for dashboard/operator controls that should apply
without restarting every process. Environment variables remain bootstrap defaults,
but this JSON file is the runtime source of truth once present.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Dict

from trading_profile import is_production, production_runtime_controls

ROOT = Path(__file__).resolve().parent
CONTROL_FILE = ROOT / "config" / "runtime_controls.json"

PRESETS: Dict[str, Dict[str, Any]] = {
    "observe_only": {
        "preset": "observe_only",
        "signal_direct_intents": False,
        "signal_min_confidence": 0.75,
        "signal_min_candles": 10,
        "signal_timeframes": ["M5", "M15"],
        "signal_macro_gate": True,
        "signal_macro_gate_max_age_sec": 900,
        "llm_decision_mode": "ADVISORY",
        "description": "Observe signals and AI reasoning without pattern-direct orders.",
    },
    "demo_cautious": {
        "preset": "demo_cautious",
        "signal_direct_intents": True,
        "stock_direct_intents": True,
        "signal_min_confidence": 0.80,
        "signal_min_candles": 10,
        "signal_timeframes": ["M5", "M15"],
        "signal_macro_gate": True,
        "signal_macro_gate_max_age_sec": 900,
        "llm_decision_mode": "LIVE",
        "description": "Allow high-confidence demo trades with macro gate enabled.",
    },
    "demo_aggressive": {
        "preset": "demo_aggressive",
        "signal_direct_intents": True,
        "stock_direct_intents": True,
        "signal_min_confidence": 0.65,
        "signal_min_candles": 5,
        "signal_timeframes": ["M1", "M5", "M15"],
        "signal_macro_gate": False,
        "signal_macro_gate_max_age_sec": 900,
        "llm_decision_mode": "LIVE",
        "description": "Allow more experimental demo trades, including M1 warmup signals, with macro gate advisory-only.",
    },
    "demo_stocks": {
        "preset": "demo_stocks",
        "signal_direct_intents": True,
        "stock_direct_intents": True,
        "signal_min_confidence": 0.75,
        "signal_min_candles": 10,
        "signal_timeframes": ["M15", "H1"],
        "signal_macro_gate": True,
        "signal_macro_gate_max_age_sec": 900,
        "llm_decision_mode": "LIVE",
        "description": "Trade enabled US stock CFDs on M15/H1 (suited to ~15m delayed MT5 quotes).",
    },
    "production": {
        "preset": "production",
        "signal_direct_intents": True,
        "stock_direct_intents": True,
        "signal_min_confidence": 0.75,
        "signal_min_candles": 10,
        "signal_timeframes": ["M5", "M15", "H1"],
        "signal_macro_gate": True,
        "signal_macro_gate_max_age_sec": 900,
        "llm_decision_mode": "LIVE",
        "description": "Default production profile: direct FX/stock intents with macro gate and LIVE brain.",
    },
    "halted": {
        "preset": "halted",
        "signal_direct_intents": False,
        "signal_min_confidence": 0.95,
        "signal_min_candles": 10,
        "signal_timeframes": ["M5", "M15"],
        "signal_macro_gate": True,
        "signal_macro_gate_max_age_sec": 900,
        "llm_decision_mode": "ADVISORY",
        "description": "No new pattern-direct risk. Pair with STOP_TRADING for execution halt.",
    },
}


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def defaults() -> Dict[str, Any]:
    if is_production():
        base = production_runtime_controls()
    else:
        base = {
            "preset": "env_bootstrap",
            "signal_direct_intents": _env_bool("TRADING_OS_SIGNAL_DIRECT_INTENTS", False),
            "stock_direct_intents": _env_bool("TRADING_OS_STOCK_DIRECT_INTENTS", False),
            "signal_min_confidence": _env_float("TRADING_OS_MIN_SIGNAL_CONFIDENCE", 0.75),
            "signal_min_candles": _env_int("TRADING_OS_SIGNAL_MIN_CANDLES", 10),
            "signal_timeframes": [
                s.strip().upper()
                for s in os.getenv("TRADING_OS_SIGNAL_TIMEFRAMES", "M5,M15").split(",")
                if s.strip()
            ],
            "signal_macro_gate": _env_bool("TRADING_OS_SIGNAL_MACRO_GATE", True),
            "signal_macro_gate_max_age_sec": _env_int("TRADING_OS_SIGNAL_MACRO_GATE_MAX_AGE_SEC", 900),
            "llm_decision_mode": os.getenv("TRADING_OS_LLM_DECISION_MODE", "ADVISORY").strip().upper(),
            "description": "Runtime controls derived from development bootstrap defaults.",
        }
    return {
        "version": 1,
        "updated_ts": 0,
        **base,
    }


def normalize_controls(raw: Dict[str, Any] | None) -> Dict[str, Any]:
    data = defaults()
    if raw:
        data.update(raw)
    data["version"] = int(data.get("version") or 1)
    data["signal_direct_intents"] = bool(data.get("signal_direct_intents"))
    data["stock_direct_intents"] = bool(data.get("stock_direct_intents"))
    data["signal_min_confidence"] = max(0.0, min(1.0, float(data.get("signal_min_confidence", 0.75))))
    data["signal_min_candles"] = max(3, int(data.get("signal_min_candles", 10)))
    raw_tfs = data.get("signal_timeframes") or ["M5", "M15"]
    if isinstance(raw_tfs, str):
        raw_tfs = [x.strip() for x in raw_tfs.split(",")]
    allowed_tfs = {"M1", "M5", "M15", "M30", "H1"}
    data["signal_timeframes"] = [str(x).strip().upper() for x in raw_tfs if str(x).strip().upper() in allowed_tfs] or ["M5", "M15"]
    data["signal_macro_gate"] = bool(data.get("signal_macro_gate", True))
    data["signal_macro_gate_max_age_sec"] = max(0, int(data.get("signal_macro_gate_max_age_sec", 900)))
    data["llm_decision_mode"] = str(data.get("llm_decision_mode") or "ADVISORY").strip().upper()
    data["preset"] = str(data.get("preset") or "custom")
    return data


def load_controls(path: Path = CONTROL_FILE) -> Dict[str, Any]:
    if not path.exists():
        return normalize_controls(None)
    try:
        return normalize_controls(json.loads(path.read_text(encoding="utf-8")))
    except (json.JSONDecodeError, OSError, ValueError, TypeError):
        data = defaults()
        data["load_error"] = True
        return normalize_controls(data)


def write_controls(updates: Dict[str, Any], path: Path = CONTROL_FILE) -> Dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    current = load_controls(path)
    current.update(updates)
    current["updated_ts"] = time.time()
    data = normalize_controls(current)
    tmp = path.with_suffix(f".tmp.{os.getpid()}")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return data


def apply_preset(preset: str, path: Path = CONTROL_FILE) -> Dict[str, Any]:
    if preset not in PRESETS:
        raise ValueError(f"unknown preset: {preset}")
    return write_controls(dict(PRESETS[preset]), path=path)


if __name__ == "__main__":
    print(json.dumps(load_controls(), indent=2, sort_keys=True))
