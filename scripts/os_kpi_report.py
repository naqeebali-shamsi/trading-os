#!/usr/bin/env python3
"""Consolidated operational/trading KPI snapshot for Trading OS.

Read-only: inspects IPC files, sends no-trade MT5 readiness commands, and summarizes
append-only bus topic streams. Intended for quick live/demo health checks.
"""
from __future__ import annotations

import argparse
import json
import os
import shlex
import statistics
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bridge.mt5_ipc_protocol import (  # noqa: E402
    CommandSlotBusy,
    IPCPaths,
    ResponseTimeout,
    get_positions,
    ping,
    status,
)
from nervous.ipc_path import get_ipc_dir  # noqa: E402

TOPICS = ROOT / "nervous" / "topics"
IPC_ROOT = Path(get_ipc_dir())


def _read_text(path: Path) -> str | None:
    if not path.exists():
        return None
    try:
        raw = path.read_bytes()
        if raw.startswith(b"\xff\xfe") or raw.startswith(b"\xfe\xff"):
            return raw.decode("utf-16", errors="replace").lstrip("\ufeff").strip()
        return raw.decode("utf-8", errors="replace").strip()
    except Exception:
        return None


def _file_ts_age(path: Path) -> float | None:
    try:
        return time.time() - path.stat().st_mtime
    except FileNotFoundError:
        return None


def _parse_mt5_ts(text: str | None) -> float | None:
    if not text:
        return None
    # heartbeat: "epoch|alive". tick: "SYMBOL,bid,ask,epoch".
    fields = []
    for chunk in text.replace("|", ",").split(","):
        fields.append(chunk.strip())
    for field in reversed(fields):
        try:
            value = float(field)
        except ValueError:
            continue
        # Treat only plausible unix timestamps as freshness timestamps, not prices.
        if value > 1_000_000_000:
            return value
    return None


def _topic_events(topic: str, since: float | None = None) -> list[dict[str, Any]]:
    path = TOPICS / f"{topic}.jsonl"
    if not path.exists():
        return []
    events: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            if since is not None and float(ev.get("ts") or 0) < since:
                continue
            events.append(ev)
    return events


def _payloads(topic: str, since: float | None = None) -> Iterable[dict[str, Any]]:
    for ev in _topic_events(topic, since):
        payload = ev.get("payload")
        if isinstance(payload, dict):
            yield payload


def _service_env(service_name: str) -> dict[str, str]:
    try:
        out = subprocess.run(
            ["systemctl", "--user", "show", service_name, "-p", "Environment", "--value"],
            check=False,
            capture_output=True,
            text=True,
            timeout=3,
        ).stdout.strip()
    except Exception:
        return {}
    env: dict[str, str] = {}
    for item in shlex.split(out):
        if "=" in item:
            k, v = item.split("=", 1)
            env[k] = v
    return env


def _service_status() -> dict[str, Any]:
    name = os.getenv("TRADING_OS_SERVICE", "trading-os.service")
    env = _service_env(name)
    try:
        active = subprocess.run(
            ["systemctl", "--user", "is-active", name],
            check=False,
            capture_output=True,
            text=True,
            timeout=3,
        ).stdout.strip()
    except Exception as exc:
        return {"service": name, "active": "unknown", "env": env, "error": str(exc)}
    return {"service": name, "active": active, "ok": active == "active", "env": env}


