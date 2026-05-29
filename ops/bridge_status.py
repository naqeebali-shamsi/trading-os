"""MT5 IPC bridge heartbeat/tick probes shared by readiness, status, and health."""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import Any, Optional

_ROOT = Path(__file__).resolve().parent.parent
_NERVOUS = _ROOT / "nervous"
if str(_NERVOUS) not in sys.path:
    sys.path.insert(0, str(_NERVOUS))

from ipc_text import read_ipc_text  # noqa: E402
from tick_quote import parse_tick_text  # noqa: E402

DEFAULT_HEARTBEAT_STALE_SEC = float(os.environ.get("TRADING_OS_HEARTBEAT_STALE_SEC", "30"))


def heartbeat_age(path: Path) -> tuple[Optional[float], str]:
    text = read_ipc_text(path)
    if not text:
        return None, "missing"
    try:
        stamp = float(text.split("|")[0] if "|" in text else text.split(",")[0])
        return max(0.0, time.time() - stamp), text[:80]
    except Exception:
        return None, f"unparseable: {text[:80]}"


def tick_ok(path: Path) -> tuple[bool, str]:
    text = read_ipc_text(path)
    if not text:
        return False, "missing"
    parts = text.split(",")
    if len(parts) < 3:
        return False, text[:80]
    try:
        bid, ask = float(parts[1]), float(parts[2])
        return bid > 0 and ask > 0 and ask >= bid, text[:80]
    except Exception:
        return False, text[:80]


def read_tick(path: Path) -> Optional[dict[str, Any]]:
    text = read_ipc_text(path)
    if not text:
        return None
    return parse_tick_text(text)


def chart_dirs(ipc_dir: Path) -> list[Path]:
    if not ipc_dir.exists():
        return []
    return sorted(p for p in ipc_dir.iterdir() if p.is_dir() and p.name.startswith("chart_"))


def detect_ipc_mode(
    ipc_dir: Path,
    charts: list[Path] | None = None,
    *,
    max_heartbeat_age: float = DEFAULT_HEARTBEAT_STALE_SEC,
) -> dict[str, Any]:
    """Detect whether MT5 is actively using root IPC, chart IPC, both, or neither."""
    charts = chart_dirs(ipc_dir) if charts is None else list(charts)
    root_age, root_detail = heartbeat_age(ipc_dir / "heartbeat.txt")
    root_tick_ok, _ = tick_ok(ipc_dir / "tick.txt")
    root_fresh = root_age is not None and root_age <= max_heartbeat_age and root_tick_ok

    fresh_charts: list[str] = []
    stale_charts: list[str] = []
    for chart in charts:
        age, _ = heartbeat_age(chart / "heartbeat.txt")
        t_ok, _ = tick_ok(chart / "tick.txt")
        if age is not None and age <= max_heartbeat_age and t_ok:
            fresh_charts.append(chart.name)
        else:
            stale_charts.append(chart.name)

    if root_fresh and fresh_charts:
        mode = "mixed"
        detail = f"root plus {len(fresh_charts)} fresh chart bridge(s)"
    elif root_fresh:
        mode = "root"
        detail = "root bridge active"
    elif fresh_charts:
        mode = "chart"
        detail = f"{len(fresh_charts)} chart bridge(s) active"
    else:
        mode = "offline"
        detail = root_detail if root_age is None else "no fresh root/chart heartbeat"
    return {
        "mode": mode,
        "detail": detail,
        "root_fresh": root_fresh,
        "root_heartbeat_age_sec": root_age,
        "fresh_charts": fresh_charts,
        "stale_charts": stale_charts,
    }


def bridge_snapshot(ipc_dir: Path, max_heartbeat_age: float | None = None) -> dict[str, Any]:
    """Consolidated MT5 bridge status for dashboards, telemetry, and ops scripts."""
    if max_heartbeat_age is None:
        max_heartbeat_age = DEFAULT_HEARTBEAT_STALE_SEC
    charts = chart_dirs(ipc_dir)
    root_age, root_detail = heartbeat_age(ipc_dir / "heartbeat.txt")
    root_tick_ok, root_tick_detail = tick_ok(ipc_dir / "tick.txt")
    mode = detect_ipc_mode(ipc_dir, charts, max_heartbeat_age=max_heartbeat_age)
    chart_rows: list[dict[str, Any]] = []
    fresh_charts = 0
    for chart in charts:
        age, detail = heartbeat_age(chart / "heartbeat.txt")
        chart_tick_ok, tick_detail = tick_ok(chart / "tick.txt")
        tick_payload = read_tick(chart / "tick.txt")
        quote_age_sec = tick_payload.get("quote_age_sec") if tick_payload else None
        heartbeat_fresh = age is not None and age <= max_heartbeat_age
        if heartbeat_fresh:
            fresh_charts += 1
        chart_rows.append({
            "name": chart.name,
            "heartbeat_age_sec": age,
            "heartbeat_fresh": heartbeat_fresh,
            "heartbeat_detail": detail,
            "tick_ok": chart_tick_ok,
            "tick": tick_detail,
            "quote_age_sec": quote_age_sec,
            "quote_ts": tick_payload.get("quote_ts") if tick_payload else None,
        })
    connected = mode.get("mode") in {"root", "chart", "mixed"}
    return {
        "available": True,
        "connected": connected,
        "mode": mode.get("mode", "unknown"),
        "detail": mode.get("detail", "unknown"),
        "ipc_root": str(ipc_dir),
        "max_heartbeat_age_sec": max_heartbeat_age,
        "root": {
            "heartbeat_age_sec": root_age,
            "heartbeat_fresh": root_age is not None and root_age <= max_heartbeat_age,
            "heartbeat_detail": root_detail,
            "tick_ok": root_tick_ok,
            "tick": root_tick_detail,
        },
        "charts": chart_rows,
        "fresh_chart_count": fresh_charts,
        "stale_chart_count": max(len(chart_rows) - fresh_charts, 0),
    }
