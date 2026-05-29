#!/usr/bin/env python3
"""Unified read-only context bundle for external agents (MCP, ADK, dashboard)."""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _latest_by_topic(events: List[dict[str, Any]], topic: str) -> dict[str, Any]:
    for event in events:
        if event.get("topic") == topic:
            return event.get("payload") or {}
    return {}


def summarize_brain(events: List[dict[str, Any]]) -> dict[str, Any]:
    from cortex.llm_status import latest_llm_summary

    return latest_llm_summary(events)


def summarize_macro_policy(events: List[dict[str, Any]]) -> dict[str, Any]:
    payload = _latest_by_topic(events, "risk.macro_policy") or {}
    return {
        "available": bool(payload),
        "action": payload.get("action"),
        "scale_factor": payload.get("scale_factor"),
        "reason": payload.get("reason"),
        "category": payload.get("category"),
        "ts": payload.get("ts"),
    }


def _telemetry_health() -> dict[str, Any]:
    port = int(os.getenv("TRADING_OS_TELEMETRY_PORT", "9876"))
    url = f"http://127.0.0.1:{port}/health"
    try:
        with urllib.request.urlopen(url, timeout=0.75) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
        return {"ok": False, "source": "telemetry", "error": "telemetry_unreachable"}


def build_agent_context(
    root: Path | None = None,
    *,
    bus_limit: int = 40,
    max_heartbeat_age: float = 30.0,
    live_preflight: bool = True,
    strict_instruments: bool = False,
) -> dict[str, Any]:
    root = Path(root or ROOT)
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
        sys.path.insert(0, str(root / "nervous"))

    from bus import tail  # noqa: WPS433
    from cortex.instrument_registry import InstrumentRegistry  # noqa: WPS433
    from ipc_path import get_ipc_dir  # noqa: WPS433
    from ops.readiness_eval import ReadinessOptions, evaluate_readiness  # noqa: WPS433
    from ops.chart_bootstrap import evaluate_bootstrap_gaps  # noqa: WPS433
    from runtime_controls import load_controls  # noqa: WPS433

    controls = load_controls()
    events = tail(max(bus_limit * 4, 80))
    events.sort(key=lambda row: row.get("ts", 0), reverse=True)
    brain = summarize_brain(events)
    macro = summarize_macro_policy(events)

    preflight = evaluate_readiness(
        root,
        ReadinessOptions(
            live=live_preflight,
            strict_instruments=strict_instruments,
            max_heartbeat_age=max_heartbeat_age,
        ),
        ipc_dir=Path(get_ipc_dir()),
    ).as_dict()

    registry = InstrumentRegistry(root / "config" / "instruments.yaml")
    enabled = []
    for symbol in registry.enabled_symbols():
        meta = registry.get(symbol) or {}
        enabled.append(
            {
                "symbol": symbol,
                "asset_class": meta.get("asset_class"),
                "strategies": meta.get("strategies"),
                "chart": meta.get("chart"),
            }
        )

    blockers: list[str] = []
    if (root / "STOP_TRADING").exists():
        blockers.append("stop_trading_active")
    if not controls.get("signal_direct_intents"):
        blockers.append("direct pattern intents disabled")
    elif controls.get("signal_direct_intents") and not controls.get("stock_direct_intents"):
        blockers.append("stock direct intents disabled (FX/metals only)")
    if brain.get("llm_ok") is False:
        blockers.append(brain.get("operator_message") or f"LLM unavailable: {brain.get('error_code')}")

    return {
        "ts": time.time(),
        "profile": os.getenv("TRADING_OS_PROFILE", "production"),
        "trading_mode": os.getenv("TRADING_OS_MODE", "SIMULATION"),
        "health": _read_json(root / "kernel" / "health.json"),
        "telemetry": _telemetry_health(),
        "preflight": preflight,
        "runtime_controls": controls,
        "brain_latest": brain,
        "macro_policy": macro,
        "enabled_instruments": enabled,
        "blockers": blockers,
        "recent_events": events[:bus_limit],
        "mcp": {
            "context_server": "bridge/context_mcp_server.py",
            "mt5_server": "bridge/mt5_mcp_server.py",
            "trade_tools_require": "TRADING_OS_MCP_ALLOW_TRADE=1 and hook approval",
            "trade_route": "muscle.order.intent / muscle.position.intent → immune → muscle IPC",
        },
        "chart_bootstrap": evaluate_bootstrap_gaps(max_heartbeat_age=max_heartbeat_age),
    }
