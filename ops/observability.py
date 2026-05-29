"""Shared health probe assembly for dashboard, telemetry, and ops scripts."""
from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

from ops import bridge_status


def unavailable_bridge_health(max_heartbeat_age: float | None, detail: str) -> dict[str, Any]:
    return {
        "available": False,
        "connected": False,
        "mode": "unavailable",
        "detail": detail,
        "ipc_root": None,
        "max_heartbeat_age_sec": max_heartbeat_age,
        "root": {
            "heartbeat_age_sec": None,
            "heartbeat_fresh": False,
            "heartbeat_detail": "missing",
            "tick_ok": False,
            "tick": "missing",
        },
        "charts": [],
        "fresh_chart_count": 0,
        "stale_chart_count": 0,
    }


def build_bridge_health(ipc_dir: Path, max_heartbeat_age: float | None = None) -> dict[str, Any]:
    """MT5 bridge status with defensive fallback when the probe fails."""
    if max_heartbeat_age is None:
        max_heartbeat_age = bridge_status.DEFAULT_HEARTBEAT_STALE_SEC
    try:
        return bridge_status.bridge_snapshot(ipc_dir, max_heartbeat_age=max_heartbeat_age)
    except Exception as exc:  # pragma: no cover - defensive path
        return unavailable_bridge_health(max_heartbeat_age, f"bridge probe failed: {exc}")


def chart_health_rows(snapshot: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Per-chart heartbeat freshness keyed by chart directory name."""
    rows: dict[str, dict[str, Any]] = {}
    for chart in snapshot.get("charts") or []:
        name = chart.get("name")
        if not name:
            continue
        age = chart.get("heartbeat_age_sec")
        if age is None:
            rows[name] = {
                "alive": False,
                "error": chart.get("heartbeat_detail", "missing"),
            }
            continue
        rows[name] = {
            "alive": bool(chart.get("heartbeat_fresh")),
            "age_sec": round(float(age), 1),
        }
    return rows


def merge_health_report(
    *,
    bridge_snapshot: dict[str, Any],
    bus_stats: dict[str, Any],
    instrument_readiness: dict[str, Any],
    uptime_sec: float,
    mode: str | None = None,
    multisymbol: str | None = None,
) -> dict[str, Any]:
    """Standard JSON health document for telemetry and dashboards."""
    charts = chart_health_rows(bridge_snapshot)
    return {
        "ts": time.time(),
        "uptime_sec": round(uptime_sec, 1),
        "charts": charts,
        "charts_alive": sum(1 for chart in charts.values() if chart.get("alive")),
        "bus": bus_stats,
        "instruments": instrument_readiness,
        "mode": mode if mode is not None else os.getenv("TRADING_OS_MODE", "SIMULATION"),
        "multisymbol": multisymbol if multisymbol is not None else os.getenv("TRADING_OS_MULTISYMBOL", "auto"),
    }
