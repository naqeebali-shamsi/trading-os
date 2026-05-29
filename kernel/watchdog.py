#!/usr/bin/env python3
"""
kernel/watchdog.py — Trading OS health monitor
----------------------------------------------
Checks runtime liveness signals and publishes actionable ops alerts.
Designed to be importable/testable and safe to run as a lightweight daemon.
"""
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
IPC_DIR = ROOT / "ipc"
BUS_FILE = ROOT / "nervous" / "bus.jsonl"
HEALTH_FILE = ROOT / "kernel" / "health.json"
LOG_FILE = ROOT / "logs" / "health_monitor.log"
LAST_SEQ_FILE = ROOT / ".health_last_seq"
LAST_TS_FILE = ROOT / ".health_last_ts"

sys.path.insert(0, str(ROOT / "nervous"))
from bus import publish, tail  # noqa: E402

HEARTBEAT_STALE_SEC = float(os.getenv("TRADING_OS_HEARTBEAT_STALE_SEC", "90"))
BUS_STALE_SEC = float(os.getenv("TRADING_OS_BUS_STALE_SEC", "120"))
CHECK_INTERVAL_SEC = float(os.getenv("TRADING_OS_HEALTH_INTERVAL_SEC", "15"))


def _read_text(path):
    if not path.exists():
        return None
    try:
        raw = path.read_bytes()
        if raw.startswith(b"\xff\xfe"):
            return raw.decode("utf-16-le", errors="replace").lstrip("\ufeff").strip()
        if raw.startswith(b"\xfe\xff"):
            return raw.decode("utf-16-be", errors="replace").lstrip("\ufeff").strip()
        return raw.decode("utf-8", errors="replace").strip()
    except Exception as exc:
        return f"__read_error__:{exc}"


def _heartbeat_status(now=None):
    now = now or time.time()
    charts = []
    alerts = []
    if not IPC_DIR.exists():
        alerts.append({"kind": "ipc_missing", "severity": "warning", "message": f"IPC directory missing: {IPC_DIR}"})
        return charts, alerts

    chart_dirs = sorted(p for p in IPC_DIR.iterdir() if p.is_dir() and p.name.startswith("chart_"))
    if not chart_dirs:
        alerts.append({"kind": "charts_absent", "severity": "warning", "message": "No chart_* IPC directories found"})
        return charts, alerts

    for chart_dir in chart_dirs:
        hb_file = chart_dir / "heartbeat.txt"
        text = _read_text(hb_file)
        status = {"chart": chart_dir.name, "alive": False}
        if not text:
            status["error"] = "missing_heartbeat"
            alerts.append({"kind": "heartbeat_missing", "severity": "critical", "chart": chart_dir.name, "message": f"{chart_dir.name} heartbeat missing"})
        else:
            try:
                stamp = float(text.split("|")[0] if "|" in text else text.split(",")[0])
                age = now - stamp
                status.update({"age_sec": round(age, 1), "alive": age <= HEARTBEAT_STALE_SEC})
                if age > HEARTBEAT_STALE_SEC:
                    alerts.append({"kind": "heartbeat_stale", "severity": "critical", "chart": chart_dir.name, "age_sec": round(age, 1), "message": f"{chart_dir.name} heartbeat stale: {age:.1f}s"})
            except Exception:
                status["error"] = "parse_error"
                alerts.append({"kind": "heartbeat_parse_error", "severity": "warning", "chart": chart_dir.name, "message": f"{chart_dir.name} heartbeat could not be parsed"})
        charts.append(status)
    return charts, alerts


def _bus_status(now=None):
    now = now or time.time()
    events = tail(1)
    last_seq = events[-1].get("seq", 0) if events else 0
    last_event_ts = events[-1].get("ts", 0) if events else 0
    prev_seq = int(LAST_SEQ_FILE.read_text().strip()) if LAST_SEQ_FILE.exists() and LAST_SEQ_FILE.read_text().strip().isdigit() else 0
    prev_ts = float(LAST_TS_FILE.read_text().strip()) if LAST_TS_FILE.exists() else now

    alerts = []
    if not BUS_FILE.exists() or not events:
        alerts.append({"kind": "bus_empty", "severity": "warning", "message": "Nervous bus has no events"})
    elif last_seq <= prev_seq and now - prev_ts > BUS_STALE_SEC:
        alerts.append({"kind": "bus_stale", "severity": "warning", "age_sec": round(now - prev_ts, 1), "last_seq": last_seq, "message": f"Nervous bus has not advanced for {now - prev_ts:.1f}s"})

    if last_seq > prev_seq:
        LAST_SEQ_FILE.write_text(str(last_seq))
        LAST_TS_FILE.write_text(str(now))
    elif not LAST_TS_FILE.exists():
        LAST_TS_FILE.write_text(str(now))
    return {"last_seq": last_seq, "last_event_ts": last_event_ts, "previous_seq": prev_seq}, alerts


def check_once(publish_alerts=True, now=None):
    now = now or time.time()
    charts, heartbeat_alerts = _heartbeat_status(now)
    bus, bus_alerts = _bus_status(now)
    alerts = heartbeat_alerts + bus_alerts
    report = {"ts": now, "charts": charts, "bus": bus, "alerts": alerts, "ok": not any(a.get("severity") == "critical" for a in alerts)}

    HEALTH_FILE.parent.mkdir(parents=True, exist_ok=True)
    HEALTH_FILE.write_text(json.dumps(report, indent=2))
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with LOG_FILE.open("a") as f:
        f.write(json.dumps(report) + "\n")

    if publish_alerts:
        for alert in alerts:
            publish("ops.health.alert", alert)
    return report


def run():
    print(f"[health] Monitoring every {CHECK_INTERVAL_SEC}s")
    while True:
        report = check_once(publish_alerts=True)
        print(f"[health] ok={report['ok']} alerts={len(report['alerts'])}", flush=True)
        time.sleep(CHECK_INTERVAL_SEC)


if __name__ == "__main__":
    run()
