"""Programmatic readiness evaluation shared by CLI gate and supervisor preflight."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional

from bridge.mt5_ipc_protocol import CommandSlotBusy, IPCPaths, ResponseTimeout, new_cid, ping
from cortex.instrument_registry import InstrumentRegistry, load_registry
from muscle import pnl_sync
from ops.bridge_status import (
    chart_dirs,
    detect_ipc_mode,
    heartbeat_age,
    read_tick,
    tick_ok,
)
from ops.readiness_policy import chart_in_scope, instrument_blocks_boot, load_readiness_policy, ReadinessPolicy


@dataclass
class ReadinessOptions:
    live: bool = False
    strict_instruments: bool = False
    instrument_gate: Optional[str] = None
    chart_gate: Optional[str] = None
    max_heartbeat_age: float = 15.0
    roundtrip: bool = False
    roundtrip_timeout: float = 5.0
    roundtrip_target: str = "root"
    all_symbols: bool = False


@dataclass
class ReadinessResult:
    ok: bool
    checks: list[dict[str, Any]] = field(default_factory=list)
    ipc_mode: dict[str, Any] = field(default_factory=dict)
    instruments: dict[str, Any] = field(default_factory=dict)
    charts: list[str] = field(default_factory=list)
    positions: dict[str, Any] = field(default_factory=dict)
    roundtrip: Optional[dict[str, Any]] = None
    enabled_stocks: list[str] = field(default_factory=list)
    readiness_policy: dict[str, Any] = field(default_factory=dict)
    boot_deferred_instruments: list[str] = field(default_factory=list)

    @property
    def exit_code(self) -> int:
        return 0 if self.ok else 1

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "charts": self.charts,
            "ipc_mode": self.ipc_mode,
            "instruments": self.instruments,
            "positions": self.positions.get("open_count", 0),
            "floating_pnl": self.positions.get("floating_pnl", 0.0),
            "roundtrip": self.roundtrip,
            "enabled_stocks": self.enabled_stocks,
            "readiness_policy": self.readiness_policy,
            "boot_deferred_instruments": self.boot_deferred_instruments,
            "checks": self.checks,
        }


def _record(checks: list[dict[str, Any]], name: str, ok: bool, detail: str = "") -> bool:
    checks.append({"name": name, "ok": bool(ok), "detail": detail})
    return bool(ok)


def roundtrip_targets(
    mode: str,
    ipc_dir: Path,
    charts: list[Path],
    instrument_status: dict[str, Any],
) -> list[Path]:
    if mode == "root":
        return [ipc_dir]
    chart_by_name = {p.name: p for p in charts}
    if mode == "present-charts":
        return list(charts)
    if mode == "enabled-charts":
        enabled_chart_names = [s["chart"] for s in instrument_status.values() if s.get("enabled")]
        return [chart_by_name[name] for name in enabled_chart_names if name in chart_by_name]
    raise ValueError(f"unknown roundtrip target mode: {mode}")


def run_ping_roundtrip(target: Path, timeout_sec: float) -> dict[str, Any]:
    cid = new_cid("readiness")
    response = ping(IPCPaths.from_root(target), timeout_sec=timeout_sec, cid=cid)
    return {"ok": True, "target": str(target), "cid": cid, "response": response[:160]}


def _resolve_policy(registry, opts: ReadinessOptions):
    overrides: dict[str, Any] = {}
    if opts.instrument_gate:
        overrides["instrument_gate"] = opts.instrument_gate
    if opts.chart_gate:
        overrides["chart_gate"] = opts.chart_gate
    if opts.strict_instruments:
        overrides["instrument_gate"] = "all_enabled"
    return load_readiness_policy(registry, overrides)


def _defer_session_closed(status: dict[str, Any], policy: ReadinessPolicy) -> bool:
    """Allow LIVE boot when bridge is up but the symbol session is closed."""
    if status.get("ready"):
        return False
    if str(status.get("result") or "") != "BLOCKED_SESSION_CLOSED":
        return False
    if not status.get("chart_present"):
        return False
    if policy.instrument_gate == "all_enabled":
        return False
    return True


def evaluate_readiness(
    root: Path,
    opts: ReadinessOptions,
    *,
    ipc_dir: Path,
    registry: Optional[InstrumentRegistry] = None,
    on_check: Optional[Callable[[str, bool, str], None]] = None,
    now: Optional[datetime] = None,
) -> ReadinessResult:
    """Evaluate bridge + instrument readiness without placing trades."""
    checks: list[dict[str, Any]] = []
    ok = True

    def check(name: str, passed: bool, detail: str = "") -> bool:
        nonlocal ok
        if on_check:
            on_check(name, passed, detail)
        ok &= _record(checks, name, passed, detail)
        return passed

    registry = registry or load_registry(force=True)
    policy = _resolve_policy(registry, opts)
    enabled_chart_set = set(registry.enabled_chart_labels())
    charts = chart_dirs(ipc_dir)
    ipc_mode = detect_ipc_mode(ipc_dir, charts, max_heartbeat_age=opts.max_heartbeat_age)
    boot_deferred: list[str] = []

    check("STOP_TRADING absent", not (root / "STOP_TRADING").exists())
    check("IPC directory exists", ipc_dir.exists(), str(ipc_dir))
    check("secrets file not tracked", True, "config/secrets.yaml is gitignored by policy")

    if opts.live and ipc_mode["mode"] in ("root", "mixed"):
        check("active IPC bridge detected", True, f"{ipc_mode['mode']}: {ipc_mode['detail']}")
    else:
        check("chart directories discovered", len(charts) > 0, ", ".join(p.name for p in charts) or "none")
        if opts.live:
            check(
                "active IPC bridge detected",
                ipc_mode["mode"] in ("chart", "mixed"),
                f"{ipc_mode['mode']}: {ipc_mode['detail']}",
            )
    if ipc_mode.get("stale_charts") and ipc_mode["mode"] in ("root", "mixed"):
        _record(checks, "stale chart IPC folders ignored", True, ", ".join(ipc_mode["stale_charts"]))

    tick_map: dict[str, dict[str, Any]] = {}
    for chart in charts:
        age, detail = heartbeat_age(chart / "heartbeat.txt")
        fresh = age is not None and age <= opts.max_heartbeat_age
        in_scope = chart_in_scope(policy, chart.name, enabled_chart_set)
        require_fresh_chart = opts.live and ipc_mode["mode"] not in ("root",) and in_scope
        if in_scope:
            label = f"{chart.name} heartbeat {'fresh' if require_fresh_chart else 'parseable'}"
            passed = fresh if require_fresh_chart else age is not None
        else:
            label = f"{chart.name} heartbeat (out_of_scope)"
            passed = age is not None if age is not None else True
            detail = detail or "optional chart not in enabled manifest"
        check(
            label,
            passed,
            f"age={age:.1f}s" if age is not None else detail,
        )
        if not in_scope:
            continue
        t_ok, t_detail = tick_ok(chart / "tick.txt")
        check(f"{chart.name} tick readable", t_ok, t_detail)
        tick = read_tick(chart / "tick.txt")
        if tick:
            tick_map[tick["symbol"]] = tick
            quote_age = tick.get("quote_age_sec")
            sym = tick.get("symbol", chart.name.replace("chart_", ""))
            max_quote = registry.max_fresh_quote_sec(sym)
            if quote_age is None:
                check(f"{chart.name} quote age", True, "quote_ts missing (legacy tick line)")
            elif quote_age > max_quote:
                check(
                    f"{chart.name} quote age",
                    True,
                    f"quote_age={quote_age:.0f}s (>{max_quote:.0f}s; re-check in session)",
                )
            else:
                check(f"{chart.name} quote age", True, f"quote_age={quote_age:.0f}s")

    instrument_status = registry.readiness_snapshot([p.name for p in charts], tick_map, now=now)
    enabled_stocks: list[str] = []
    for symbol, status in instrument_status.items():
        if not opts.all_symbols and not status.get("enabled"):
            continue
        if status.get("enabled") and status.get("asset_class") == "stock_cfd":
            enabled_stocks.append(f"{symbol}:{status['result']}")
        if not status.get("enabled"):
            continue
        if status.get("ready"):
            _record(checks, f"instrument {symbol} ready", True, status.get("result", "READY"))
            continue
        result = status.get("result", "not_ready")
        if _defer_session_closed(status, policy):
            deferred = f"{symbol}:{result}"
            boot_deferred.append(deferred)
            _record(checks, f"instrument {symbol} ready (boot_deferred)", True, result)
            continue
        if policy.instrument_gate == "all_enabled":
            ok = False
            _record(checks, f"instrument {symbol} ready", False, result)
            continue
        if instrument_blocks_boot(
            policy,
            registry,
            enabled=bool(status.get("enabled")),
            ready=False,
            symbol=symbol,
        ):
            ok = False
            _record(checks, f"instrument {symbol} ready", False, result)
        else:
            deferred = f"{symbol}:{result}"
            boot_deferred.append(deferred)
            _record(checks, f"instrument {symbol} ready (boot_deferred)", True, result)

    snapshot = pnl_sync.snapshot_from_data_file()
    report = pnl_sync.reconcile_positions(snapshot, publish_events=False)
    positions = {
        "open_count": report.get("open_count", 0),
        "floating_pnl": report.get("floating_pnl", 0.0),
        "source": report.get("source"),
    }
    check(
        "position/PnL reconciliation runnable",
        isinstance(report.get("open_count"), int),
        f"open={report['open_count']} floating_pnl={report['floating_pnl']}",
    )

    roundtrip_result = None
    if opts.roundtrip:
        roundtrip_result = {"target_mode": opts.roundtrip_target, "results": []}
        targets = roundtrip_targets(opts.roundtrip_target, ipc_dir, charts, instrument_status)
        if not targets:
            roundtrip_result["ok"] = False
            check("no-trade IPC PING roundtrip targets", False, "no matching target directories")
        for target in targets:
            try:
                result = run_ping_roundtrip(target, opts.roundtrip_timeout)
                roundtrip_result["results"].append(result)
                check(
                    f"no-trade IPC PING roundtrip {target.name}",
                    True,
                    f"cid={result['cid']} {result['response'][:80]}",
                )
            except CommandSlotBusy as exc:
                roundtrip_result["results"].append({"ok": False, "target": str(target), "error": str(exc)})
                check(f"no-trade IPC PING roundtrip {target.name}", False, "cmd_in busy, not overwritten")
            except ResponseTimeout as exc:
                roundtrip_result["results"].append({"ok": False, "target": str(target), "error": str(exc)})
                check(f"no-trade IPC PING roundtrip {target.name}", False, str(exc))
        roundtrip_result["ok"] = bool(roundtrip_result["results"]) and all(
            r.get("ok") for r in roundtrip_result["results"]
        )

    return ReadinessResult(
        ok=ok,
        checks=checks,
        ipc_mode=ipc_mode,
        instruments=instrument_status,
        charts=[p.name for p in charts],
        positions=positions,
        roundtrip=roundtrip_result,
        enabled_stocks=enabled_stocks,
        readiness_policy=policy.as_dict(),
        boot_deferred_instruments=boot_deferred,
    )


def evaluate_readiness_json(root: Path, opts: ReadinessOptions, *, ipc_dir: Path) -> str:
    return json.dumps(evaluate_readiness(root, opts, ipc_dir=ipc_dir).as_dict(), indent=2)
