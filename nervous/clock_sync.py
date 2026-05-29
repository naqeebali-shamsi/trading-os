#!/usr/bin/env python3
"""
nervous/clock_sync.py -- WSL Clock Keeper
------------------------------------------
WSL2 clock freezes when Windows sleeps. This daemon detects drift
by comparing local epoch() against MT5 heartbeat timestamp from
the EA. If WSL falls behind by >30s, it forces a sync via sudo.

Run by supervisor as layer: nervous.clock_sync
"""
import os, sys, subprocess, time, json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "nervous"))
from ipc_path import get_ipc_dir  # noqa
from ipc_text import read_ipc_text  # noqa

from bus import publish  # noqa

IPC_DIR = get_ipc_dir()
HB_FILE = IPC_DIR / "heartbeat.txt"
SYNC_INTERVAL_SEC = 10.0
DRIFT_THRESHOLD = 30.0


def read_hb_ts():
    text = read_ipc_text(HB_FILE)
    if not text:
        return None
    try:
        return float(text.split("|")[0] if "|" in text else text.split(",")[0])
    except ValueError:
        return None


def wsl_sync(epoch_ts: int):
    """Sync WSL clock to given epoch timestamp via sudo date."""
    try:
        result = subprocess.run(
            ["sudo", "date", "-u", "-s", f"@{epoch_ts}"],
            capture_output=True, text=True, check=True,
        )
        return True
    except subprocess.CalledProcessError:
        return False


def run():
    print("[clock_sync] WSL clock keeper started")
    while True:
        hb_ts = read_hb_ts()
        if hb_ts is not None:
            now = time.time()
            lag = abs(now - hb_ts)
            if lag > DRIFT_THRESHOLD:
                epoch_target = int(round(hb_ts))
                ok = wsl_sync(epoch_target)
                publish(
                    "system.clock_sync",
                    {
                        "old_lag_sec": lag,
                        "synced_to_epoch": epoch_target,
                        "success": ok,
                    },
                )
                if ok:
                    print(f"[clock_sync] Synced WSL clock to HB epoch ({lag:.0f}s drift)")
                else:
                    # Fall back to direct sudo-less if no tty auth
                    pass
        time.sleep(SYNC_INTERVAL_SEC)


if __name__ == "__main__":
    run()