def _bridge_snapshot(timeout: float) -> dict[str, Any]:
    paths = IPCPaths.from_root(IPC_ROOT)
    hb_text = _read_text(paths.heartbeat)
    tick_text = _read_text(paths.tick)
    hb_ts = _parse_mt5_ts(hb_text)
    tick_ts = _parse_mt5_ts(tick_text)
    now = time.time()
    bridge: dict[str, Any] = {
        "ipc_root": str(IPC_ROOT),
        "heartbeat_age_sec": round(now - hb_ts, 3) if hb_ts else None,
        "heartbeat_file_age_sec": round(_file_ts_age(paths.heartbeat), 3) if _file_ts_age(paths.heartbeat) is not None else None,
        "tick_age_sec": round(now - tick_ts, 3) if tick_ts else None,
        "tick_file_age_sec": round(_file_ts_age(paths.tick), 3) if _file_ts_age(paths.tick) is not None else None,
        "tick": tick_text,
    }

    t0 = time.perf_counter()
    try:
        raw_ping = ping(paths, timeout_sec=timeout)
        bridge["ping_ok"] = True
        bridge["ping_latency_ms"] = round((time.perf_counter() - t0) * 1000, 1)
        bridge["ping_response"] = raw_ping.splitlines()[-1] if raw_ping else ""
    except (CommandSlotBusy, ResponseTimeout, Exception) as exc:
        bridge["ping_ok"] = False
        bridge["ping_error"] = f"{type(exc).__name__}: {exc}"

    t0 = time.perf_counter()
    try:
        raw_status = status(paths, timeout_sec=timeout)
        bridge["status_ok"] = True
        bridge["status_latency_ms"] = round((time.perf_counter() - t0) * 1000, 1)
        bridge["status_response"] = raw_status.splitlines()[-1] if raw_status else ""
    except (CommandSlotBusy, ResponseTimeout, Exception) as exc:
        bridge["status_ok"] = False
        bridge["status_error"] = f"{type(exc).__name__}: {exc}"

    t0 = time.perf_counter()
    try:
        positions = get_positions(paths, timeout_sec=timeout)
        bridge["positions_ok"] = True
        bridge["positions_latency_ms"] = round((time.perf_counter() - t0) * 1000, 1)
        bridge["open_positions"] = len(positions)
        bridge["positions_by_symbol"] = dict(Counter(str(p.get("symbol", "unknown")) for p in positions))
        pnl = sum(float(p.get("profit") or p.get("pnl") or 0) for p in positions)
        bridge["floating_pnl"] = round(pnl, 2)
        bridge["positions_sample"] = positions[:5]
    except (CommandSlotBusy, ResponseTimeout, Exception) as exc:
        bridge["positions_ok"] = False
        bridge["positions_error"] = f"{type(exc).__name__}: {exc}"

    bridge["ok"] = bool(bridge.get("ping_ok") and bridge.get("status_ok") and bridge.get("positions_ok"))
    return bridge


def _execution_snapshot(since: float) -> dict[str, Any]:
    topics = [
        "muscle.order.intent",
        "immune.pass",
        "immune.block",
        "muscle.order.sent",
        "muscle.order.filled",
        "muscle.order.rejected",
        "muscle.order.timeout",
        "muscle.order.queued",
    ]
    counts = {topic: len(_topic_events(topic, since)) for topic in topics}
    retcodes = Counter()
    rejects = []
    for topic in ("muscle.order.rejected", "muscle.order.filled", "muscle.order.timeout"):
        for p in _payloads(topic, since):
            for key in ("retcode", "code", "mt5_retcode"):
                if key in p:
                    retcodes[str(p.get(key))] += 1
            if topic == "muscle.order.rejected":
                rejects.append(p)
    return {
        "window_counts": counts,
        "retcodes": dict(retcodes),
        "acceptance_rate": _safe_rate(counts.get("muscle.order.sent", 0), counts.get("muscle.order.intent", 0)),
        "fill_rate": _safe_rate(counts.get("muscle.order.filled", 0), counts.get("muscle.order.sent", 0)),
        "rejection_rate": _safe_rate(counts.get("muscle.order.rejected", 0), counts.get("muscle.order.sent", 0)),
        "timeout_rate": _safe_rate(counts.get("muscle.order.timeout", 0), counts.get("muscle.order.sent", 0)),
        "recent_rejections": rejects[-5:],
    }


def _safe_rate(num: int, den: int) -> float | None:
    if den <= 0:
        return None
    return round(num / den, 4)


def _llm_snapshot(since: float) -> dict[str, Any]:
    from cortex.llm_status import classify_llm_error, count_llm_errors_since

    status_events = _topic_events("cortex.llm.status", since)
    latest = status_events[-1].get("payload", {}) if status_events else {}
    fallback_errors = Counter()
    for p in _payloads("cortex.fallback", since):
        code = p.get("llm_error_code") or classify_llm_error(p.get("llm_error"))
        if code and code != "OK":
            fallback_errors[str(code)] += 1
    return {
        "status_events": len(status_events),
        "latest_ok": latest.get("ok"),
        "latest_error_code": latest.get("error_code"),
        "latest_operator_message": latest.get("operator_message"),
        "error_counts": count_llm_errors_since(status_events, since),
        "fallback_error_counts": dict(fallback_errors),
    }


