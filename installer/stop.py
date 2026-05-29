#!/usr/bin/env python3
"""Stop a Trading OS supervisor started by the desktop launcher."""
from __future__ import annotations

import os
import signal
import sys
from pathlib import Path

CONFIG_DIR_NAME = "TradingOS"
PID_FILE_NAME = "supervisor.pid"


def _install_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def _pid_file(install_root: Path) -> Path:
    program_data = os.environ.get("ProgramData")
    if program_data:
        return Path(program_data) / CONFIG_DIR_NAME / PID_FILE_NAME
    return install_root / PID_FILE_NAME


def _pid_running(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def main() -> int:
    install_root = Path(os.environ.get("TRADING_OS_ROOT") or _install_root()).resolve()
    path = _pid_file(install_root)
    if not path.exists():
        print("Trading OS is not running.")
        return 0
    try:
        pid = int(path.read_text(encoding="utf-8").strip())
    except ValueError:
        print("Invalid supervisor PID file.")
        path.unlink(missing_ok=True)
        return 1
    if not _pid_running(pid):
        path.unlink(missing_ok=True)
        print("Trading OS is not running.")
        return 0
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError as exc:
        print(f"Failed to stop supervisor PID {pid}: {exc}")
        return 1
    path.unlink(missing_ok=True)
    print(f"Stopped Trading OS supervisor (PID {pid}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
