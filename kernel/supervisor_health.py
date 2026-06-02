#!/usr/bin/env python3
"""Persist supervisor-managed layer liveness into kernel/health.json."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

ChildRow = Tuple[str, Any, Path, str, Any]


def layer_snapshot(children: Sequence[ChildRow]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for name, proc, script, _purpose, _log in children:
        rc = proc.poll() if proc is not None else -1
        running = rc is None and proc is not None
        rows.append(
            {
                "layer": name,
                "pid": proc.pid if running else None,
                "running": running,
                "exit_code": rc,
                "script": script.name if script else "",
            }
        )
    return rows


def build_supervisor_block(
    children: Sequence[ChildRow],
    *,
    supervisor_pid: Optional[int] = None,
) -> Dict[str, Any]:
    layers = layer_snapshot(children)
    running_count = sum(1 for row in layers if row.get("running"))
    return {
        "ts": time.time(),
        "pid": supervisor_pid if supervisor_pid is not None else os.getpid(),
        "layer_count": len(layers),
        "running_count": running_count,
        "all_running": running_count == len(layers) and len(layers) > 0,
        "layers": layers,
    }


def merge_supervisor_health(
    health_path: Path,
    block: Mapping[str, Any],
) -> Dict[str, Any]:
    health_path = Path(health_path)
    health_path.parent.mkdir(parents=True, exist_ok=True)
    existing: Dict[str, Any] = {}
    if health_path.exists():
        try:
            existing = json.loads(health_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            existing = {}
    existing["supervisor"] = dict(block)
    health_path.write_text(json.dumps(existing, indent=2), encoding="utf-8")
    return existing