def _decision_snapshot(since: float) -> dict[str, Any]:
    counts = {
        "market.signal": len(_topic_events("market.signal", since)),
        "cortex.decision": len(_topic_events("cortex.decision", since)),
        "cortex.brain.result": len(_topic_events("cortex.brain.result", since)),
        "cortex.fallback": len(_topic_events("cortex.fallback", since)),
        "cortex.llm_call": len(_topic_events("cortex.llm_call", since)),
        "cortex.decision_guard": len(_topic_events("cortex.decision_guard", since)),
    }
    actions = Counter()
    strategies = Counter()
    for p in _payloads("cortex.decision", since):
        actions[str(p.get("action") or p.get("decision") or "unknown").upper()] += 1
        sid = p.get("strategy_id") or p.get("strategy")
        if sid:
            strategies[str(sid)] += 1
    return {"window_counts": counts, "actions": dict(actions), "strategies": dict(strategies)}


def _risk_snapshot(since: float) -> dict[str, Any]:
    reasons = Counter()
    for p in _payloads("immune.block", since):
        reason = p.get("reason") or p.get("block_reason") or p.get("error") or "unknown"
        reasons[str(reason)] += 1
    anomalies = Counter(str(p.get("type") or p.get("reason") or "unknown") for p in _payloads("immune.anomaly", since))
    return {"immune_blocks": sum(reasons.values()), "immune_block_reasons": dict(reasons), "immune_anomalies": dict(anomalies)}


def _market_data_snapshot(since: float) -> dict[str, Any]:
    events = _topic_events("market.tick", since)
    by_symbol = Counter()
    last_by_symbol: dict[str, dict[str, Any]] = {}
    offsets: dict[str, list[float]] = {}
    now = time.time()
    for ev in events:
        p = ev.get("payload") if isinstance(ev.get("payload"), dict) else {}
        symbol = str(p.get("symbol") or "unknown")
        by_symbol[symbol] += 1
        last_by_symbol[symbol] = {"event_ts": ev.get("ts"), "payload": p}
        try:
            source_ts = float(p.get("time")) if p.get("time") is not None else None
            event_ts = float(ev.get("ts") or 0)
        except (TypeError, ValueError):
            source_ts = None
            event_ts = 0.0
        if source_ts and source_ts > 1_000_000_000 and event_ts > 1_000_000_000:
            offsets.setdefault(symbol, []).append(event_ts - source_ts)

    freshness = {}
    for symbol, item in last_by_symbol.items():
        p = item.get("payload", {})
        event_ts = float(item.get("event_ts") or 0)
        source_ts = None
        try:
            source_ts = float(p.get("time")) if p.get("time") is not None else None
        except (TypeError, ValueError):
            source_ts = None
        offset_samples = offsets.get(symbol, [])[-120:]
        clock_offset = statistics.median(offset_samples) if offset_samples else None
        adjusted_age = None
        if source_ts and clock_offset is not None:
            adjusted_age = max(0.0, now - (source_ts + clock_offset))
        freshness[symbol] = {
            # Primary freshness: when the OS ingested a new tick event.
            "event_age_sec": round(now - event_ts, 3) if event_ts else None,
            # Raw broker/server time can be offset from WSL/Windows local time.
            "source_tick_age_raw_sec": round(now - source_ts, 3) if source_ts else None,
            "source_clock_offset_sec": round(clock_offset, 3) if clock_offset is not None else None,
            "source_tick_age_adjusted_sec": round(adjusted_age, 3) if adjusted_age is not None else None,
            "fresh": (now - event_ts) < 30 if event_ts else False,
            "bid": p.get("bid"),
            "ask": p.get("ask"),
        }
    return {"market_tick_count": len(events), "ticks_by_symbol": dict(by_symbol), "freshness": freshness}


def _ops_snapshot(since: float) -> dict[str, Any]:
    health_alerts = _topic_events("ops.health_alert", since) + _topic_events("ops.health.alert", since)
    hooks = len(_topic_events("ops.hook.block", since))
    return {"health_alerts": len(health_alerts), "hook_blocks": hooks, "recent_health_alerts": [e.get("payload", {}) for e in health_alerts[-3:]]}


