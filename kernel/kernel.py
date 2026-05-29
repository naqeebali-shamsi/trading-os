#!/usr/bin/env python3
"""
kernel/kernel.py -- Brainstem
-----------------------------
Process manager, health registry, subsystem lifecycle.
Each subsystem registers itself. Watchdog checks heartbeats.
Dead subsystems are resurrected. Fatal state is published as event.
"""
import json, os, time, sys, subprocess, signal
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).resolve().parent.parent
KERNEL_DIR = ROOT / "kernel"
PROCS_FILE = KERNEL_DIR / "procs.json"
HEALTH_FILE = KERNEL_DIR / "health.json"

sys.path.insert(0, str(ROOT / "nervous"))
from bus import publish, subscribe  # noqa

SUBSYSTEMS = [
    "sensory",
    "muscle",
    "immune",
    "memory",
    "cortex",
    "swarm",
]

HEARTBEAT_TIMEOUT = 45.0  # seconds before considered dead


def init_registry():
    if not PROCS_FILE.exists():
        PROCS_FILE.write_text(json.dumps({}, indent=2))
    if not HEALTH_FILE.exists():
        HEALTH_FILE.write_text(json.dumps({}, indent=2))


def register(subsystem, pid, cmd, started_by="kernel"):
    init_registry()
    procs = json.loads(PROCS_FILE.read_text())
    procs[subsystem] = {
        "pid": pid,
        "cmd": cmd,
        "started_at": time.time(),
        "started_by": started_by,
    }
    PROCS_FILE.write_text(json.dumps(procs, indent=2))
    health = json.loads(HEALTH_FILE.read_text())
    health[subsystem] = {
        "status": "unknown",
        "last_seen": time.time(),
        "restarts": 0,
    }
    HEALTH_FILE.write_text(json.dumps(health, indent=2))
    publish("kernel.proc.register", {"subsystem": subsystem, "pid": pid})


def heartbeat(subsystem):
    init_registry()
    health = json.loads(HEALTH_FILE.read_text())
    old = health.get(subsystem, {})
    old["status"] = "alive"
    old["last_seen"] = time.time()
    health[subsystem] = old
    HEALTH_FILE.write_text(json.dumps(health, indent=2))


def get_health():
    init_registry()
    return json.loads(HEALTH_FILE.read_text())


def _is_alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def check_and_resurrect():
    init_registry()
    procs = json.loads(PROCS_FILE.read_text())
    health = json.loads(HEALTH_FILE.read_text())
    now = time.time()
    actions = []
    for sub in SUBSYSTEMS:
        h = health.get(sub, {})
        last_seen = h.get("last_seen", 0)
        status = h.get("status", "unknown")
        pid_info = procs.get(sub, {})
        pid = pid_info.get("pid")
        is_dead = False

        # Check heartbeats
        if now - last_seen > HEARTBEAT_TIMEOUT:
            is_dead = True

        # Check OS-level life
        if pid and not _is_alive(pid):
            is_dead = True

        if is_dead and status != "dead":
            h["status"] = "dead"
            h["restarts"] = h.get("restarts", 0) + 1
            health[sub] = h
            HEALTH_FILE.write_text(json.dumps(health, indent=2))
            publish("kernel.alert.dead", {"subsystem": sub, "pid": pid})
            # Resurrect if under max restarts
            if h["restarts"] < 10:
                _resurrect(sub)
            else:
                publish("kernel.alert.fatal", {"subsystem": sub, "restarts": h["restarts"]})
    return actions


def _resurrect(sub):
    script_map = {
        "sensory": ROOT / "sensory" / "main.py",
        "muscle": ROOT / "muscle" / "main.py",
        "immune": ROOT / "immune" / "main.py",
        "memory": ROOT / "memory" / "main.py",
        "cortex": ROOT / "cortex" / "main.py",
        "swarm": ROOT / "swarm" / "main.py",
    }
    path = script_map.get(sub)
    if path and path.exists():
        proc = subprocess.Popen(
            [sys.executable, str(path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(ROOT),
        )
        register(sub, proc.pid, str(path), started_by="kernel.resurrect")
        publish("kernel.proc.resurrected", {"subsystem": sub, "new_pid": proc.pid})


if __name__ == "__main__":
    import time as t
    init_registry()
    for s in SUBSYSTEMS:
        if s not in json.loads(PROCS_FILE.read_text()):
            _resurrect(s)
    while True:
        check_and_resurrect()
        publish("kernel.pulse", {"subsystems": SUBSYSTEMS})
        t.sleep(15)
