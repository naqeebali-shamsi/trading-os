"""Cross-platform process discovery and graceful shutdown helpers."""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from typing import List

SUPERVISOR_MARKER = "kernel/supervisor.py"


def find_supervisor_pids(marker: str = SUPERVISOR_MARKER) -> List[int]:
    """Return PIDs for running supervisor processes, if any."""
    if sys.platform == "win32":
        return _find_pids_windows(marker)
    return _find_pids_unix(marker)


def _find_pids_unix(marker: str) -> List[int]:
    try:
        out = subprocess.check_output(
            ["pgrep", "-f", marker],
            text=True,
            stderr=subprocess.DEVNULL,
        )
        return [int(pid) for pid in out.split() if pid.strip().isdigit()]
    except (FileNotFoundError, subprocess.CalledProcessError):
        return _find_pids_powershell(marker) if sys.platform == "win32" else []


def _find_pids_windows(marker: str) -> List[int]:
    pids = _find_pids_powershell(marker)
    if pids:
        return pids
    return _find_pids_wmic(marker)


def _find_pids_powershell(marker: str) -> List[int]:
    escaped = marker.replace("'", "''")
    script = (
        "Get-CimInstance Win32_Process | "
        f"Where-Object {{ $_.CommandLine -like '*{escaped}*' }} | "
        "Select-Object -ExpandProperty ProcessId"
    )
    try:
        out = subprocess.check_output(
            ["powershell", "-NoProfile", "-Command", script],
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=15,
        )
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return []
    return [int(line.strip()) for line in out.splitlines() if line.strip().isdigit()]


def _find_pids_wmic(marker: str) -> List[int]:
    try:
        out = subprocess.check_output(
            ["wmic", "process", "get", "ProcessId,CommandLine", "/FORMAT:CSV"],
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=20,
        )
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return []
    pids: List[int] = []
    for line in out.splitlines():
        if marker.replace("\\", "/") not in line.replace("\\", "/"):
            continue
        parts = [part.strip() for part in line.split(",") if part.strip()]
        for part in reversed(parts):
            if part.isdigit():
                pids.append(int(part))
                break
    return pids


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        try:
            out = subprocess.check_output(
                ["tasklist", "/FI", f"PID eq {pid}"],
                text=True,
                stderr=subprocess.DEVNULL,
                timeout=10,
            )
            return str(pid) in out
        except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
            return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def terminate_pid(pid: int, sig: signal.Signals | int = signal.SIGTERM, *, force: bool = False) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        args = ["taskkill", "/PID", str(pid), "/T"]
        if force:
            args.append("/F")
        try:
            proc = subprocess.run(args, capture_output=True, text=True, timeout=10)
            return proc.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False
    try:
        os.kill(pid, sig)
        return True
    except OSError:
        return False


def stop_supervisor_processes(*, grace_sec: float = 2.0, marker: str = SUPERVISOR_MARKER) -> List[int]:
    """Gracefully stop supervisor processes; force-kill survivors after grace."""
    pids = find_supervisor_pids(marker)
    if not pids:
        return []

    for pid in pids:
        terminate_pid(pid, signal.SIGTERM)

    if grace_sec > 0:
        time.sleep(grace_sec)

    for pid in list(pids):
        if pid_alive(pid):
            terminate_pid(pid, force=True)

    return [pid for pid in pids if not pid_alive(pid)]
