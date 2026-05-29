#!/usr/bin/env python3
"""Generate and evaluate MT5 chart bootstrap manifests from instruments.yaml."""
from __future__ import annotations

import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parent.parent
_NERVOUS = ROOT / "nervous"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(_NERVOUS) not in sys.path:
    sys.path.insert(0, str(_NERVOUS))
DEFAULT_MANIFEST_CSV = ROOT / "ipc" / "chart_manifest.csv"
DEFAULT_MANIFEST_JSON = ROOT / "config" / "chart_manifest.json"

_ASSET_DEFAULT_TF = {
    "forex": "M15",
    "metals": "M15",
    "stock_cfd": "M15",
    "crypto": "M15",
    "indices": "M15",
}
_TF_TO_MT5 = {
    "M1": 1,
    "M5": 5,
    "M15": 15,
    "M30": 30,
    "H1": 60,
    "H4": 240,
    "D1": 1440,
}


@dataclass
class ChartManifestEntry:
    symbol: str
    broker_symbol: str
    chart_label: str
    timeframe: str
    mt5_period: int
    asset_class: str
    enabled: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "broker_symbol": self.broker_symbol,
            "chart_label": self.chart_label,
            "timeframe": self.timeframe,
            "mt5_period": self.mt5_period,
            "asset_class": self.asset_class,
            "enabled": self.enabled,
        }


def default_timeframe(asset_class: str) -> str:
    return _ASSET_DEFAULT_TF.get(str(asset_class or "").strip(), "M15")


def mt5_period(timeframe: str) -> int:
    tf = str(timeframe or "M15").strip().upper()
    return int(_TF_TO_MT5.get(tf, 15))


def build_manifest_entries(registry=None) -> list[ChartManifestEntry]:
    if registry is None:
        from cortex.instrument_registry import InstrumentRegistry

        registry = InstrumentRegistry()

    entries: list[ChartManifestEntry] = []
    for symbol in registry.enabled_symbols():
        cfg = registry.get(symbol) or {}
        broker = str(cfg.get("broker_symbol") or symbol).strip().upper()
        asset_class = str(cfg.get("asset_class") or "forex")
        tf = default_timeframe(asset_class)
        entries.append(
            ChartManifestEntry(
                symbol=str(symbol).upper(),
                broker_symbol=broker,
                chart_label=f"chart_{broker}",
                timeframe=tf,
                mt5_period=mt5_period(tf),
                asset_class=asset_class,
                enabled=True,
            )
        )
    entries.sort(key=lambda row: row.symbol)
    return entries


def write_manifest(
    entries: Iterable[ChartManifestEntry],
    *,
    csv_path: Path = DEFAULT_MANIFEST_CSV,
    json_path: Path = DEFAULT_MANIFEST_JSON,
    registry=None,
) -> dict[str, Any]:
    from ipc_text import write_ipc_utf16  # noqa: WPS433
    from ops.mt5_template import preferred_template_stem  # noqa: WPS433

    if registry is None:
        from cortex.instrument_registry import load_registry

        registry = load_registry(force=True)

    rows = list(entries)
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.parent.mkdir(parents=True, exist_ok=True)

    # MT5 FILE_COMMON text reads expect UTF-16 (same as bridge heartbeat/tick files).
    csv_lines = ["symbol,broker_symbol,chart_label,timeframe,mt5_period,asset_class"]
    for row in rows:
        csv_lines.append(
            ",".join(
                [
                    row.symbol,
                    row.broker_symbol,
                    row.chart_label,
                    row.timeframe,
                    str(row.mt5_period),
                    row.asset_class,
                ]
            )
        )
    write_ipc_utf16(csv_path, "\r\n".join(csv_lines))

    payload = {
        "version": 1,
        "updated_ts": time.time(),
        "ea_name": "FileBridgeEA_MultiSymbol",
        "template_name": preferred_template_stem(registry),
        "charts": [row.as_dict() for row in rows],
    }
    json_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return payload


def generate_manifest(registry=None) -> dict[str, Any]:
    if registry is None:
        from cortex.instrument_registry import load_registry

        registry = load_registry(force=True)
    entries = build_manifest_entries(registry=registry)
    return write_manifest(entries, registry=registry)


def _mt5_template_status(registry=None) -> dict[str, Any]:
    from ops.mt5_template import mt5_template_status as _status

    return _status(registry)


def evaluate_bootstrap_gaps(
    *,
    ipc_dir: Path | None = None,
    max_heartbeat_age: float = 120.0,
    registry=None,
) -> dict[str, Any]:
    from ipc_path import get_ipc_dir
    from ops.bridge_status import chart_dirs, heartbeat_age, read_tick

    ipc_dir = Path(ipc_dir or get_ipc_dir())
    entries = build_manifest_entries(registry=registry)
    charts = chart_dirs(ipc_dir)
    chart_set = {path.name for path in charts}

    rows: list[dict[str, Any]] = []
    missing: list[str] = []
    stale: list[str] = []
    ready: list[str] = []

    for entry in entries:
        chart_path = ipc_dir / entry.chart_label
        present = entry.chart_label in chart_set
        hb_age, _hb_raw = heartbeat_age(chart_path / "heartbeat.txt") if present else (None, "missing")
        tick = read_tick(chart_path / "tick.txt") if present else None
        fresh = present and hb_age is not None and hb_age <= max_heartbeat_age
        row = {
            **entry.as_dict(),
            "chart_present": present,
            "heartbeat_age_sec": hb_age,
            "tick_ok": bool(tick),
            "fresh": fresh,
            "result": "READY" if fresh else ("STALE" if present else "MISSING"),
        }
        rows.append(row)
        if not present:
            missing.append(entry.symbol)
        elif not fresh:
            stale.append(entry.symbol)
        else:
            ready.append(entry.symbol)

    return {
        "ts": time.time(),
        "ipc_dir": str(ipc_dir),
        "max_heartbeat_age_sec": max_heartbeat_age,
        "summary": {
            "enabled": len(entries),
            "ready": len(ready),
            "missing": len(missing),
            "stale": len(stale),
        },
        "ready_symbols": ready,
        "missing_symbols": missing,
        "stale_symbols": stale,
        "charts": rows,
        "actions": {
            "generate_manifest": "python3 scripts/bootstrap_mt5_charts.py --write",
            "mt5_service": "Attach bridge/ChartBootstrapService.ex5 to any chart (Algo Trading ON)",
            "mt5_template": "Bridge template names come from defaults.mt5_bridge in instruments.yaml",
            "mt5_log": "Check ipc/chart_bootstrap.log for template_ready / bridge_attached / template_apply_failed",
            "fix_template_name": "python scripts/install_mt5_bridge_template.py --install",
        },
        "mt5_template_status": _mt5_template_status(registry),
    }
