#!/usr/bin/env python3
"""
scripts/health_monitor.py — Autonomous Trading Health Watchdog
---------------------------------------------------------------
Checks: supervisor alive, EA heartbeat, clock drift, bus growth, circuit breaker.
Writes alerts to bus, exits with non-zero on hard failures.
Run via cron every 1-2 minutes.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "nervous"))

from paths import ipc_dir, logs_dir, nervous_dir, repo_root  # noqa: E402
from ops.process_utils import find_supervisor_pids  # noqa: E402
from bus import publish  # noqa: E402

IPC_DIR = ipc_dir()
SEQ_FILE = nervous_dir() / ".seq"
LOG_FILE = logs_dir() / "health_monitor.log"
MARKER_SEQ = repo_root() / ".health_last_seq"
MARKER_TS = repo_root() / ".health_last_ts"

MAX_HEARTBEAT_AGE_SEC = 90.0
MAX_CLOCK_DRIFT_SEC = 60.0
MIN_BUS_GROWTH_PER_MIN = 2
MAX_DAILY_LOSS_USD = -1000.0
MAX_TRADES_PER_HOUR = 20

alerts: list[str] = []
status = {"ok": True, "checks": {}}


def now() -> float:
    return time.time()


def log(msg: str) -> None:
    ts = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())
    line = f"[{ts}] {msg}"
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    except OSError:
        pass
    print(line)


def read_heartbeat() -> float | None:
    hb = IPC_DIR / "heartbeat.txt"
    if not hb.exists():
        return None
    try:
        raw = hb.read_bytes()
        if raw.startswith(b"\xff\xfe"):
            text = raw.decode("utf-16-le", errors="replace").lstrip("\ufeff").strip()
        elif raw.startswith(b"\xfe\xff"):
            text = raw.decode("utf-16-be", errors="replace").lstrip("\ufeff").strip()
        else:
            text = raw.decode("utf-8", errors="replace").strip()
        parts = text.split("|") if "|" in text else text.split(",")
        return float(parts[0])
    except (OSError, ValueError, IndexError):
        return None


def check_supervisor() -> bool:
    pids = find_supervisor_pids()
    ok = len(pids) > 0
    status["checks"]["supervisor"] = {"pid_count": len(pids), "pids": pids, "ok": ok}
    if not ok:
        alerts.append("SUPERVISOR_DOWN: no supervisor.py process found")
        status["ok"] = False
    return ok


def check_heartbeat() -> None:
    ts = read_heartbeat()
    if ts is None:
        alerts.append("EA_DISCONNECTED: heartbeat.txt missing")
        status["checks"]["heartbeat"] = {"ok": False, "age": None}
        status["ok"] = False
        return
    age = abs(now() - ts)
    ok = age < MAX_HEARTBEAT_AGE_SEC
    status["checks"]["heartbeat"] = {"ts": ts, "age_sec": round(age, 1), "ok": ok}
    if not ok:
        alerts.append(f"EA_STALE: heartbeat age={age:.0f}s (max {MAX_HEARTBEAT_AGE_SEC})")
        status["ok"] = False


def check_clock() -> None:
    ts = read_heartbeat()
    if ts is None:
        return
    drift = abs(now() - ts)
    ok = drift <= MAX_CLOCK_DRIFT_SEC * 2
    status["checks"]["clock"] = {"drift_sec": round(drift, 1), "ok": ok}
    if not ok:
        alerts.append(f"CLOCK_DRIFT: {drift:.0f}s behind MT5 (host clock may be frozen)")
        status["ok"] = False


def check_bus_growth() -> None:
    if not SEQ_FILE.exists():
        return
    try:
        seq = int(SEQ_FILE.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return

    if MARKER_SEQ.exists() and MARKER_TS.exists():
        try:
            last_seq = int(MARKER_SEQ.read_text(encoding="utf-8").strip())
            last_ts = float(MARKER_TS.read_text(encoding="utf-8").strip())
            elapsed = now() - last_ts
            if elapsed > 90:
                growth = seq - last_seq
                rate = growth / (elapsed / 60.0)
                ok = rate >= MIN_BUS_GROWTH_PER_MIN
                status["checks"]["bus_growth"] = {"seq": seq, "rate_per_min": round(rate, 1), "ok": ok}
                if not ok:
                    alerts.append(f"BUS_FREEZE: bus rate {rate:.1f}/min (min {MIN_BUS_GROWTH_PER_MIN})")
                    status["ok"] = False
        except (OSError, ValueError):
            pass

    MARKER_SEQ.write_text(str(seq), encoding="utf-8")
    MARKER_TS.write_text(str(now()), encoding="utf-8")


def check_circuit_breaker() -> None:
    topic_file = nervous_dir() / "topics" / "muscle.order.filled.jsonl"
    if not topic_file.exists():
        status["checks"]["circuit"] = {"ok": True, "daily_pnl": 0, "trades_last_hour": 0}
        return

    daily_pnl = 0.0
    trades_last_hour = 0
    hour_ago = now() - 3600
    today_start = now() - (now() % 86400)
    try:
        with open(topic_file, encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                ts = ev.get("ts", 0)
                if ts > today_start:
                    payload = ev.get("payload", {})
                    daily_pnl += payload.get("pnl", 0) or 0
                    if ts > hour_ago:
                        trades_last_hour += 1
    except OSError:
        pass

    daily_ok = daily_pnl > MAX_DAILY_LOSS_USD or abs(daily_pnl - MAX_DAILY_LOSS_USD) < 0.01
    rate_ok = trades_last_hour < MAX_TRADES_PER_HOUR
    cb_ok = daily_ok and rate_ok
    status["checks"]["circuit"] = {
        "ok": cb_ok,
        "daily_pnl": round(daily_pnl, 2),
        "trades_last_hour": trades_last_hour,
    }
    if not daily_ok:
        alerts.append(f"CIRCUIT_BREAKER: daily_loss={daily_pnl:.2f} USD (limit {MAX_DAILY_LOSS_USD})")
        status["ok"] = False
    if not rate_ok:
        alerts.append(f"CIRCUIT_BREAKER: trades_per_hour={trades_last_hour} (limit {MAX_TRADES_PER_HOUR})")
        status["ok"] = False


def write_alert_event() -> None:
    if not alerts:
        return
    payload = {
        "severity": "warning" if status["ok"] else "critical",
        "alerts": alerts,
        "checks": status["checks"],
    }
    publish("ops.health_alert", payload)
    for alert in alerts:
        publish("ops.health.alert", {"source": "health_monitor", **alert, "batch": payload})


def main() -> None:
    check_supervisor()
    check_heartbeat()
    check_clock()
    check_bus_growth()
    check_circuit_breaker()
    for alert in alerts:
        log(alert)
    write_alert_event()
    if not status["ok"]:
        log("HEALTH_CHECK_FAIL")
        sys.exit(1)
    log("HEALTH_CHECK_PASS")
    sys.exit(0)


if __name__ == "__main__":
    main()
