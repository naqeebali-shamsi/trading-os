#!/usr/bin/env python3
"""
bridge_daemon.py — Persistent bridge health monitor + MT5 watchdog.
Runs in background. Checks MT5 process and EA heartbeat every 60s.
If MT5 dead: attempts restart. If dead again: escalates to orchestrator.
If EA heartbeat stale: logs warning.

Usage:
    python bridge_daemon.py start      # fork to background
    python bridge_daemon.py status     # check current state
    python bridge_daemon.py stop       # kill daemon
"""

import os
import sys
import time
import json
import signal
import logging
import subprocess
from pathlib import Path
from datetime import datetime, timezone

PIDFILE = Path("/tmp/mt5_bridge_daemon.pid")
LOGFILE = Path("/mnt/e/GROWTH/trading-os/logs/bridge_daemon.log")
CONFIG_DIR = Path("/mnt/e/GROWTH/trading-os/config")
WINE_PREFIX = Path.home() / ".mt5"
MT5_EXE = WINE_PREFIX / "drive_c" / "Program Files" / "MetaTrader 5" / "terminal64.exe"
DISPLAY = ":99"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)14s | %(message)s",
    handlers=[
        logging.FileHandler(LOGFILE),
        logging.StreamHandler(sys.stderr),
    ],
)
logger = logging.getLogger("bridge_daemon")


def is_mt5_running() -> bool:
    try:
        return bool(subprocess.check_output(["pgrep", "-f", "terminal64.exe"], text=True).strip())
    except subprocess.CalledProcessError:
        return False


def is_xvfb_running() -> bool:
    try:
        return bool(subprocess.check_output(["pgrep", "-f", f"Xvfb {DISPLAY}"], text=True).strip())
    except subprocess.CalledProcessError:
        return False


def start_xvfb():
    if not is_xvfb_running():
        logger.info("Starting Xvfb on %s", DISPLAY)
        subprocess.Popen(
            ["Xvfb", DISPLAY, "-screen", "0", "1024x768x24", "-ac", "+render", "-noreset"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        time.sleep(2)


def start_mt5():
    if not is_mt5_running():
        logger.info("Starting MT5 terminal...")
        env = os.environ.copy()
        env["DISPLAY"] = DISPLAY
        env["WINEPREFIX"] = str(WINE_PREFIX)
        subprocess.Popen(
            ["wine", str(MT5_EXE)],
            env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        time.sleep(15)  # Give MT5 time to initialize


def ipc_heartbeat_ok() -> bool:
    ipc_dir = WINE_PREFIX / "drive_c" / "Program Files" / "MetaTrader 5" / "MQL5" / "Files" / "trading-os"
    hb = ipc_dir / "heartbeat.txt"
    if not hb.exists():
        return False
    try:
        data = hb.read_text().strip().splitlines()
        if not data:
            return False
        line = data[-1]
        if "|" in line:
            ts_str = line.split("|")[0]
            hb_time = datetime.strptime(ts_str, "%Y.%m.%d %H:%M:%S")
            return (datetime.now() - hb_time).total_seconds() < 60
    except Exception:
        return False


def load_state() -> str:
    try:
        return (CONFIG_DIR / "swarm_state").read_text().strip()
    except Exception:
        return "ACTIVE"


def daemon_loop():
    logger.info("Bridge daemon loop started. PID=%s", os.getpid())
    fail_count = 0
    while True:
        state = load_state()
        if state == "EMERGENCY_HALT":
            logger.critical("EMERGENCY_HALT detected. Exiting daemon.")
            break
        if state == "PAUSED":
            logger.info("Swarm PAUSED. Sleeping 60s.")
            time.sleep(60)
            continue

        # Manage Xvfb
        if not is_xvfb_running():
            start_xvfb()

        # Manage MT5
        if not is_mt5_running():
            logger.warning("MT5 dead. Attempting restart (fail_count=%s)", fail_count)
            start_mt5()
            if not is_mt5_running():
                fail_count += 1
                if fail_count >= 3:
                    logger.critical("MT5 restart failed 3x. Escalating to orchestrator.")
                    # Write to orchestrator INBOX
                    inbox = Path("/mnt/e/GROWTH/trading-os/queue/orchestrator-trader/INBOX.md")
                    with open(inbox, "a") as f:
                        f.write(f"\n## ALERT | {datetime.now(timezone.utc).isoformat()}\n")
                        f.write("**Alert**: MT5_RESTART_FAILED\n")
                        f.write("**Details**: terminal64.exe could not start after 3 attempts\n")
                    fail_count = 0
            else:
                logger.info("MT5 restarted successfully.")
                fail_count = 0

        # Check EA heartbeat
        if is_mt5_running() and not ipc_heartbeat_ok():
            logger.warning("MT5 alive but EA heartbeat missing. EA may need re-attachment.")

        time.sleep(60)


def do_start():
    if PIDFILE.exists():
        try:
            old_pid = int(PIDFILE.read_text())
            os.kill(old_pid, 0)
            print(f"Daemon already running (PID {old_pid})")
            return
        except (OSError, ValueError):
            PIDFILE.unlink(missing_ok=True)

    pid = os.fork()
    if pid > 0:
        PIDFILE.write_text(str(pid))
        print(f"Daemon started (PID {pid})")
        return

    # Child process
    os.setsid()
    try:
        daemon_loop()
    finally:
        PIDFILE.unlink(missing_ok=True)


def do_stop():
    if not PIDFILE.exists():
        print("Daemon not running")
        return
    try:
        pid = int(PIDFILE.read_text())
        os.kill(pid, signal.SIGTERM)
        PIDFILE.unlink(missing_ok=True)
        print(f"Daemon stopped (PID {pid})")
    except (OSError, ValueError) as e:
        print(f"Failed to stop daemon: {e}")
        PIDFILE.unlink(missing_ok=True)


def do_status():
    print(f"MT5 process: {'RUNNING' if is_mt5_running() else 'NOT RUNNING'}")
    print(f"Xvfb {DISPLAY}: {'RUNNING' if is_xvfb_running() else 'NOT RUNNING'}")
    if PIDFILE.exists():
        try:
            pid = int(PIDFILE.read_text())
            os.kill(pid, 0)
            print(f"Daemon: RUNNING (PID {pid})")
        except OSError:
            print("Daemon: STALE PIDFILE")
    else:
        print("Daemon: NOT RUNNING")
    print(f"IPC heartbeat: {'OK' if ipc_heartbeat_ok() else 'MISSING/STALE'}")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "start":
        do_start()
    elif cmd == "stop":
        do_stop()
    else:
        do_status()