def build_report(window_minutes: float, timeout: float, include_bridge: bool = True) -> dict[str, Any]:
    since = time.time() - window_minutes * 60.0
    service = _service_status()
    service_env = service.get("env", {}) if isinstance(service.get("env"), dict) else {}
    report = {
        "ts": time.time(),
        "window_minutes": window_minutes,
        "mode": service_env.get("TRADING_OS_MODE") or os.getenv("TRADING_OS_MODE", "SIMULATION"),
        "multisymbol": service_env.get("TRADING_OS_MULTISYMBOL") or os.getenv("TRADING_OS_MULTISYMBOL", "auto"),
        "human_approved": service_env.get("TRADING_OS_HUMAN_APPROVED") or os.getenv("TRADING_OS_HUMAN_APPROVED", "0"),
        "service": service,
        "execution": _execution_snapshot(since),
        "data": _market_data_snapshot(since),
        "decisions": _decision_snapshot(since),
        "llm": _llm_snapshot(since),
        "risk": _risk_snapshot(since),
        "ops": _ops_snapshot(since),
    }
    if include_bridge:
        report["bridge"] = _bridge_snapshot(timeout)
    return report


def append_snapshot(report: dict[str, Any], path: Path | None = None) -> Path:
    """Persist one KPI snapshot as JSONL for later charting/backtests."""
    out = path or (ROOT / "consciousness" / "kpi" / "snapshots.jsonl")
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("a", encoding="utf-8") as f:
        f.write(json.dumps(report, sort_keys=True) + "\n")
    return out


def _print_text(report: dict[str, Any]) -> None:
    bridge = report.get("bridge", {})
    execution = report["execution"]
    decisions = report["decisions"]
    risk = report["risk"]
    print(f"Trading OS KPI snapshot | mode={report['mode']} multisymbol={report['multisymbol']} window={report['window_minutes']}m")
    print(f"Service: {report['service'].get('active')} ({report['service'].get('service')})")
    if bridge:
        print(
            "Bridge: "
            f"ok={bridge.get('ok')} hb_age={bridge.get('heartbeat_age_sec')}s "
            f"tick_age={bridge.get('tick_age_sec') or bridge.get('tick_file_age_sec')}s ping={bridge.get('ping_latency_ms')}ms "
            f"positions={bridge.get('open_positions')} floating_pnl={bridge.get('floating_pnl')}"
        )
        if bridge.get("positions_by_symbol"):
            print(f"Positions by symbol: {bridge.get('positions_by_symbol')}")
    print(f"Market data: ticks={report['data']['market_tick_count']} by_symbol={report['data']['ticks_by_symbol']} freshness={report['data']['freshness']}")
    llm = report.get("llm", {})
    print(
        "LLM: "
        f"latest_ok={llm.get('latest_ok')} code={llm.get('latest_error_code')} "
        f"message={llm.get('latest_operator_message')} errors={llm.get('error_counts')}"
    )
    print(f"Execution counts: {execution['window_counts']}")
    print(
        "Execution rates: "
        f"accept={execution['acceptance_rate']} fill={execution['fill_rate']} "
        f"reject={execution['rejection_rate']} timeout={execution['timeout_rate']} retcodes={execution['retcodes']}"
    )
    print(f"Decisions: counts={decisions['window_counts']} actions={decisions['actions']} strategies={decisions['strategies']}")
    print(f"Risk: immune_blocks={risk['immune_blocks']} reasons={risk['immune_block_reasons']} anomalies={risk['immune_anomalies']}")
    print(f"Ops: health_alerts={report['ops']['health_alerts']} hook_blocks={report['ops']['hook_blocks']}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--window-minutes", type=float, default=60.0, help="Event lookback window")
    ap.add_argument("--timeout", type=float, default=8.0, help="MT5 no-trade command timeout")
    ap.add_argument("--json", action="store_true", help="Emit JSON instead of text")
    ap.add_argument("--no-bridge", action="store_true", help="Skip live MT5 no-trade roundtrips")
    ap.add_argument("--log", action="store_true", help="Append snapshot to consciousness/kpi/snapshots.jsonl")
    ap.add_argument("--log-path", type=Path, default=None, help="Custom JSONL snapshot path")
    args = ap.parse_args()
    report = build_report(args.window_minutes, args.timeout, include_bridge=not args.no_bridge)
    logged_to = append_snapshot(report, args.log_path) if args.log or args.log_path else None
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        _print_text(report)
        if logged_to:
            print(f"Logged snapshot: {logged_to}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
